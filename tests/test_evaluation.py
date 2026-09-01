from __future__ import annotations

import csv
from pathlib import Path

from app.demo_providers import DemoRetriever


REQUIRED_COLUMNS = {
    "id",
    "question",
    "expected_source",
    "answerable",
}


def load_evaluation_questions():
    """Load and validate evaluation questions."""

    project_root = Path(__file__).resolve().parent.parent

    csv_path = project_root / "data" / "evaluation_questions.csv"

    if not csv_path.exists():
        raise FileNotFoundError(f"Evaluation questions file not found: {csv_path}")

    with csv_path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        rows = list(csv.DictReader(file))

    if not rows:
        raise ValueError("Evaluation questions file is empty.")

    missing = REQUIRED_COLUMNS - set(rows[0].keys())

    if missing:
        raise ValueError(
            "Evaluation CSV is missing required columns: " + ", ".join(sorted(missing))
        )

    return rows


def normalize_source(source: str) -> str:
    """
    Normalize harmless case and whitespace differences.
    """

    return " ".join(str(source or "").strip().lower().split())


def evaluate():
    """Evaluate DemoRetriever against the evaluation dataset."""

    questions = load_evaluation_questions()
    retriever = DemoRetriever()

    total = len(questions)
    passed = 0
    failed = 0

    print()
    print("=" * 75)
    print("SupportAI RAG Retrieval Evaluation")
    print("=" * 75)

    for row in questions:
        question_id = row["id"]
        question = row["question"]
        expected_source = row["expected_source"]
        answerable = row["answerable"].strip().lower() == "true"

        results = retriever.retrieve(
            query=question,
            top_k=5,
        )

        retrieved_sources = [chunk.document_name for chunk in results]

        normalized_retrieved = [
            normalize_source(source) for source in retrieved_sources
        ]

        normalized_expected = normalize_source(expected_source)

        if answerable:
            test_passed = normalized_expected in normalized_retrieved
        else:
            # For unsupported questions, the lightweight demo retriever
            # is expected to return no candidates.
            test_passed = len(results) == 0

        if test_passed:
            passed += 1
            status = "PASS"
        else:
            failed += 1
            status = "FAIL"

        print()
        print(f"{question_id}  [{status}]")
        print(f"Question : {question}")
        print(f"Expected : {expected_source}")

        if retrieved_sources:
            print(
                "Retrieved:",
                ", ".join(retrieved_sources),
            )
        else:
            print("Retrieved: NONE")

    accuracy = (passed / total) * 100 if total else 0

    print()
    print("=" * 75)
    print("Evaluation Summary")
    print("=" * 75)

    print(f"Total Questions : {total}")
    print(f"Passed          : {passed}")
    print(f"Failed          : {failed}")
    print(f"Accuracy        : {accuracy:.2f}%")

    print("=" * 75)

    return failed == 0


if __name__ == "__main__":
    success = evaluate()

    if not success:
        raise SystemExit(1)
