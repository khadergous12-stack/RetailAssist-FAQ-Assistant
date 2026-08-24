from pathlib import Path


# ------------------------------------------------------------
# RetailAssist FAQ Assistant
# Generate reproducible Snowflake seed SQL
# ------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_FILE = BASE_DIR / "sql" / "02_seed.sql"


DOCUMENTS = [
    {
        "file": "warranty_faq.md",
        "document_id": "WARRANTY",
        "document_name": "Warranty FAQ",
        "category": "warranty",
    },
    {
        "file": "shipping_faq.md",
        "document_id": "SHIPPING",
        "document_name": "Shipping FAQ",
        "category": "shipping",
    },
    {
        "file": "payments_faq.md",
        "document_id": "PAYMENTS",
        "document_name": "Payments FAQ",
        "category": "payments",
    },
    {
        "file": "refunds_faq.md",
        "document_id": "REFUNDS",
        "document_name": "Refunds FAQ",
        "category": "refunds",
    },
    {
        "file": "returns_faq.md",
        "document_id": "RETURNS",
        "document_name": "Returns FAQ",
        "category": "returns",
    },
]


def snowflake_string(value: str) -> str:
    """
    Convert Python text into a safe Snowflake SQL string literal.

    Snowflake supports $$...$$ string constants, which makes
    multiline Markdown easier to preserve.
    """

    if "$$" not in value:
        return f"$${value}$$"

    # Fallback if the document itself contains $$.
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def main():
    statements = []

    statements.append(
        """-- ============================================================
-- RetailAssist FAQ Assistant
-- Phase 2: Generated Seed Data
-- DO NOT EDIT MANUALLY
-- Regenerate this file using:
-- python scripts/generate_seed.py
-- ============================================================

USE DATABASE RETAIL_ASSIST_DB;
USE SCHEMA RETAIL_ASSIST;

-- Clear previous seed data so this script is reproducible.
TRUNCATE TABLE POLICY_SOURCES;

"""
    )

    for document in DOCUMENTS:
        source_file = DATA_DIR / document["file"]

        if not source_file.exists():
            raise FileNotFoundError(f"Missing source document: {source_file}")

        content = source_file.read_text(encoding="utf-8").strip()

        if not content:
            raise ValueError(f"Source document is empty: {source_file}")

        content_sql = snowflake_string(content)

        statement = f"""INSERT INTO POLICY_SOURCES (
    DOCUMENT_ID,
    DOCUMENT_NAME,
    CATEGORY,
    CONTENT
)
VALUES (
    '{document["document_id"]}',
    '{document["document_name"]}',
    '{document["category"]}',
    {content_sql}
);

"""

        statements.append(statement)

    statements.append(
        """-- ============================================================
-- Validation
-- ============================================================

SELECT
    DOCUMENT_ID,
    DOCUMENT_NAME,
    CATEGORY,
    LENGTH(CONTENT) AS CONTENT_LENGTH
FROM POLICY_SOURCES
ORDER BY DOCUMENT_ID;
"""
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        "".join(statements),
        encoding="utf-8",
    )

    print("Seed SQL generated successfully.")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Documents generated: {len(DOCUMENTS)}")


if __name__ == "__main__":
    main()
