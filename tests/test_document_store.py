import hashlib

import pytest

from providers.snowflake.document_store import DocumentStore


def test_sanitize_filename_removes_path_and_unsafe_characters():
    assert DocumentStore.sanitize_filename(
        r"..\folder\My policy (final).pdf"
    ) == "My_policy__final_.pdf"


def test_validate_file_rejects_unsupported_extension():
    with pytest.raises(ValueError, match="Unsupported document type"):
        DocumentStore.validate_file(b"hello", "policy.exe")


def test_validate_file_rejects_empty_file():
    with pytest.raises(ValueError, match="is empty"):
        DocumentStore.validate_file(b"", "policy.md")


def test_content_hash_is_stable_sha256():
    data = b"retailassist"
    assert DocumentStore._calculate_content_hash(data) == hashlib.sha256(data).hexdigest()


def test_category_detection_uses_filename_and_content():
    content = """
    # Why is my payment pending?
    A pending payment may still be processing.
    """

    assert DocumentStore.detect_category(content, "customer_policy.md") == "payments"


def test_category_detection_from_filename():
    assert (
        DocumentStore.detect_category(
            "The estimated delivery window is shown during checkout.",
            "delivery_policy.pdf",
        )
        == "shipping"
    )


def test_unknown_category_is_rejected():
    with pytest.raises(ValueError, match="Could not determine"):
        DocumentStore.detect_category(
            "This document is about employee vacation scheduling.",
            "internal_notes.txt",
        )


def test_faq_chunking_keeps_question_with_answer():
    content = """
    # How long does delivery take?
    Delivery usually takes 3 to 5 business days.

    # Can I track my order?
    Yes. Tracking information is shown in your order details.
    """

    chunks = DocumentStore._build_chunks(content)

    assert len(chunks) == 2
    assert "How long does delivery take?" in chunks[0]
    assert "3 to 5 business days" in chunks[0]
    assert "Can I track my order?" in chunks[1]


def test_plain_text_chunking_splits_large_paragraphs():
    content = ("Shipping policy information. " * 100) + "\n\n" + (
        "Delivery policy information. " * 100
    )

    chunks = DocumentStore._build_chunks(content)

    assert chunks
    assert all(len(chunk) <= 1250 for chunk in chunks)


def test_allowed_extensions_include_all_required_formats():
    assert DocumentStore.ALLOWED_EXTENSIONS == {".txt", ".md", ".pdf", ".docx"}
