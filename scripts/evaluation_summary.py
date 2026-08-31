from __future__ import annotations

import csv
import re
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

INPUT_FILE = ROOT / "data" / "provider_evaluation_results.csv"
OUTPUT_FILE = ROOT / "data" / "provider_evaluation_summary.csv"


REFUSAL_PHRASES = (
    "couldn't find a policy",
    "could not find a policy",
    "not enough information",
    "no policy was found",
)


def normalize_source(value: str) -> str:
    """
    Normalize filenames and built-in document names so equivalent sources
    compare correctly.
    """

    value = str(value or "").strip().lower()

    # Remove extension.
    value = re.sub(r"\.(pdf|docx|md|txt)$", "", value)

    # Normalize separators.
    value = re.sub(r"[_\-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    # Normalize known built-in naming differences.
    aliases = {
        "returns faq": "returns faq",
        "return policy": "returns faq",
        "refunds faq": "refunds faq",
        "shipping faq": "shipping faq",
        "payments faq": "payments faq",
        "warranty faq": "warranty faq",
        "supportai shipping policy": "shipping policy",
        "supportai refund policy": "refunds faq",
    }

    return aliases.get(value, value)


def is_refusal(answer: str) -> bool:
    text = (answer or "").strip().lower()

    return any(phrase in text for phrase in REFUSAL_PHRASES)


def source_correct(
    expected_source: str,
    retrieved_sources: str,
) -> bool:
    expected = normalize_source(expected_source)

    if expected.upper() == "NONE":
        return False

    sources = {
        normalize_source(source)
        for source in retrieved_sources.split(";")
        if source.strip()
    }

    return expected in sources


def unsupported_handled(
    answerable: bool,
    answer: str,
) -> str:
    if answerable:
        return ""

    return str(is_refusal(answer)).lower()


def main() -> None:
    with INPUT_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle))

    providers = {
        "Snowflake Cortex": {
            "answer": "snowflake_answer",
            "error": "snowflake_error",
            "time": "snowflake_response_time_sec",
        },
        "OpenRouter": {
            "answer": "openrouter_answer",
            "error": "openrouter_error",
            "time": "openrouter_response_time_sec",
        },
    }

    detail_rows = []
    summary = []

    for provider_name, columns in providers.items():
        successful_times = []
        successful_count = 0
        source_correct_count = 0
        unsupported_total = 0
        unsupported_correct = 0

        for row in rows:
            answer = row.get(columns["answer"], "") or ""
            error = row.get(columns["error"], "") or ""

            response_time = float(row.get(columns["time"], "0") or 0)

            answerable = row.get("answerable", "").strip().lower() == "true"

            source_ok = source_correct(
                row.get("expected_source", ""),
                row.get("retrieved_sources", ""),
            )

            unsupported_ok = ""

            if not answerable:
                unsupported_total += 1
                unsupported_ok = unsupported_handled(
                    answerable,
                    answer,
                )

                if unsupported_ok == "true":
                    unsupported_correct += 1

            request_ok = not bool(error)

            if request_ok:
                successful_count += 1
                successful_times.append(response_time)

            if source_ok:
                source_correct_count += 1

            detail_rows.append(
                {
                    "provider": provider_name,
                    "question_id": row["id"],
                    "answerable": str(answerable).lower(),
                    "request_successful": str(request_ok).lower(),
                    "source_correct": str(source_ok).lower(),
                    "unsupported_handled": unsupported_ok,
                    "response_time_sec": response_time,
                    "correctness": "",
                    "groundedness": "",
                    "answer": answer,
                    "error": error,
                }
            )

        average_time = statistics.mean(successful_times) if successful_times else 0.0

        summary.append(
            {
                "provider": provider_name,
                "questions": len(rows),
                "successful_responses": successful_count,
                "source_correct": source_correct_count,
                "unsupported_total": unsupported_total,
                "unsupported_handled": unsupported_correct,
                "average_response_time_sec": round(
                    average_time,
                    3,
                ),
            }
        )

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        fieldnames = detail_rows[0].keys()

        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(detail_rows)

    print()
    print("=" * 65)

    for row in summary:
        print(row["provider"])
        print("-" * 65)
        print(f"Successful responses: {row['successful_responses']}/{row['questions']}")
        print(f"Source-correct retrievals: {row['source_correct']}/{row['questions']}")
        print(
            f"Unsupported questions handled: "
            f"{row['unsupported_handled']}/"
            f"{row['unsupported_total']}"
        )
        print(f"Average response time: {row['average_response_time_sec']:.3f}s")
        print()

    print(f"Detailed results written to: {OUTPUT_FILE}")
    print(
        "Correctness and groundedness remain blank because the supplied "
        "evaluation CSV does not contain expected answers for objective "
        "automatic scoring."
    )


if __name__ == "__main__":
    main()
