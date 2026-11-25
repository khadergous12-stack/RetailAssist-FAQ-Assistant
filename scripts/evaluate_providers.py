from __future__ import annotations

import csv
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import load_settings
from providers.openai.generator import OpenAIGenerator
from providers.snowflake.connection import create_snowflake_session
from providers.snowflake.generator import SnowflakeGenerator
from providers.snowflake.retriever import SnowflakeRetriever
from rag.prompts import SYSTEM_PROMPT, build_grounded_prompt
from rag.service import RAGService


logger = logging.getLogger(__name__)
QUESTIONS_FILE = PROJECT_ROOT / "data" / "evaluation_questions.csv"
OUTPUT_FILE = PROJECT_ROOT / "data" / "provider_evaluation_results.csv"
REQUIRED_COLUMNS = {
    "id",
    "question",
    "expected_source",
    "answerable",
}


def format_evidence(chunks) -> str:
    formatted = []
    for index, chunk in enumerate(chunks, start=1):
        page = str(chunk.page_number) if chunk.page_number is not None else "N/A"
        formatted.append(
            f"""Evidence {index}
Document: {chunk.document_name}
Document ID: {chunk.document_id}
Category: {chunk.category or "Uncategorized"}
Chunk ID: {chunk.chunk_id}
Chunk Index: {chunk.chunk_index}
Page Number: {page}
Section: {chunk.section_heading or "N/A"}
Text:
{chunk.chunk_text}""".strip()
        )
    return "\n\n".join(formatted)


def build_prompt(question: str, chunks) -> str:
    evidence_text = format_evidence(chunks)
    return f"{SYSTEM_PROMPT}\n\n{build_grounded_prompt(question, evidence_text)}"


def get_sources(chunks) -> str:
    return "; ".join(
        dict.fromkeys(
            str(chunk.document_name).strip()
            for chunk in chunks
            if str(chunk.document_name).strip()
        )
    )


def run_provider(generator, prompt: str):
    started = time.perf_counter()
    try:
        answer = generator.generate(prompt)
        error = ""
    except Exception as exc:
        answer = ""
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started
    return answer, error, elapsed


def load_questions() -> list[dict[str, str]]:
    if not QUESTIONS_FILE.exists():
        raise FileNotFoundError(
            f"Evaluation questions file not found: {QUESTIONS_FILE}"
        )

    with QUESTIONS_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        raise ValueError(f"Evaluation questions file is empty: {QUESTIONS_FILE}")

    missing = REQUIRED_COLUMNS - set(rows[0].keys())
    if missing:
        raise ValueError(
            "Evaluation CSV is missing required columns: " + ", ".join(sorted(missing))
        )

    return rows


def main() -> None:
    settings = load_settings()

    # Groq uses the same OpenAI-compatible wrapper, but authentication/model
    # are now supplied through GROQ_* configuration.
    import os

    groq_api_key = os.getenv("GROQ_API_KEY") or getattr(
        settings,
        "groq_api_key",
        None,
    )
    groq_model = os.getenv("GROQ_MODEL") or getattr(
        settings,
        "groq_model",
        None,
    )

    if not groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    if not groq_model:
        raise RuntimeError("GROQ_MODEL is not configured. Example: openai/gpt-oss-20b")

    questions = load_questions()
    logger.info(
        "Starting provider evaluation | questions=%s | generation_provider=Groq | model=%s",
        len(questions),
        groq_model,
    )

    session = create_snowflake_session()
    try:
        retriever = SnowflakeRetriever(
            session=session,
        )
        snowflake_generator = SnowflakeGenerator(
            session=session,
        )
        groq_generator = OpenAIGenerator(
            settings=settings,
        )

        # RAGService is used only for final evidence selection.
        # Both providers receive exactly the same final evidence.
        evidence_service = RAGService(
            retriever=retriever,
            generator=snowflake_generator,
        )

        results = []

        for row in questions:
            question_id = row["id"].strip()
            question = row["question"].strip()
            expected_source = row["expected_source"].strip()
            answerable = row["answerable"].strip().lower() == "true"

            print()
            print("=" * 80)
            print(f"{question_id}: {question}")

            retrieval_error = ""
            filter_error = ""
            final_evidence = []

            retrieval_started = time.perf_counter()
            try:
                retrieved = retriever.retrieve(
                    query=question,
                    top_k=20,
                )
            except Exception as exc:
                retrieved = []
                retrieval_error = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "Retrieval failed | question_id=%s",
                    question_id,
                )
            retrieval_time = time.perf_counter() - retrieval_started

            if not retrieval_error:
                try:
                    final_evidence = evidence_service._filter_evidence(
                        question,
                        retrieved,
                    )
                except Exception as exc:
                    filter_error = f"{type(exc).__name__}: {exc}"
                    logger.exception(
                        "Evidence filtering failed | question_id=%s",
                        question_id,
                    )

            retrieved_sources = get_sources(final_evidence)
            prompt = build_prompt(
                question,
                final_evidence,
            )

            print(
                "Final evidence:",
                retrieved_sources or "NONE",
            )

            if retrieval_error:
                print("Retrieval error:", retrieval_error)
            if filter_error:
                print("Evidence filtering error:", filter_error)

            if retrieval_error or filter_error:
                snowflake_answer = ""
                snowflake_error = retrieval_error or filter_error
                snowflake_time = retrieval_time
                groq_answer = ""
                groq_error = retrieval_error or filter_error
                groq_time = 0.0
            else:
                snowflake_answer, snowflake_error, snowflake_time = run_provider(
                    snowflake_generator,
                    prompt,
                )
                groq_answer, groq_error, groq_time = run_provider(
                    groq_generator,
                    prompt,
                )

            print(f"Snowflake: {snowflake_time:.2f}s")
            print(f"Groq: {groq_time:.2f}s")

            results.append(
                {
                    "id": question_id,
                    "question": question,
                    "expected_source": expected_source,
                    "answerable": str(answerable).lower(),
                    "retrieved_sources": retrieved_sources,
                    "retrieval_error": retrieval_error,
                    "evidence_filter_error": filter_error,
                    "snowflake_answer": snowflake_answer,
                    "snowflake_error": snowflake_error,
                    "snowflake_response_time_sec": round(
                        snowflake_time,
                        3,
                    ),
                    "groq_answer": groq_answer,
                    "groq_error": groq_error,
                    "groq_response_time_sec": round(
                        groq_time,
                        3,
                    ),
                }
            )

        OUTPUT_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with OUTPUT_FILE.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as handle:
            fieldnames = [
                "id",
                "question",
                "expected_source",
                "answerable",
                "retrieved_sources",
                "retrieval_error",
                "evidence_filter_error",
                "snowflake_answer",
                "snowflake_error",
                "snowflake_response_time_sec",
                "groq_answer",
                "groq_error",
                "groq_response_time_sec",
            ]

            writer = csv.DictWriter(
                handle,
                fieldnames=fieldnames,
            )
            writer.writeheader()
            writer.writerows(results)

        print()
        print("=" * 80)
        print(f"Evaluation completed: {len(results)} questions")
        print(f"Results written to: {OUTPUT_FILE}")

    finally:
        session.close()
        logger.info("Snowflake evaluation session closed.")


if __name__ == "__main__":
    main()
