from __future__ import annotations

import csv
import re
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

INPUT_FILE = ROOT / "data" / "provider_evaluation_results.csv"
OUTPUT_FILE = ROOT / "data" / "provider_evaluation_summary.csv"


REFUSAL_PHRASES = (
    "i couldn't find a policy",
    "i could not find a policy",
    "couldn't find a policy",
    "could not find a policy",
    "not enough information",
    "no policy was found",
)


def normalize_source(value: str) -> str:
    """
    Normalize source names while preserving meaningful policy identity.

    The evaluation CSV defines the expected source. We only normalize
    harmless filename/format differences; we do not treat unrelated
    policies as equivalent.
    """

    value = str(value or "").strip().lower()

    if not value:
        return ""

    if value.upper() == "NONE":
        return "none"

    value = re.sub(
        r"\.(pdf|docx|md|txt)$",
        "",
        value,
    )

    value = re.sub(
        r"[_\-]+",
        " ",
        value,
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    ).strip()

    aliases = {
        "returns faq": "returns faq",
        "return faq": "returns faq",
        "return policy": "returns faq",
        "refunds faq": "refunds faq",
        "refund faq": "refunds faq",
        "shipping faq": "shipping faq",
        "payments faq": "payments faq",
        "warranty faq": "warranty faq",
        "supportai shipping policy": "shipping faq",
        "supportai refund policy": "refunds faq",
    }

    return aliases.get(value, value)


def is_refusal(answer: str) -> bool:
    text = str(answer or "").strip().lower()
    return any(phrase in text for phrase in REFUSAL_PHRASES)


def source_correct(
    expected_source: str,
    retrieved_sources: str,
) -> str:
    """
    Return:
      true  -> expected source retrieved
      false -> expected source not retrieved
      ""    -> source correctness is not applicable

    Unsupported questions use expected_source=NONE, so source correctness
    should not be counted as a normal retrieval failure.
    """

    expected = normalize_source(expected_source)

    if expected == "none":
        return ""

    sources = {
        normalize_source(source)
        for source in str(retrieved_sources or "").split(";")
        if source.strip()
    }

    return str(expected in sources).lower()


def unsupported_handled(
    answerable: bool,
    answer: str,
    error: str,
) -> str:
    if answerable:
        return ""

    if error:
        return "false"

    return str(is_refusal(answer)).lower()


def safe_float(value: str) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Evaluation results file not found: {INPUT_FILE}")

    with INPUT_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        raise ValueError(f"Evaluation results file is empty: {INPUT_FILE}")

    providers = {
        "Snowflake Cortex": {
            "answer": "snowflake_answer",
            "error": "snowflake_error",
            "time": "snowflake_response_time_sec",
        },
        "Groq (GPT-OSS 20B)": {
            "answer": "groq_answer",
            "error": "groq_error",
            "time": "groq_response_time_sec",
        },
    }

    detail_rows = []
    summary = []

    for provider_name, columns in providers.items():
        successful_times = []
        successful_count = 0
        source_correct_count = 0
        source_applicable_count = 0
        unsupported_total = 0
        unsupported_correct = 0

        for row in rows:
            answer = (
                row.get(
                    columns["answer"],
                    "",
                )
                or ""
            )

            error = (
                row.get(
                    columns["error"],
                    "",
                )
                or ""
            )

            response_time = safe_float(
                row.get(
                    columns["time"],
                    "0",
                )
            )

            answerable = (
                row.get(
                    "answerable",
                    "",
                )
                .strip()
                .lower()
                == "true"
            )

            source_ok = source_correct(
                row.get(
                    "expected_source",
                    "",
                ),
                row.get(
                    "retrieved_sources",
                    "",
                ),
            )

            if source_ok != "":
                source_applicable_count += 1
                if source_ok == "true":
                    source_correct_count += 1

            unsupported_ok = ""

            if not answerable:
                unsupported_total += 1

                unsupported_ok = unsupported_handled(
                    answerable,
                    answer,
                    error,
                )

                if unsupported_ok == "true":
                    unsupported_correct += 1

            request_ok = not bool(error)

            if request_ok:
                successful_count += 1
                successful_times.append(response_time)

            detail_rows.append(
                {
                    "provider": provider_name,
                    "question_id": row.get(
                        "id",
                        "",
                    ),
                    "answerable": str(answerable).lower(),
                    "request_successful": str(request_ok).lower(),
                    "source_correct": source_ok,
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
                "source_applicable": source_applicable_count,
                "unsupported_total": unsupported_total,
                "unsupported_handled": unsupported_correct,
                "average_response_time_sec": round(
                    average_time,
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
        print(
            f"Source-correct retrievals: "
            f"{row['source_correct']}/{row['source_applicable']}"
        )
        print(
            f"Unsupported questions handled: "
            f"{row['unsupported_handled']}/"
            f"{row['unsupported_total']}"
        )
        print(f"Average response time: {row['average_response_time_sec']:.3f}s")
        print()

    print(f"Detailed results written to: {OUTPUT_FILE}")
    print(
        "Correctness and groundedness remain blank because "
        "the evaluation dataset provides expected sources but "
        "does not provide reference answers for objective "
        "answer-level scoring."
    )


if __name__ == "__main__":
    main()
