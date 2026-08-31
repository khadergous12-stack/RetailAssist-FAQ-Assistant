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
from providers.snowflake.retriever import SnowflakeRetriever
from rag.prompts import SYSTEM_PROMPT, build_grounded_prompt
from rag.service import RAGService


QUESTIONS_FILE = PROJECT_ROOT / "data" / "evaluation_questions.csv"
RESULTS_FILE = PROJECT_ROOT / "data" / "provider_evaluation_results.csv"

FAILED_IDS = {"E001", "E004", "E005", "E014", "E018"}


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
    return (
        f"{SYSTEM_PROMPT}\n\n{build_grounded_prompt(question, format_evidence(chunks))}"
    )


def main() -> None:
    settings = load_settings()

    session = create_snowflake_session()

    try:
        retriever = SnowflakeRetriever(session=session)

        evidence_service = RAGService(
            retriever=retriever,
            generator=None,
        )

        generator = OpenAIGenerator(settings=settings)

        with QUESTIONS_FILE.open(
            "r",
            encoding="utf-8",
            newline="",
        ) as handle:
            questions = list(csv.DictReader(handle))

        with RESULTS_FILE.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            existing_results = list(csv.DictReader(handle))

        retry_answers = {}

        for row in questions:
            if row["id"] not in FAILED_IDS:
                continue

            question_id = row["id"]
            question = row["question"]

            print()
            print("=" * 80)
            print(f"Retrying {question_id}: {question}")

            retrieved = retriever.retrieve(
                query=question,
                top_k=20,
            )

            final_evidence = evidence_service._filter_evidence(
                question,
                retrieved,
            )

            prompt = build_prompt(
                question,
                final_evidence,
            )

            started = time.perf_counter()

            try:
                answer = generator.generate(prompt)
                error = ""
            except Exception as exc:
                answer = ""
                error = str(exc)

            elapsed = time.perf_counter() - started

            print(
                "Evidence:",
                "; ".join(chunk.document_name for chunk in final_evidence) or "NONE",
            )

            print(f"Time: {elapsed:.2f}s")

            if answer:
                print("Answer:", answer)
            else:
                print("ERROR:", error)

            retry_answers[question_id] = {
                "answer": answer,
                "error": error,
                "time": round(elapsed, 3),
            }

        # Update only the five failed OpenRouter rows.
        for row in existing_results:
            question_id = row["id"]

            if question_id not in retry_answers:
                continue

            result = retry_answers[question_id]

            row["openrouter_answer"] = result["answer"]
            row["openrouter_error"] = result["error"]
            row["openrouter_response_time_sec"] = result["time"]

        with RESULTS_FILE.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as handle:
            fieldnames = existing_results[0].keys()

            writer = csv.DictWriter(
                handle,
                fieldnames=fieldnames,
            )

            writer.writeheader()
            writer.writerows(existing_results)

        print()
        print("=" * 80)
        print("Retry complete.")
        print(f"Updated: {len(retry_answers)} OpenRouter results.")

    finally:
        session.close()


if __name__ == "__main__":
    main()
