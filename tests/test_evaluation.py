import csv
from pathlib import Path

from app.demo_providers import DemoRetriever


def load_evaluation_questions():
    """Load evaluation questions from the CSV file."""

    project_root = Path(__file__).resolve().parent.parent
    csv_path = project_root / "data" / "evaluation_questions.csv"

    with csv_path.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as file:
        return list(csv.DictReader(file))


def normalize_source(source: str) -> str:
    """
    Normalize FAQ source names so that harmless capitalization
    differences do not cause evaluation failures.

    Example:
        Returns FAQ
        Returns Faq

    Both become:
        returns faq
    """

    return " ".join(source.strip().lower().split())


def evaluate():
    """Evaluate DemoRetriever against the evaluation dataset."""

    questions = load_evaluation_questions()
    retriever = DemoRetriever()

    total = len(questions)
    passed = 0
    failed = 0

    print()
    print("=" * 75)
    print("RetailAssist RAG Retrieval Evaluation")
    print("=" * 75)

    for row in questions:
        question_id = row["id"]
        question = row["question"]
        expected_source = row["expected_source"]
        answerable = row["answerable"].lower() == "true"

        results = retriever.retrieve(
            query=question,
            top_k=5,
        )

        retrieved_sources = [chunk.document_name for chunk in results]

        normalized_retrieved = [
            normalize_source(source) for source in retrieved_sources
        ]

        normalized_expected = normalize_source(expected_source)

        # ---------------------------------------------------------
        # Answerable question
        # ---------------------------------------------------------

        if answerable:
            test_passed = normalized_expected in normalized_retrieved

        # ---------------------------------------------------------
        # Unsupported question
        # ---------------------------------------------------------

        else:
            # The current keyword retriever may return weakly
            # related chunks for unsupported questions.
            #
            # We report these separately rather than treating
            # every retrieved chunk as a definite failure.
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

    # -------------------------------------------------------------
    # Final summary
    # -------------------------------------------------------------

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
