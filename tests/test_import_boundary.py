import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def python_files_under(directory: str):
    return (PROJECT_ROOT / directory).rglob("*.py")


def test_app_and_rag_do_not_import_snowflake():
    protected_directories = [
        "app",
        "rag",
    ]

    forbidden_terms = {
        "snowflake",
        "snowflake.snowpark",
        "snowflake.cortex",
    }

    violations = []

    for directory in protected_directories:
        for file_path in python_files_under(directory):
            source = file_path.read_text(encoding="utf-8")

            tree = ast.parse(
                source,
                filename=str(file_path),
            )

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.lower() in forbidden_terms:
                            violations.append(f"{file_path}: {alias.name}")

                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""

                    if any(module.lower().startswith(term) for term in forbidden_terms):
                        violations.append(f"{file_path}: {module}")

    assert not violations, "Snowflake imports found in protected layers:\n" + "\n".join(
        violations
    )
