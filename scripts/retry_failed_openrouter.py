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

    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured.")

    if not settings.openrouter_model:
        raise RuntimeError("OPENROUTER_MODEL is not configured.")

    if not QUESTIONS_FILE.exists():
        raise FileNotFoundError(
            f"Evaluation questions file not found: {QUESTIONS_FILE}"
        )

    if not RESULTS_FILE.exists():
        raise FileNotFoundError(f"Evaluation results file not found: {RESULTS_FILE}")

    session = create_snowflake_session()

    try:
        retriever = SnowflakeRetriever(
            session=session,
        )

        evidence_service = RAGService(
            retriever=retriever,
            generator=None,
        )

        generator = OpenAIGenerator(
            settings=settings,
        )

        with QUESTIONS_FILE.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            questions = list(csv.DictReader(handle))

        with RESULTS_FILE.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            existing_results = list(csv.DictReader(handle))

        result_by_id = {row["id"]: row for row in existing_results if row.get("id")}

        # Automatically find rows whose OpenRouter answer is empty or
        # whose previous OpenRouter request failed.
        retry_ids = [
            row["id"]
            for row in questions
            if (
                not str(
                    result_by_id.get(
                        row["id"],
                        {},
                    ).get(
                        "openrouter_answer",
                        "",
                    )
                    or ""
                ).strip()
                or str(
                    result_by_id.get(
                        row["id"],
                        {},
                    ).get(
                        "openrouter_error",
                        "",
                    )
                    or ""
                ).strip()
            )
        ]

        if not retry_ids:
            print("No failed OpenRouter rows require retry.")
            return

        print(f"Found {len(retry_ids)} OpenRouter rows to retry.")

        for row in questions:
            question_id = row["id"]

            if question_id not in retry_ids:
                continue

            question = row["question"]

            print()
            print("=" * 80)
            print(f"Retrying {question_id}: {question}")

            try:
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
            except Exception as exc:
                print(
                    "ERROR during retrieval/evidence preparation:",
                    f"{type(exc).__name__}: {exc}",
                )
                continue

            started = time.perf_counter()

            try:
                answer = generator.generate(prompt)
                error = ""
            except Exception as exc:
                answer = ""
                error = f"{type(exc).__name__}: {exc}"

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

            result = result_by_id.get(question_id)

            if result is not None:
                result["openrouter_answer"] = answer
                result["openrouter_error"] = error
                result["openrouter_response_time_sec"] = round(
                    elapsed,
                    3,
                )

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

            for row in existing_results:
                writer.writerow(row)

        remaining_failures = [
            row
            for row in existing_results
            if not str(
                row.get(
                    "openrouter_answer",
                    "",
                )
                or ""
            ).strip()
        ]

        print()
        print("=" * 80)
        print("OpenRouter retry complete.")
        print(f"Retried: {len(retry_ids)}")
        print(f"Remaining empty OpenRouter answers: {len(remaining_failures)}")
        print(f"Updated results: {RESULTS_FILE}")

    finally:
        session.close()


if __name__ == "__main__":
    main()
