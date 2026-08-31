from __future__ import annotations

import csv
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


QUESTIONS_FILE = PROJECT_ROOT / "data" / "evaluation_questions.csv"
OUTPUT_FILE = PROJECT_ROOT / "data" / "provider_evaluation_results.csv"


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
    return "; ".join(dict.fromkeys(chunk.document_name for chunk in chunks))


def run_provider(generator, prompt: str):
    started = time.perf_counter()

    try:
        answer = generator.generate(prompt)
        error = ""
    except Exception as exc:
        answer = ""
        error = str(exc)

    elapsed = time.perf_counter() - started

    return answer, error, elapsed


def main() -> None:
    settings = load_settings()

    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured.")

    if not settings.openrouter_model:
        raise RuntimeError("OPENROUTER_MODEL is not configured.")

    session = create_snowflake_session()

    try:
        retriever = SnowflakeRetriever(
            session=session,
        )

        snowflake_generator = SnowflakeGenerator(
            session=session,
        )

        openrouter_generator = OpenAIGenerator(
            settings=settings,
        )

        # RAGService is used only for evidence selection.
        # Both providers will receive the exact same final evidence.
        evidence_service = RAGService(
            retriever=retriever,
            generator=snowflake_generator,
        )

        with QUESTIONS_FILE.open(
            "r",
            encoding="utf-8",
            newline="",
        ) as handle:
            questions = list(csv.DictReader(handle))

        results = []

        for row in questions:
            question_id = row["id"]
            question = row["question"]
            expected_source = row["expected_source"]
            answerable = row["answerable"].strip().lower() == "true"

            print()
            print("=" * 80)
            print(f"{question_id}: {question}")

            # --------------------------------------------------
            # Retrieve + filter ONCE
            # --------------------------------------------------

            retrieved = retriever.retrieve(
                query=question,
                top_k=20,
            )

            final_evidence = evidence_service._filter_evidence(
                question,
                retrieved,
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

            # --------------------------------------------------
            # Snowflake Cortex
            # --------------------------------------------------

            snowflake_answer, snowflake_error, snowflake_time = run_provider(
                snowflake_generator,
                prompt,
            )

            # --------------------------------------------------
            # OpenRouter
            # --------------------------------------------------

            openrouter_answer, openrouter_error, openrouter_time = run_provider(
                openrouter_generator,
                prompt,
            )

            print(f"Snowflake: {snowflake_time:.2f}s")
            print(f"OpenRouter: {openrouter_time:.2f}s")

            results.append(
                {
                    "id": question_id,
                    "question": question,
                    "expected_source": expected_source,
                    "answerable": str(answerable).lower(),
                    "retrieved_sources": retrieved_sources,
                    "snowflake_answer": snowflake_answer,
                    "snowflake_error": snowflake_error,
                    "snowflake_response_time_sec": round(
                        snowflake_time,
                        3,
                    ),
                    "openrouter_answer": openrouter_answer,
                    "openrouter_error": openrouter_error,
                    "openrouter_response_time_sec": round(
                        openrouter_time,
                        3,
                    ),
                }
            )

        with OUTPUT_FILE.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as handle:
            fieldnames = results[0].keys()

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


if __name__ == "__main__":
    main()
