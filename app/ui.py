import re
import html
import textwrap
import hashlib
import logging
import time

import streamlit as st

from app.controller import RetailAssistController
from rag.service import RAGResponse
from services.cortex_analyst import CortexAnalystResponse
from services.cortex_agent import CortexAgentResponse


logger = logging.getLogger(__name__)


FRAGMENT_AVAILABLE = callable(getattr(st, "fragment", None))


def local_rerun() -> None:
    """Rerun only the current fragment when supported by Streamlit."""
    if FRAGMENT_AVAILABLE:
        try:
            st.rerun(scope="fragment")
            return
        except Exception:
            logger.debug("Fragment rerun unavailable; falling back to full app rerun.")
    st.rerun()


def generate_document_id(file_bytes: bytes) -> str:
    """Generate a unique document ID from file contents."""
    return hashlib.sha256(file_bytes).hexdigest()[:16]


def validate_uploaded_files(uploaded_files):
    """Validate uploaded knowledge-base documents."""
    valid_files = []
    errors = []

    max_files = 5
    max_size = 20 * 1024 * 1024  # 20 MB

    if len(uploaded_files) > max_files:
        errors.append(f"Maximum {max_files} files can be uploaded at once.")
        uploaded_files = uploaded_files[:max_files]

    allowed_extensions = {".pdf", ".docx", ".md", ".txt"}

    for uploaded_file in uploaded_files:
        filename = uploaded_file.name.lower()

        if not any(filename.endswith(ext) for ext in allowed_extensions):
            errors.append(f"{uploaded_file.name}: Unsupported file type.")
            continue

        file_bytes = uploaded_file.getvalue()

        if len(file_bytes) > max_size:
            errors.append(f"{uploaded_file.name}: File exceeds the 20 MB limit.")
            continue

        if len(file_bytes) == 0:
            errors.append(f"{uploaded_file.name}: File is empty.")
            continue

        valid_files.append(uploaded_file)

    return valid_files, errors


def detect_category_cached(controller, file_bytes: bytes, filename: str) -> str:
    """Cache category detection for the same file content during this session."""
    cache = st.session_state.setdefault("supportai_category_cache", {})
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    cache_key = f"{filename.lower()}::{file_hash}"

    if cache_key in cache:
        return cache[cache_key]

    started = time.perf_counter()
    try:
        if controller.document_store is None:
            detected = "Unable to detect"
        else:
            detected = controller.document_store.detect_category_from_file(
                file_bytes,
                filename,
            )
    except Exception:
        logger.exception("Document category detection failed | filename=%s", filename)
        detected = "Unable to detect"

    cache[cache_key] = detected
    logger.info(
        "Category detection completed | filename=%s | category=%s | duration=%.3fs",
        filename,
        detected,
        time.perf_counter() - started,
    )
    return detected


# ============================================================
# Answer cleanup
# ============================================================


def clean_answer(
    answer: str,
    question: str,
) -> str:
    """
    Clean unnecessary repetition from the generated answer.
    """

    if not answer:
        return ""

    answer = answer.strip()
    question = question.strip()

    if not question:
        return answer

    normalized_answer = re.sub(
        r"\s+",
        " ",
        answer,
    ).strip()

    normalized_question = re.sub(
        r"\s+",
        " ",
        question,
    ).strip()

    # Remove phrases that should never be shown to the user.
    unwanted_prefixes = [
        r"^based on the provided policy evidence[:,]?\s*",
        r"^based on the policy evidence[:,]?\s*",
        r"^according to the evidence[:,]?\s*",
        r"^according to the provided evidence[:,]?\s*",
        r"^according to the policy evidence[:,]?\s*",
        r"^the evidence shows[:,]?\s*",
        r"^the policy evidence shows[:,]?\s*",
        r"^answer[:,]?\s*",
        r"^final answer[:,]?\s*",
    ]

    for pattern in unwanted_prefixes:
        normalized_answer = re.sub(
            pattern,
            "",
            normalized_answer,
            flags=re.IGNORECASE,
        ).strip()

    # Remove "The customer's question is..."
    normalized_answer = re.sub(
        r"^the customer's question is\s*[:\-]?\s*.*?"
        r"(?:\.\s+|:\s+)",
        "",
        normalized_answer,
        flags=re.IGNORECASE,
    ).strip()

    # Remove exact question repetition.
    if normalized_answer.lower().startswith(normalized_question.lower()):
        cleaned = normalized_answer[len(normalized_question) :].strip()

        cleaned = re.sub(
            r"^[\s:,;\-–—]+",
            "",
            cleaned,
        ).strip()

        if cleaned:
            normalized_answer = cleaned

    # Remove "Question: <question>"
    question_prefix_pattern = (
        rf"^(?:question|user question|your question)"
        rf"\s*[:\-–—]\s*"
        rf"{re.escape(normalized_question)}"
        rf"\s*"
    )

    normalized_answer = re.sub(
        question_prefix_pattern,
        "",
        normalized_answer,
        flags=re.IGNORECASE,
    ).strip()

    return normalized_answer


# ============================================================
# Source extraction
# ============================================================


def split_source_text(text: str):
    """
    Split a single FAQ chunk into:

        question
        answer

    Example:

        ## Where will my refund be sent?

        Refunds are sent to the original payment method...

    becomes:

        question = "Where will my refund be sent?"
        answer = "Refunds are sent..."
    """

    if not text:
        return "", ""

    text = text.strip()

    lines = text.splitlines()

    if not lines:
        return "", ""

    first_line = lines[0].strip()

    heading_match = re.match(
        r"^#{1,6}\s+(.+?)\s*$",
        first_line,
    )

    if heading_match:
        source_question = heading_match.group(1).strip()

        remaining_text = "\n".join(lines[1:]).strip()

        return (
            source_question,
            remaining_text,
        )

    return "", text


# ============================================================
# Extract relevant FAQ section
# ============================================================


def extract_relevant_section(
    question: str,
    chunk_text: str,
) -> tuple[str, str]:
    """
    Extract exactly one FAQ entry from a retrieved chunk.

    The retrieval/RAG pipeline is unchanged. This function only controls
    which already-retrieved FAQ section is displayed in the UI.
    """

    if not chunk_text or not chunk_text.strip():
        return "", ""

    text = chunk_text.strip()

    # Find Markdown FAQ headings and preserve the text until the next heading.
    heading_pattern = re.compile(r"(?m)^\s*#{1,6}\s+(.+?)\s*$")
    matches = list(heading_pattern.finditer(text))

    if not matches:
        return "", text

    stop_words = {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "my",
        "me",
        "i",
        "we",
        "you",
        "your",
        "our",
        "to",
        "for",
        "of",
        "on",
        "in",
        "at",
        "and",
        "or",
        "but",
        "what",
        "where",
        "when",
        "how",
        "why",
        "can",
        "could",
        "would",
        "will",
        "do",
        "does",
        "did",
        "it",
        "this",
        "that",
        "please",
        "tell",
    }

    important_terms = {
        "refund",
        "return",
        "returned",
        "exchange",
        "replace",
        "shipping",
        "delivery",
        "delivered",
        "warranty",
        "damage",
        "damaged",
        "broken",
        "cracked",
        "defective",
        "card",
        "payment",
        "declined",
        "pending",
        "charge",
        "tracking",
        "lost",
        "late",
    }

    def tokenize(value: str) -> set[str]:
        words = re.findall(r"[a-z0-9]+", value.lower())
        return {word for word in words if word not in stop_words and len(word) > 2}

    question_words = tokenize(question)

    best_question = ""
    best_answer = ""
    best_score = -1

    for index, match in enumerate(matches):
        source_question = match.group(1).strip()
        section_start = match.end()
        section_end = (
            matches[index + 1].start() if index + 1 < len(matches) else len(text)
        )

        source_answer = text[section_start:section_end].strip()

        # Ignore empty headings.
        if not source_question or not source_answer:
            continue

        section_words = tokenize(source_question + " " + source_answer)
        overlap = question_words.intersection(section_words)

        score = len(overlap)
        score += len(overlap.intersection(important_terms)) * 2

        # Strongly prefer a heading whose wording directly matches the
        # customer's question.
        question_lower = question.strip().lower()
        heading_lower = source_question.lower()
        if heading_lower == question_lower:
            score += 20
        elif question_lower and heading_lower in question_lower:
            score += 8

        if score > best_score:
            best_score = score
            best_question = source_question
            best_answer = source_answer

    if best_question:
        return best_question, best_answer

    # Fallback: return the first actual FAQ entry.
    first = matches[0]
    first_end = matches[1].start() if len(matches) > 1 else len(text)
    return (
        first.group(1).strip(),
        text[first.end() : first_end].strip(),
    )


# ============================================================
# UI styling
# ============================================================


def inject_box_styles(theme: str = "light") -> None:
    """
    Inject professional, industry-style CSS for the SupportAI interface.
    This function changes presentation only; application logic is unchanged.
    """

    theme = str(theme or "light").strip().lower()
    if theme not in {"light", "dark"}:
        theme = "light"

    st.markdown(
        textwrap.dedent(
            """
            <style>

            /* =========================================================
               Global layout
               ========================================================= */

            .stApp {
                background:
                    radial-gradient(circle at 8% 8%, rgba(99, 102, 241, 0.18), transparent 28%),
                    radial-gradient(circle at 92% 18%, rgba(14, 165, 233, 0.16), transparent 30%),
                    radial-gradient(circle at 50% 100%, rgba(168, 85, 247, 0.11), transparent 34%),
                    radial-gradient(circle at 75% 65%, rgba(255, 255, 255, 0.75), transparent 22%),
                    linear-gradient(135deg, #eef4ff 0%, #f7f5ff 48%, #edf7fb 100%);
                min-height: 100vh;
            }

            .main .block-container {
                position: relative;
            }

            .main .block-container::before {
                content: "";
                position: fixed;
                inset: 0;
                pointer-events: none;
                background-image:
                    radial-gradient(rgba(99, 102, 241, 0.10) 1px, transparent 1px);
                background-size: 28px 28px;
                mask-image: linear-gradient(to bottom, rgba(0,0,0,0.32), transparent 48%);
                z-index: 0;
            }

            .block-container {
                max-width: 1240px;
                padding-top: 4.15rem;
                padding-bottom: 3rem;
            }

            /* Hide Streamlit chrome that is not useful for the product UI */
            #MainMenu {
                visibility: hidden;
            }

            footer {
                visibility: hidden;
            }

            /* =========================================================
               Hero / brand
               ========================================================= */

            .ra-hero {
                position: relative;
                overflow: hidden;
                padding: 30px 34px 78px;
                border-radius: 22px;
                margin-bottom: 24px;
                background:
                    linear-gradient(135deg, #0f172a 0%, #1e293b 58%, #312e81 100%);
                box-shadow: 0 18px 45px rgba(15, 23, 42, 0.16);
                color: white;
            }

            .ra-hero::after {
                content: "";
                position: absolute;
                width: 360px;
                height: 360px;
                right: -120px;
                top: -170px;
                border-radius: 50%;
                background:
                    radial-gradient(
                        circle at 35% 35%,
                        rgba(255, 255, 255, 0.22) 0%,
                        rgba(129, 140, 248, 0.14) 30%,
                        rgba(255, 255, 255, 0) 70%
                    );
                filter: blur(1px);
                animation: supportai-orb 8s ease-in-out infinite alternate;
            }

            .ra-hero::before {
                content: "";
                position: absolute;
                inset: -60% -20%;
                background:
                    linear-gradient(
                        115deg,
                        transparent 42%,
                        rgba(255, 255, 255, 0.08) 48%,
                        rgba(255, 255, 255, 0.20) 50%,
                        rgba(255, 255, 255, 0.08) 52%,
                        transparent 58%
                    );
                transform: translateX(-55%);
                animation: supportai-shine 7s ease-in-out infinite;
                pointer-events: none;
                z-index: 0;
            }

            @keyframes supportai-shine {
                0%, 18% {
                    transform: translateX(-55%) rotate(0deg);
                    opacity: 0;
                }
                30% {
                    opacity: 1;
                }
                58%, 100% {
                    transform: translateX(55%) rotate(0deg);
                    opacity: 0;
                }
            }

            @keyframes supportai-orb {
                from {
                    transform: translate3d(0, 0, 0) scale(0.96);
                }
                to {
                    transform: translate3d(-18px, 16px, 0) scale(1.06);
                }
            }

            .ra-brand-row {
                display: flex;
                align-items: center;
                gap: 14px;
                position: relative;
                z-index: 1;
            }

            .ra-brand-icon {
                width: 52px;
                height: 52px;
                display: flex;
                align-items: center;
                justify-content: center;
                border-radius: 15px;
                background: rgba(255, 255, 255, 0.12);
                border: 1px solid rgba(255, 255, 255, 0.18);
                font-size: 28px;
            }

            .ra-brand-title {
                font-size: 2rem;
                line-height: 1.1;
                font-weight: 800;
                letter-spacing: -0.03em;
                margin: 0;
                color: #ffffff !important;
                -webkit-text-fill-color: #ffffff !important;
            }

            .ra-brand-subtitle {
                margin: 7px 0 0;
                color: #cbd5e1;
                font-size: 0.98rem;
            }

            .ra-status-row,
            .ra-status-strip {
                display: flex;
                flex-wrap: wrap;
                gap: 9px;
                position: absolute;
                left: 34px;
                bottom: 18px;
                z-index: 3;
                pointer-events: none;
            }
            
            .ra-status {
                display: inline-flex;
                align-items: center;
                gap: 7px;
                padding: 7px 13px;
                border-radius: 999px;
                font-size: 0.76rem;
                font-weight: 700;
                color: #f8fafc !important;
                background: rgba(15, 23, 42, 0.72);
                border: 1px solid rgba(255, 255, 255, 0.28);
                box-shadow:
                    0 8px 18px rgba(15, 23, 42, 0.18),
                    inset 0 1px 0 rgba(255, 255, 255, 0.10);
                backdrop-filter: blur(8px);
                -webkit-backdrop-filter: blur(8px);
                white-space: nowrap;
            }

            .ra-status-dot {
                width: 7px;
                height: 7px;
                border-radius: 50%;
                background: #22c55e;
                box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.14);
            }

            /* =========================================================
               Section labels
               ========================================================= */

            .ra-section-label {
                font-size: 0.76rem;
                font-weight: 750;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                color: #64748b;
                margin: 22px 0 9px;
            }

            .ra-section-title {
                font-size: 1.12rem;
                font-weight: 750;
                color: #0f172a;
                margin: 0 0 10px;
            }

            /* =========================================================
               Input area
               ========================================================= */

            .ra-helper {
                color: #64748b;
                font-size: 0.86rem;
                line-height: 1.5;
                margin: 2px 0 12px;
            }

            div[data-testid="stTextArea"] {
                background: rgba(255, 255, 255, 0.96);
                border: 1px solid #d8e0ea;
                border-radius: 18px;
                padding: 9px;
                box-shadow: 0 7px 24px rgba(15, 23, 42, 0.045);
            }

            textarea {
                border-radius: 13px !important;
                border: 1px solid #cbd5e1 !important;
                background: #f8fafc !important;
                color: #0f172a !important;
                font-size: 0.98rem !important;
                line-height: 1.55 !important;
            }

            textarea:focus {
                border-color: #6366f1 !important;
                box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.10) !important;
            }

            div[data-testid="stTextArea"] label {
                font-weight: 650;
                color: #334155;
            }

            /* Buttons */
            .stButton > button {
                min-height: 46px;
                border-radius: 11px;
                font-weight: 700;
                border: 1px solid #cbd5e1;
                transition: all 0.15s ease;
            }

            .stButton > button:hover {
                transform: translateY(-1px);
                box-shadow: 0 7px 18px rgba(15, 23, 42, 0.10);
            }

            .stButton > button[kind="primary"] {
                border: none !important;
                background: linear-gradient(135deg, #4f46e5, #6366f1) !important;
                box-shadow: 0 8px 18px rgba(79, 70, 229, 0.22);
            }

            .stButton > button[kind="primary"]:hover {
                background: linear-gradient(135deg, #4338ca, #4f46e5) !important;
            }

            /* =========================================================
               Inline processing indicator
               ========================================================= */

            .ra-processing {
                display: flex;
                align-items: center;
                gap: 12px;
                padding: 12px 16px;
                margin: 10px 0 18px;
                border: 1px solid #dbe3ee;
                border-radius: 13px;
                background: rgba(255, 255, 255, 0.94);
                box-shadow: 0 6px 18px rgba(15, 23, 42, 0.05);
                color: #475569;
                font-size: 0.9rem;
                font-weight: 650;
            }

            .ra-processing-spinner {
                width: 18px;
                height: 18px;
                border: 2px solid #c7d2fe;
                border-top-color: #4f46e5;
                border-radius: 50%;
                animation: supportai-spin 0.8s linear infinite;
                flex: 0 0 auto;
            }

            @keyframes supportai-spin {
                to { transform: rotate(360deg); }
            }

            /* =========================================================
               Answer
               ========================================================= */

            .ra-answer-box {
                border-radius: 17px;
                padding: 22px 24px;
                margin: 5px 0 22px;
                background:
                    linear-gradient(135deg, #eef2ff 0%, #f8fafc 100%);
                border: 1px solid #c7d2fe;
                color: #1e293b;
                font-size: 1.03rem;
                line-height: 1.75;
                box-shadow: 0 8px 25px rgba(79, 70, 229, 0.07);
            }

            .ra-answer-label {
                display: flex;
                align-items: center;
                gap: 9px;
                font-size: 0.76rem;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                font-weight: 800;
                color: #4f46e5;
                margin-bottom: 9px;
            }

            .ra-answer-meta-row {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                flex-wrap: wrap;
                margin-top: 14px;
                color: #64748b;
                font-size: 0.76rem;
            }

            .ra-latency-badge {
                display: inline-flex;
                align-items: center;
                padding: 5px 9px;
                border: 1px solid;
                border-radius: 999px;
                font-weight: 800;
                white-space: nowrap;
            }

            /* =========================================================
               Sources
               ========================================================= */

            .ra-source-box {
                border-radius: 16px;
                padding: 20px 22px;
                margin-bottom: 13px;
                background: rgba(255, 255, 255, 0.96);
                border: 1px solid #dbe3ee;
                box-shadow: 0 6px 20px rgba(15, 23, 42, 0.045);
                transition: box-shadow 0.15s ease, transform 0.15s ease;
            }

            .ra-source-box:hover {
                transform: translateY(-1px);
                box-shadow: 0 10px 25px rgba(15, 23, 42, 0.08);
                border-color: #c7d2fe;
            }

            .ra-source-top {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                margin-bottom: 8px;
            }

            .ra-source-number {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                min-width: 28px;
                height: 28px;
                padding: 0 8px;
                border-radius: 8px;
                background: #eef2ff;
                color: #4338ca;
                font-size: 0.75rem;
                font-weight: 800;
            }

            .ra-source-title {
                flex: 1;
                font-weight: 800;
                font-size: 1rem;
                color: #1e293b;
            }

            .ra-source-meta {
                color: #64748b;
                font-size: 0.76rem;
                margin: 8px 0 14px;
            }

            .ra-source-meta code {
                background: #f1f5f9;
                color: #475569;
                padding: 3px 7px;
                border-radius: 6px;
                border: 1px solid #e2e8f0;
            }

            .ra-source-question {
                font-weight: 750;
                font-size: 1rem;
                line-height: 1.5;
                margin: 4px 0 9px;
                color: #334155;
            }

            .ra-answer-text {
                color: #233047;
                font-size: 1.02rem;
                line-height: 1.72;
            }

            .ra-answer-text strong {
                color: #111827;
                font-weight: 800;
            }

            .ra-answer-text br + strong {
                display: inline-block;
                margin-top: 6px;
            }

            body:has(.ra-theme-marker-dark) .ra-answer-text strong {
                color: #f8fafc !important;
            }
            
            .ra-answer-text h1,
            .ra-answer-text h2,
            .ra-answer-text h3,
            .ra-answer-text h4 {
                font-size: 1.15rem !important;
                line-height: 1.35 !important;
                margin: 14px 0 8px !important;
            }

            .ra-source-text {
                color: #64748b;
                line-height: 1.7;
                font-size: 0.92rem;
            }

            /* =========================================================
               Premium micro-interactions
               ========================================================= */

            .ra-hero,
            .ra-product-tile,
            .ra-context-card,
            .ra-answer-box,
            .ra-source-box {
                transform: translateZ(0);
                transition:
                    transform 0.22s ease,
                    box-shadow 0.22s ease,
                    border-color 0.22s ease;
            }

            .ra-product-tile:hover,
            .ra-context-card:hover {
                transform: translateY(-3px);
                box-shadow: 0 16px 34px rgba(15,23,42,0.10);
            }

            body:has(.ra-theme-marker-dark) .ra-product-tile:hover,
            body:has(.ra-theme-marker-dark) .ra-context-card:hover {
                box-shadow: 0 18px 40px rgba(0,0,0,0.30);
            }

            .ra-hero:hover {
                box-shadow: 0 22px 54px rgba(15,23,42,0.19);
            }

            body:has(.ra-theme-marker-dark) .ra-hero:hover {
                box-shadow: 0 24px 58px rgba(0,0,0,0.34);
            }

            .stButton > button {
                position: relative;
                overflow: hidden;
            }

            .stButton > button::after {
                content: "";
                position: absolute;
                top: -120%;
                left: -35%;
                width: 28%;
                height: 340%;
                transform: rotate(20deg);
                background: linear-gradient(
                    90deg,
                    transparent,
                    rgba(255,255,255,0.26),
                    transparent
                );
                transition: left 0.55s ease;
                pointer-events: none;
            }

            .stButton > button:hover::after {
                left: 125%;
            }

            /* =========================================================
               Footer
               ========================================================= */

            .ra-footer-credit {
                display: inline-block;
                margin-left: 10px;
                padding-left: 10px;
                border-left: 1px solid #cbd5e1;
                color: #94a3b8;
            }

            body:has(.ra-theme-marker-dark) .ra-footer-credit {
                border-left-color: #334155;
                color: #64748b;
            }

            .ra-footer {
                margin-top: 34px;
                padding-top: 18px;
                border-top: 1px solid #e2e8f0;
                text-align: center;
                color: #94a3b8;
                font-size: 0.76rem;
            }

            /* =========================================================
               Responsive
               ========================================================= */

            @media (max-width: 768px) {
                .block-container {
                    padding-left: 1rem;
                    padding-right: 1rem;
                    padding-top: 1.2rem;
                }

                .ra-hero {
                    padding: 24px 20px 76px;
                    border-radius: 18px;
                }

                .ra-status-row,
                .ra-status-strip {
                    left: 20px;
                    bottom: -18px;
                }

                .ra-brand-title {
                    font-size: 1.55rem;
                }

                .ra-helper {
                    font-size: 0.82rem;
                }

                .ra-answer-box,
                .ra-source-box {
                    padding: 17px 16px;
                }
            }

            /* =========================================================
               Premium customer-facing refresh
               ========================================================= */

            .block-container {
                max-width: 1240px !important;
                padding-top: 2.25rem !important;
            }

            .ra-hero {
                padding: 30px 34px 34px !important;
                margin-bottom: 18px !important;
                border-radius: 26px !important;
                background:
                    radial-gradient(circle at 92% 10%, rgba(129, 140, 248, 0.34), transparent 28%),
                    radial-gradient(circle at 14% 0%, rgba(56, 189, 248, 0.16), transparent 24%),
                    linear-gradient(135deg, #0b1220 0%, #101a31 52%, #1b2350 100%) !important;
                box-shadow: 0 24px 70px rgba(15, 23, 42, 0.20) !important;
            }

            .ra-hero .ra-brand-title {
                font-size: 2.18rem !important;
                letter-spacing: -0.045em !important;
            }

            .ra-hero .ra-brand-subtitle {
                max-width: 760px;
                font-size: 1rem !important;
                line-height: 1.55 !important;
                color: #d7def0 !important;
            }

            .ra-status-strip {
                position: static !important;
                margin-top: 26px;
                pointer-events: auto !important;
            }

            .ra-status {
                padding: 8px 12px !important;
                background: rgba(255,255,255,0.08) !important;
                border-color: rgba(255,255,255,0.14) !important;
                color: #e8eefc !important;
            }

            .ra-product-strip {
                display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 12px;
                margin: 14px 0 26px;
            }

            .ra-product-tile {
                padding: 14px 16px;
                border-radius: 16px;
                border: 1px solid rgba(148,163,184,0.22);
                background: rgba(255,255,255,0.72);
                box-shadow: 0 10px 30px rgba(15,23,42,0.05);
                backdrop-filter: blur(12px);
                -webkit-backdrop-filter: blur(12px);
            }

            .ra-product-kicker {
                font-size: 0.68rem;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                font-weight: 800;
                color: #64748b;
                margin-bottom: 4px;
            }

            .ra-product-value {
                font-size: 0.92rem;
                font-weight: 760;
                color: #0f172a;
            }

            div[data-testid="stRadio"] {
                padding: 6px;
                border: 1px solid #dbe3ee;
                border-radius: 20px;
                background: rgba(255,255,255,0.78);
                box-shadow: 0 10px 32px rgba(15,23,42,0.05);
                backdrop-filter: blur(10px);
                -webkit-backdrop-filter: blur(10px);
            }

            div[data-testid="stRadio"] > label {
                display: none;
            }

            div[data-testid="stRadio"] div[role="radiogroup"] {
                display: grid;
                grid-template-columns: repeat(3, minmax(0,1fr));
                gap: 6px;
                width: 100%;
            }

            div[data-testid="stRadio"] div[role="radiogroup"] label {
                min-height: 78px;
                border-radius: 15px;
                padding: 10px 12px;
                border: 1px solid transparent;
                background: transparent;
                align-items: center;
                transition: all 0.16s ease;
            }

            div[data-testid="stRadio"] div[role="radiogroup"] label:hover {
                background: #f8fafc;
                border-color: #e2e8f0;
            }

            div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) {
                background: linear-gradient(135deg, #eef2ff 0%, #f5f3ff 100%);
                border-color: #c7d2fe;
                box-shadow: 0 8px 22px rgba(79,70,229,0.10);
            }

            div[data-testid="stRadio"] div[role="radiogroup"] p {
                margin: 0 !important;
            }

            div[data-testid="stRadio"] div[role="radiogroup"] label span {
                font-weight: 760;
                color: #1e293b;
            }

            .ra-context-card {
                margin-top: 12px;
                padding: 13px 16px;
                border-radius: 15px;
                background: linear-gradient(135deg, #f8fafc 0%, #f5f3ff 100%);
                border: 1px solid #e2e8f0;
                color: #475569;
                font-size: 0.88rem;
                line-height: 1.55;
            }

            .ra-section-label {
                margin-top: 18px !important;
            }

            div[data-testid="stFileUploader"] {
                border-radius: 16px;
            }

            .ra-answer-box {
                border-radius: 20px !important;
                padding: 24px 26px !important;
                background:
                    radial-gradient(circle at 100% 0%, rgba(129,140,248,0.13), transparent 34%),
                    linear-gradient(135deg, #ffffff 0%, #f7f8ff 100%) !important;
                border: 1px solid #d8def2 !important;
                box-shadow: 0 14px 38px rgba(49,46,129,0.08) !important;
            }

            .ra-answer-label {
                color: #4f46e5 !important;
            }

            .ra-source-box {
                border-radius: 18px !important;
                background: rgba(255,255,255,0.88) !important;
                box-shadow: 0 9px 28px rgba(15,23,42,0.05) !important;
            }

            .ra-source-box:hover {
                transform: translateY(-2px) !important;
            }

            .ra-footer {
                padding-bottom: 12px;
            }

            @media (max-width: 820px) {
                .ra-product-strip,
                div[data-testid="stRadio"] div[role="radiogroup"] {
                    grid-template-columns: 1fr;
                }

                div[data-testid="stRadio"] div[role="radiogroup"] label {
                    min-height: 58px;
                }
            }

            /* =========================================================
               Theme system
               ========================================================= */

            /* Compact theme switcher: one clean icon button beside Deploy. */
            .st-key-supportai_theme_toggle {
                position: fixed !important;
                top: 9px !important;
                right: clamp(112px, 6.8vw, 142px) !important;
                z-index: 999999 !important;
                width: 42px !important;
                min-width: 42px !important;
                padding: 0 !important;
                margin: 0 !important;
            }

            .st-key-supportai_theme_toggle > div {
                width: 42px !important;
            }

            .st-key-supportai_theme_toggle .stButton,
            .st-key-supportai_theme_toggle .stButton > div {
                width: 42px !important;
            }

            .st-key-supportai_theme_toggle .stButton > button {
                width: 42px !important;
                height: 34px !important;
                min-height: 34px !important;
                padding: 0 !important;
                border-radius: 999px !important;
                border: 1px solid rgba(148,163,184,0.28) !important;
                background: rgba(255,255,255,0.93) !important;
                color: #334155 !important;
                box-shadow:
                    0 7px 20px rgba(15,23,42,0.15),
                    inset 0 1px 0 rgba(255,255,255,0.85) !important;
                backdrop-filter: blur(14px);
                -webkit-backdrop-filter: blur(14px);
                transition: transform 0.16s ease, box-shadow 0.18s ease, background 0.18s ease !important;
            }

            .st-key-supportai_theme_toggle .stButton > button:hover {
                transform: translateY(-1px) scale(1.04) !important;
                box-shadow:
                    0 10px 24px rgba(15,23,42,0.20),
                    0 0 0 3px rgba(99,102,241,0.08) !important;
            }

            .st-key-supportai_theme_toggle .stButton > button span {
                font-size: 0.92rem !important;
                line-height: 1 !important;
                font-weight: 850 !important;
            }

            body:has(.ra-theme-marker-dark) .st-key-supportai_theme_toggle .stButton > button {
                background: rgba(12,18,31,0.92) !important;
                color: #e2e8f0 !important;
                border-color: rgba(148,163,184,0.24) !important;
                box-shadow:
                    0 9px 24px rgba(0,0,0,0.34),
                    inset 0 1px 0 rgba(255,255,255,0.05) !important;
            }

            body:has(.ra-theme-marker-dark) .st-key-supportai_theme_toggle .stButton > button:hover {
                box-shadow:
                    0 11px 28px rgba(0,0,0,0.40),
                    0 0 0 3px rgba(129,140,248,0.10) !important;
            }

            @media (max-width: 700px) {
                .st-key-supportai_theme_toggle {
                    top: 8px !important;
                    right: 70px !important;
                }
                .st-key-supportai_theme_toggle .stButton > button {
                    width: 38px !important;
                    height: 32px !important;
                    min-height: 32px !important;
                }
                .st-key-supportai_theme_toggle,
                .st-key-supportai_theme_toggle > div,
                .st-key-supportai_theme_toggle .stButton,
                .st-key-supportai_theme_toggle .stButton > div {
                    width: 38px !important;
                }
            }

            /* ---------- Dark mode ---------- */

            body:has(.ra-theme-marker-dark) .stApp {
                color-scheme: dark;
                background:
                    radial-gradient(circle at 8% 4%, rgba(99,102,241,0.25), transparent 26%),
                    radial-gradient(circle at 94% 14%, rgba(56,189,248,0.16), transparent 28%),
                    radial-gradient(circle at 50% 92%, rgba(168,85,247,0.16), transparent 34%),
                    linear-gradient(135deg, #060b17 0%, #0b1120 44%, #10182c 100%) !important;
            }

            body:has(.ra-theme-marker-dark) .main .block-container::before {
                background-image: radial-gradient(rgba(129,140,248,0.11) 1px, transparent 1px);
                opacity: 0.75;
            }

            body:has(.ra-theme-marker-dark) .ra-section-label,
            body:has(.ra-theme-marker-dark) .ra-theme-label {
                color: #93a4c5 !important;
            }

            body:has(.ra-theme-marker-dark) .ra-section-title {
                color: #f8fafc !important;
            }

            body:has(.ra-theme-marker-dark) .ra-helper {
                color: #9aa9c2 !important;
            }

            body:has(.ra-theme-marker-dark) .ra-product-tile {
                background: rgba(15,23,42,0.78) !important;
                border-color: rgba(148,163,184,0.17) !important;
                box-shadow: 0 14px 34px rgba(0,0,0,0.22) !important;
            }

            body:has(.ra-theme-marker-dark) .ra-product-kicker {
                color: #94a3b8 !important;
            }

            body:has(.ra-theme-marker-dark) .ra-product-value {
                color: #eef2ff !important;
            }

            body:has(.ra-theme-marker-dark) div[data-testid="stRadio"] {
                background: rgba(15,23,42,0.76) !important;
                border-color: rgba(148,163,184,0.18) !important;
                box-shadow: 0 14px 38px rgba(0,0,0,0.25) !important;
            }

            body:has(.ra-theme-marker-dark) div[data-testid="stRadio"] div[role="radiogroup"] label:hover {
                background: rgba(30,41,59,0.92) !important;
                border-color: rgba(129,140,248,0.22) !important;
            }

            body:has(.ra-theme-marker-dark) div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) {
                background: linear-gradient(135deg, rgba(79,70,229,0.26), rgba(99,102,241,0.16)) !important;
                border-color: rgba(129,140,248,0.45) !important;
                box-shadow: 0 10px 28px rgba(79,70,229,0.18) !important;
            }

            body:has(.ra-theme-marker-dark) div[data-testid="stRadio"] div[role="radiogroup"] label span {
                color: #e5e7eb !important;
            }

            body:has(.ra-theme-marker-dark) .ra-context-card {
                background: linear-gradient(135deg, rgba(15,23,42,0.88), rgba(30,27,75,0.64)) !important;
                border-color: rgba(129,140,248,0.22) !important;
                color: #b9c4da !important;
            }

            body:has(.ra-theme-marker-dark) div[data-testid="stFileUploader"] {
                color: #dbe4f5 !important;
            }

            body:has(.ra-theme-marker-dark) div[data-testid="stFileUploader"] section,
            body:has(.ra-theme-marker-dark) div[data-testid="stTextArea"],
            body:has(.ra-theme-marker-dark) .ra-source-box {
                background: rgba(15,23,42,0.78) !important;
                border-color: rgba(148,163,184,0.18) !important;
                box-shadow: 0 12px 34px rgba(0,0,0,0.22) !important;
            }

            body:has(.ra-theme-marker-dark) textarea {
                background: #0b1220 !important;
                color: #f8fafc !important;
                border-color: #334155 !important;
            }

            body:has(.ra-theme-marker-dark) textarea::placeholder {
                color: #71809a !important;
            }

            body:has(.ra-theme-marker-dark) div[data-testid="stTextArea"] label,
            body:has(.ra-theme-marker-dark) .ra-source-question {
                color: #dbe5f6 !important;
            }

            body:has(.ra-theme-marker-dark) .ra-source-title {
                color: #f1f5f9 !important;
            }

            body:has(.ra-theme-marker-dark) .ra-source-meta,
            body:has(.ra-theme-marker-dark) .ra-source-text {
                color: #94a3b8 !important;
            }

            body:has(.ra-theme-marker-dark) .ra-source-meta code {
                background: #111827 !important;
                border-color: #334155 !important;
                color: #cbd5e1 !important;
            }

            body:has(.ra-theme-marker-dark) .ra-source-number {
                background: rgba(79,70,229,0.20) !important;
                color: #c7d2fe !important;
            }

            body:has(.ra-theme-marker-dark) .ra-answer-box {
                background:
                    radial-gradient(circle at 100% 0%, rgba(129,140,248,0.22), transparent 34%),
                    linear-gradient(135deg, rgba(17,24,39,0.96), rgba(20,24,54,0.96)) !important;
                border-color: rgba(129,140,248,0.27) !important;
                box-shadow: 0 16px 44px rgba(0,0,0,0.30) !important;
                color: #e5e7eb !important;
            }

            body:has(.ra-theme-marker-dark) .ra-answer-text {
                color: #e2e8f0 !important;
            }

            body:has(.ra-theme-marker-dark) .ra-answer-meta-row {
                color: #94a3b8 !important;
            }

            body:has(.ra-theme-marker-dark) .ra-processing {
                background: rgba(15,23,42,0.84) !important;
                border-color: rgba(148,163,184,0.18) !important;
                color: #cbd5e1 !important;
            }

            body:has(.ra-theme-marker-dark) .ra-footer {
                border-top-color: rgba(148,163,184,0.14) !important;
                color: #64748b !important;
            }

            body:has(.ra-theme-marker-dark) .stMarkdown,
            body:has(.ra-theme-marker-dark) p,
            body:has(.ra-theme-marker-dark) label {
                color: #dbe5f4;
            }

            /* ---------- Softer, richer light mode ---------- */

            body:has(.ra-theme-marker-light) .stApp {
                background:
                    radial-gradient(circle at 7% 8%, rgba(99,102,241,0.18), transparent 27%),
                    radial-gradient(circle at 93% 12%, rgba(14,165,233,0.18), transparent 28%),
                    radial-gradient(circle at 36% 86%, rgba(236,72,153,0.08), transparent 26%),
                    radial-gradient(circle at 68% 72%, rgba(168,85,247,0.12), transparent 30%),
                    linear-gradient(135deg, #eaf0ff 0%, #f7f2ff 44%, #eaf8fb 100%) !important;
            }

            body:has(.ra-theme-marker-light) .main .block-container::before {
                opacity: 0.95;
            }

            /* Theme controls use the same premium button language as the rest of the UI. */

            .ra-theme-marker-light,
            .ra-theme-marker-dark {
                display: none !important;
            }

            .ra-theme-label {
                margin-bottom: 6px;
                text-align: right;
            }

            body:has(.ra-theme-marker-dark) .stButton > button:not([kind="primary"]) {
                background: rgba(15,23,42,0.74) !important;
                color: #dbe5f4 !important;
                border-color: rgba(148,163,184,0.20) !important;
            }

            body:has(.ra-theme-marker-dark) .stTabs [data-baseweb="tab-list"] {
                border-bottom-color: rgba(148,163,184,0.18) !important;
            }

            body:has(.ra-theme-marker-dark) .stTabs [data-baseweb="tab"] {
                color: #aab7cc !important;
            }

            body:has(.ra-theme-marker-dark) .stTabs [aria-selected="true"] {
                color: #eef2ff !important;
            }


            /* =========================================================
               Premium interaction layer / native Streamlit controls
               ========================================================= */

            .main .block-container {
                transition: background 0.35s ease, color 0.35s ease;
            }

            .ra-hero,
            .ra-product-tile,
            .ra-context-card,
            .ra-answer-box,
            .ra-source-box,
            .ra-processing {
                transition: background 0.35s ease, border-color 0.25s ease,
                            box-shadow 0.25s ease, transform 0.2s ease;
            }

            .ra-product-tile {
                position: relative;
                overflow: hidden;
                cursor: default;
            }

            .ra-product-tile::after {
                content: "";
                position: absolute;
                width: 120px;
                height: 120px;
                right: -44px;
                top: -48px;
                border-radius: 50%;
                background: radial-gradient(circle, rgba(99,102,241,0.18), transparent 68%);
                pointer-events: none;
            }

            .ra-product-tile:hover {
                transform: translateY(-3px);
                box-shadow: 0 16px 35px rgba(15,23,42,0.10) !important;
            }

            .ra-context-card {
                position: relative;
                overflow: hidden;
            }

            .ra-context-card::before {
                content: "";
                position: absolute;
                inset: 0 auto 0 0;
                width: 3px;
                background: linear-gradient(180deg, #6366f1, #06b6d4);
                border-radius: 3px;
            }

            /* Inputs / Selectboxes */
            div[data-testid="stSelectbox"] label,
            div[data-testid="stMultiSelect"] label,
            div[data-testid="stCheckbox"] label,
            div[data-testid="stFileUploader"] label,
            div[data-testid="stTextArea"] label {
                font-weight: 700 !important;
                letter-spacing: 0.01em;
            }

            div[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
            div[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
                min-height: 44px;
                border-radius: 12px !important;
                border: 1px solid #d6deeb !important;
                background: rgba(255,255,255,0.92) !important;
                box-shadow: 0 6px 20px rgba(15,23,42,0.035);
            }

            div[data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover,
            div[data-testid="stMultiSelect"] div[data-baseweb="select"] > div:hover {
                border-color: #9da9f5 !important;
                box-shadow: 0 8px 24px rgba(79,70,229,0.08);
            }

            /* Menus / popovers */
            div[data-baseweb="popover"],
            div[data-baseweb="menu"] {
                border-radius: 14px !important;
                box-shadow: 0 18px 45px rgba(15,23,42,0.18) !important;
                overflow: hidden !important;
            }

            [role="option"] {
                min-height: 38px;
            }

            [role="option"]:hover {
                background: rgba(99,102,241,0.10) !important;
            }

            /* File uploader */
            div[data-testid="stFileUploader"] section {
                border-radius: 16px !important;
                border: 1px dashed #b8c2d6 !important;
                background: rgba(255,255,255,0.72) !important;
                box-shadow: inset 0 1px 0 rgba(255,255,255,0.8);
                transition: all 0.2s ease;
            }

            div[data-testid="stFileUploader"] section:hover {
                border-color: #818cf8 !important;
                background: rgba(99,102,241,0.045) !important;
            }

            /* Tabs */
            .stTabs [data-baseweb="tab-list"] {
                gap: 6px;
                padding-bottom: 8px;
                border-bottom: 1px solid #dbe3ee;
            }

            .stTabs [data-baseweb="tab"] {
                border-radius: 10px 10px 0 0;
                padding: 9px 13px;
                font-weight: 700;
                transition: all 0.18s ease;
            }

            .stTabs [data-baseweb="tab"]:hover {
                background: rgba(99,102,241,0.07);
            }

            /* Expanders */
            details[data-testid="stExpander"] {
                border-radius: 14px !important;
                border: 1px solid #dbe3ee !important;
                overflow: hidden;
                background: rgba(255,255,255,0.66);
            }

            /* Buttons */
            .stButton > button {
                position: relative;
                overflow: hidden;
            }

            .stButton > button::after {
                content: "";
                position: absolute;
                inset: 0;
                background: linear-gradient(105deg, transparent 34%, rgba(255,255,255,0.18) 50%, transparent 66%);
                transform: translateX(-130%);
                transition: transform 0.55s ease;
                pointer-events: none;
            }

            .stButton > button:hover::after {
                transform: translateX(130%);
            }

            /* Dark: native Streamlit controls */
            body:has(.ra-theme-marker-dark) div[data-testid="stSelectbox"] label,
            body:has(.ra-theme-marker-dark) div[data-testid="stMultiSelect"] label,
            body:has(.ra-theme-marker-dark) div[data-testid="stCheckbox"] label,
            body:has(.ra-theme-marker-dark) div[data-testid="stFileUploader"] label,
            body:has(.ra-theme-marker-dark) div[data-testid="stTextArea"] label {
                color: #e2e8f0 !important;
            }

            body:has(.ra-theme-marker-dark) div[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
            body:has(.ra-theme-marker-dark) div[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
                background: rgba(15,23,42,0.92) !important;
                border-color: #334155 !important;
                color: #f8fafc !important;
                box-shadow: 0 8px 24px rgba(0,0,0,0.18) !important;
            }

            body:has(.ra-theme-marker-dark) div[data-testid="stSelectbox"] [data-baseweb="select"] *,
            body:has(.ra-theme-marker-dark) div[data-testid="stMultiSelect"] [data-baseweb="select"] * {
                color: #e5e7eb !important;
            }

            body:has(.ra-theme-marker-dark) div[data-baseweb="popover"],
            body:has(.ra-theme-marker-dark) div[data-baseweb="menu"] {
                background: #0f172a !important;
                border: 1px solid #334155 !important;
            }

            body:has(.ra-theme-marker-dark) [role="option"] {
                color: #e5e7eb !important;
            }

            body:has(.ra-theme-marker-dark) [role="option"]:hover {
                background: #1e293b !important;
            }

            body:has(.ra-theme-marker-dark) div[data-testid="stFileUploader"] section {
                background: linear-gradient(135deg, rgba(15,23,42,0.90), rgba(23,24,65,0.76)) !important;
                border-color: #475569 !important;
            }

            body:has(.ra-theme-marker-dark) div[data-testid="stFileUploader"] section:hover {
                border-color: #818cf8 !important;
                background: rgba(79,70,229,0.10) !important;
            }

            /* Dark uploader internals: Streamlit keeps its own native button colors. */
            body:has(.ra-theme-marker-dark) div[data-testid="stFileUploader"] button {
                background: #1b2640 !important;
                color: #e8eef9 !important;
                border: 1px solid #4b5f82 !important;
                box-shadow: 0 5px 14px rgba(0,0,0,0.18) !important;
            }

            body:has(.ra-theme-marker-dark) div[data-testid="stFileUploader"] button:hover {
                background: #243250 !important;
                border-color: #818cf8 !important;
                color: #ffffff !important;
            }

            body:has(.ra-theme-marker-dark) div[data-testid="stFileUploader"] button svg {
                fill: currentColor !important;
                color: currentColor !important;
            }

            body:has(.ra-theme-marker-dark) div[data-testid="stFileUploader"] [data-testid="stFileUploaderDropzoneInstructions"] {
                color: #aebbd0 !important;
            }

            body:has(.ra-theme-marker-dark) div[data-testid="stFileUploader"] [data-testid="stFileUploaderDropzoneInstructions"] span,
            body:has(.ra-theme-marker-dark) div[data-testid="stFileUploader"] small {
                color: #93a4bf !important;
            }

            body:has(.ra-theme-marker-dark) .stTabs [data-baseweb="tab-list"] {
                border-bottom-color: #243047 !important;
            }

            body:has(.ra-theme-marker-dark) .stTabs [data-baseweb="tab"] {
                color: #b7c3d8 !important;
            }

            body:has(.ra-theme-marker-dark) .stTabs [data-baseweb="tab"]:hover {
                background: rgba(99,102,241,0.10) !important;
                color: #f8fafc !important;
            }

            body:has(.ra-theme-marker-dark) details[data-testid="stExpander"] {
                background: rgba(15,23,42,0.72) !important;
                border-color: #2c3a52 !important;
            }

            body:has(.ra-theme-marker-dark) details[data-testid="stExpander"] summary,
            body:has(.ra-theme-marker-dark) details[data-testid="stExpander"] summary * {
                color: #e5e7eb !important;
            }

            /* Dark data grid frame */
            body:has(.ra-theme-marker-dark) div[data-testid="stDataFrame"] {
                border: 1px solid #2a3750 !important;
                border-radius: 14px !important;
                overflow: hidden !important;
                box-shadow: 0 12px 30px rgba(0,0,0,0.20);
                background: #0f172a !important;
            }

            /* Dark checkbox */
            body:has(.ra-theme-marker-dark) div[data-testid="stCheckbox"] span {
                color: #dbe5f4 !important;
            }

            /* Theme-aware typography for native markdown */
            body:has(.ra-theme-marker-dark) .stMarkdown,
            body:has(.ra-theme-marker-dark) .stMarkdown p,
            body:has(.ra-theme-marker-dark) .stMarkdown li,
            body:has(.ra-theme-marker-dark) .stCaption,
            body:has(.ra-theme-marker-dark) small {
                color: #cbd5e1;
            }

            /* =========================================================
               Premium background ambiance
               ========================================================= */

            .ra-bg-ambience {
                position: fixed;
                inset: 0;
                pointer-events: none;
                z-index: -1;
                overflow: hidden;
            }

            .ra-bg-ambience::before,
            .ra-bg-ambience::after {
                content: "";
                position: absolute;
                border-radius: 50%;
                filter: blur(34px);
                opacity: 0.28;
                animation: ra-ambient-float 16s ease-in-out infinite alternate;
            }

            .ra-bg-ambience::before {
                width: 360px;
                height: 360px;
                left: -120px;
                top: 24%;
                background: radial-gradient(circle, rgba(99,102,241,0.24), transparent 70%);
            }

            .ra-bg-ambience::after {
                width: 420px;
                height: 420px;
                right: -150px;
                bottom: 3%;
                background: radial-gradient(circle, rgba(6,182,212,0.18), transparent 70%);
                animation-delay: -5s;
            }

            @keyframes ra-ambient-float {
                from { transform: translate3d(0,0,0) scale(0.96); }
                to { transform: translate3d(30px,-18px,0) scale(1.06); }
            }

            body:has(.ra-theme-marker-dark) .ra-bg-ambience::before {
                background: radial-gradient(circle, rgba(99,102,241,0.24), transparent 70%);
            }

            body:has(.ra-theme-marker-dark) .ra-bg-ambience::after {
                background: radial-gradient(circle, rgba(34,211,238,0.15), transparent 70%);
            }

            @media (prefers-reduced-motion: reduce) {
                .ra-bg-ambience::before,
                .ra-bg-ambience::after,
                .ra-hero::before,
                .ra-hero::after {
                    animation: none !important;
                }

                .ra-product-tile,
                .ra-source-box,
                .stButton > button {
                    transition: none !important;
                }
            }


            /* Document inventory table */
            .ra-doc-table-wrap {
                margin-top: 14px;
                border: 1px solid #dbe3ee;
                border-radius: 16px;
                overflow: auto;
                background: rgba(255,255,255,0.68);
                box-shadow: 0 10px 28px rgba(15,23,42,0.045);
            }

            .ra-doc-table {
                width: 100%;
                min-width: 920px;
                border-collapse: separate;
                border-spacing: 0;
                font-size: 0.78rem;
                color: #475569;
            }

            .ra-doc-table th {
                position: sticky;
                top: 0;
                z-index: 1;
                text-align: left;
                padding: 11px 12px;
                background: linear-gradient(180deg, #f8fafc, #eef2f7);
                color: #64748b;
                font-size: 0.67rem;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                font-weight: 850;
                border-bottom: 1px solid #dbe3ee;
                white-space: nowrap;
            }

            .ra-doc-table td {
                padding: 10px 12px;
                border-bottom: 1px solid #e8edf4;
                white-space: nowrap;
                vertical-align: middle;
            }

            .ra-doc-table tr:last-child td { border-bottom: 0; }
            .ra-doc-table tbody tr { transition: background 0.16s ease, transform 0.16s ease; }
            .ra-doc-table tbody tr:hover { background: rgba(99,102,241,0.055); }
            .ra-doc-name { color: #1e293b; font-weight: 700; max-width: 260px; overflow: hidden; text-overflow: ellipsis; }

            .ra-doc-status,
            .ra-doc-active {
                display: inline-flex;
                align-items: center;
                gap: 5px;
                padding: 4px 8px;
                border-radius: 999px;
                font-size: 0.67rem;
                font-weight: 800;
            }

            .ra-doc-status.indexed { color: #166534; background: #dcfce7; border: 1px solid #bbf7d0; }
            .ra-doc-status.other { color: #92400e; background: #fef3c7; border: 1px solid #fde68a; }
            .ra-active-dot, .ra-inactive-dot { width: 6px; height: 6px; border-radius: 50%; display:inline-block; }
            .ra-active-dot { background: #22c55e; box-shadow: 0 0 0 3px rgba(34,197,94,0.12); }
            .ra-inactive-dot { background: #94a3b8; }

            body:has(.ra-theme-marker-dark) .ra-doc-table-wrap {
                background: rgba(15,23,42,0.82);
                border-color: #293750;
                box-shadow: 0 12px 32px rgba(0,0,0,0.22);
            }

            body:has(.ra-theme-marker-dark) .ra-doc-table {
                color: #aebbd0;
            }

            body:has(.ra-theme-marker-dark) .ra-doc-table th {
                background: linear-gradient(180deg, #172136, #121b2d);
                color: #91a0b8;
                border-bottom-color: #2a3850;
            }

            body:has(.ra-theme-marker-dark) .ra-doc-table td {
                border-bottom-color: #1e2a3e;
            }

            body:has(.ra-theme-marker-dark) .ra-doc-table tbody tr:hover {
                background: rgba(99,102,241,0.08);
            }

            body:has(.ra-theme-marker-dark) .ra-doc-name {
                color: #e6edf8;
            }

            body:has(.ra-theme-marker-dark) .ra-doc-status.indexed {
                color: #bbf7d0;
                background: rgba(22,101,52,0.22);
                border-color: rgba(34,197,94,0.22);
            }

            body:has(.ra-theme-marker-dark) .ra-doc-status.other {
                color: #fde68a;
                background: rgba(146,64,14,0.20);
                border-color: rgba(245,158,11,0.22);
            }

            </style>
            """
        ),
        unsafe_allow_html=True,
    )


def render_response(
    response: RAGResponse,
    question: str,
    provider: str = "",
    latency_seconds: float | None = None,
) -> None:
    """
    Render the final answer and one relevant FAQ section per retrieved source.
    No retrieval or generation behavior is changed here.
    """

    # ---------------------------------------------------------
    # Answer
    # ---------------------------------------------------------

    st.markdown(
        '<div class="ra-section-label">Response</div>'
        '<div class="ra-section-title">Answer</div>',
        unsafe_allow_html=True,
    )

    answer = clean_answer(response.answer, question)
    if not answer:
        answer = "I couldn't find a policy that answers that question."

    provider_label = provider.strip() if provider else "Unknown"

    if latency_seconds is None:
        latency_html = ""
    else:
        try:
            latency_value = float(latency_seconds)
        except (TypeError, ValueError):
            latency_value = 0.0

        if latency_value < 2.0:
            latency_color = "#16a34a"
            latency_bg = "#f0fdf4"
            latency_border = "#bbf7d0"
        elif latency_value < 5.0:
            latency_color = "#d97706"
            latency_bg = "#fffbeb"
            latency_border = "#fde68a"
        else:
            latency_color = "#dc2626"
            latency_bg = "#fef2f2"
            latency_border = "#fecaca"

        latency_html = (
            f'<span class="ra-latency-badge" '
            f'style="color:{latency_color};background:{latency_bg};'
            f'border-color:{latency_border};">'
            f"Latency: {latency_value:.2f}s"
            f"</span>"
        )

    answer_html = (
        '<div class="ra-answer-box">'
        '<div class="ra-answer-label">POLICY-GROUNDED RESPONSE</div>'
        f'<div class="ra-answer-text">{html.escape(answer)}</div>'
        f'<div class="ra-answer-meta-row">'
        f"<span>Generated by: <strong>{html.escape(provider_label)}</strong></span>"
        f"{latency_html}"
        "</div>"
        "</div>"
    )
    st.markdown(answer_html, unsafe_allow_html=True)

    if not response.evidence:
        return

    # ---------------------------------------------------------
    # Sources
    # ---------------------------------------------------------

    st.markdown(
        '<div class="ra-section-label">Evidence</div>'
        '<div class="ra-section-title">Supporting sources</div>',
        unsafe_allow_html=True,
    )

    shown_sources = set()

    # Display only the 3 most relevant evidence chunks.
    display_evidence = (response.evidence or [])[:3]

    for source_number, chunk in enumerate(display_evidence, start=1):
        source_question, source_text = extract_relevant_section(
            question,
            chunk.chunk_text,
        )

        source_key = (
            str(chunk.document_id),
            str(chunk.chunk_index),
            source_question.strip().lower(),
        )

        if source_key in shown_sources:
            continue

        shown_sources.add(source_key)

        document_name = html.escape(str(chunk.document_name))
        category = html.escape(str(chunk.category))
        chunk_index = html.escape(str(chunk.chunk_index))

        page_number = getattr(chunk, "page_number", None)
        section_heading = getattr(chunk, "section_heading", None)
        source_type = str(getattr(chunk, "source_type", "") or "").upper()

        safe_question = html.escape(source_question)
        safe_text = html.escape(source_text).replace("\n", "<br>")

        # ---------------------------------------------------------
        # Page / section metadata
        # ---------------------------------------------------------

        metadata_parts = [f"Category <code>{category}</code>"]

        # Show page number for uploaded PDF/DOCX documents.
        if (
            source_type == "USER_UPLOAD"
            and page_number is not None
            and str(document_name).lower().endswith((".pdf", ".docx"))
        ):
            metadata_parts.append(
                f'<span style="margin-left:10px;">Page <code>{html.escape(str(page_number))}</code></span>'
            )

        # Show section heading when available.
        if section_heading and str(section_heading).strip():
            safe_section = html.escape(str(section_heading).strip())
            metadata_parts.append(
                f'<span style="margin-left:10px;">Section <code>{safe_section}</code></span>'
            )

        metadata_parts.append(
            f'<span style="margin-left:10px;">Chunk <code>{chunk_index}</code></span>'
        )

        source_metadata_html = "".join(metadata_parts)

        source_html = (
            '<div class="ra-source-box">'
            '<div class="ra-source-top">'
            f'<span class="ra-source-number">SOURCE {source_number}</span>'
            f'<div class="ra-source-title">{document_name}</div>'
            "</div>"
            f'<div class="ra-source-meta">{source_metadata_html}</div>'
            f'<div class="ra-source-question">{safe_question}</div>'
            f'<div class="ra-source-text">{safe_text}</div>'
            "</div>"
        )

        st.markdown(source_html, unsafe_allow_html=True)


def render_analytics_response(
    response: CortexAnalystResponse,
    question: str,
    latency_seconds: float | None = None,
) -> None:
    """Render a Cortex Analyst answer and its generated SQL."""
    st.markdown(
        '<div class="ra-section-label">Response</div>'
        '<div class="ra-section-title">Support Analytics</div>',
        unsafe_allow_html=True,
    )

    answer = str(response.answer or "").strip()

    # Use the executed Cortex Analyst result as the user-facing answer.
    result = response.result or []

    if result:
        first_row = result[0]
        keys = {str(key).lower(): key for key in first_row.keys()}

        # Handle monthly ticket-volume results.
        month_key = next(
            (keys[key] for key in keys if "month" in key),
            None,
        )

        count_key = next(
            (keys[key] for key in keys if "count" in key or "ticket_count" in key),
            None,
        )

        if month_key and count_key and len(result) > 1:
            lines = ["Monthly ticket volume:"]

            for row in result:
                month = row.get(month_key)
                count = row.get(count_key)

                if hasattr(month, "strftime"):
                    month = month.strftime("%B %Y")

                lines.append(f"{month}: {count} tickets")

            answer = "\n".join(lines)

        # Handle single-row results such as highest/lowest category.
        elif len(result) == 1:
            values = []

            for key, value in first_row.items():
                clean_key = str(key).replace("_", " ").title()
                values.append(f"{clean_key}: {value}")

            answer = ", ".join(values)

        # Generic multi-row fallback.
        else:
            lines = []

            for row in result:
                row_values = []

                for key, value in row.items():
                    clean_key = str(key).replace("_", " ").title()
                    row_values.append(f"{clean_key}: {value}")

                lines.append(", ".join(row_values))

            answer = "\n".join(lines)

    st.markdown(
        '<div class="ra-answer-box">'
        '<div class="ra-answer-label">STRUCTURED ANALYTICS RESPONSE</div>'
        f'<div class="ra-answer-text">{html.escape(answer).replace(chr(10), "<br>")}</div>'
        + (
            f'<div class="ra-answer-meta-row"><span>Latency: '
            f"<strong>{float(latency_seconds):.2f}s</strong></span></div>"
            if latency_seconds is not None
            else ""
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    if response.sql:
        with st.expander("Generated SQL", expanded=False):
            st.code(response.sql, language="sql")

    if response.request_id:
        st.caption(f"Request ID: {response.request_id}")


def clean_agent_answer(answer: str) -> str:
    """
    Remove unnecessary process narration from the Cortex Agent's final answer.
    Keeps the actual policy/analytics response intact.
    """
    if not answer:
        return ""

    text = str(answer).strip()

    # Remove common process-narration sentences at the beginning.
    opening_patterns = [
        r"^\s*I'll look into .*?\.\s*",
        r"^\s*I will look into .*?\.\s*",
        r"^\s*Let me check .*?\.\s*",
        r"^\s*Let me .*?\.\s*",
        r"^\s*I'll pull .*?\.\s*",
        r"^\s*I have .*?\.\s*",
        r"^\s*Now I'll .*?\.\s*",
        r"^\s*Here's .*?\.\s*",
    ]

    for pattern in opening_patterns:
        text = re.sub(
            pattern,
            "",
            text,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )

    # For combined answers, remove everything before the first meaningful heading.
    heading_match = re.search(
        r"(?m)^\s*(\*\*[^*\n]+\*\*|#{1,6}\s+.+?)\s*$",
        text,
    )

    if heading_match and heading_match.start() > 0:
        prefix = text[: heading_match.start()].strip().lower()

        process_words = (
            "look into",
            "let me",
            "i have",
            "now i'll",
            "here's",
            "pull the",
            "query the",
            "full picture",
        )

        if any(word in prefix for word in process_words):
            text = text[heading_match.start() :].strip()

    # Remove a stray one-word continuation sometimes returned at the end
    # by the Cortex Agent (for example, a dangling "What").
    text = re.sub(
        r"(?m)\n\s*(?:What)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Remove timeout / continuation text if it appears at the end.
    text = re.sub(
        r"\s*I've reached the time limit for this request\..*?$",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    text = re.sub(
        r"\s*The analysis may be incomplete\..*?$",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    text = re.sub(
        r"\s*Would you like (?:me|to) .*?$",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    return text.strip()


def format_agent_answer_html(answer: str) -> str:
    """Render common Cortex Agent markdown as clean customer-facing HTML."""
    if not answer:
        return ""

    text = str(answer).strip()

    # Convert Markdown headings such as "## Refund Policy" into bold labels.
    text = re.sub(
        r"(?m)^\s*#{1,6}\s+(.+?)\s*$",
        r"<strong>\1</strong>",
        text,
    )

    # Convert bold Markdown into real HTML bold.
    text = re.sub(
        r"\*\*(.+?)\*\*",
        r"<strong>\1</strong>",
        text,
        flags=re.DOTALL,
    )

    # Remove remaining Markdown emphasis markers without changing the words.
    text = re.sub(r"(?<!\w)[*_](.+?)[*_](?!\w)", r"\1", text)

    # Render Markdown-style line breaks as HTML line breaks.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", "<br>")

    return text


def render_agent_response(
    response: CortexAgentResponse,
    latency_seconds: float | None = None,
) -> None:
    """Render a Snowflake Cortex Agent response and tool activity."""
    st.markdown(
        '<div class="ra-section-label">Response</div>'
        '<div class="ra-section-title">Cortex Agent</div>',
        unsafe_allow_html=True,
    )

    answer = clean_agent_answer(
        str(response.answer or "No agent response was returned.")
    )
    agent_answer_html = format_agent_answer_html(answer)

    st.markdown(
        '<div class="ra-answer-box">'
        '<div class="ra-answer-label">CORTEX AGENT RESPONSE</div>'
        f'<div class="ra-answer-text">{agent_answer_html}</div>'
        + (
            f'<div class="ra-answer-meta-row"><span>Latency: '
            f"<strong>{float(latency_seconds):.2f}s</strong></span></div>"
            if latency_seconds is not None
            else ""
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    with st.expander("Agent activity ", expanded=False):
        used_tools = getattr(response, "used_tools", None) or []

        if used_tools:
            for tool in used_tools:
                normalized = str(tool).strip().lower()

                if (
                    "policysearch" in normalized
                    or "cortex_search" in normalized
                    or "search" in normalized
                ):
                    st.write("✓ PolicySearch — Cortex Search")

                elif (
                    "supportanalytics" in normalized
                    or "cortex_analyst" in normalized
                    or "analyst" in normalized
                ):
                    st.write("✓ SupportAnalytics — Cortex Analyst")

                else:
                    st.write(f"✓ {tool}")
        else:
            st.caption("No tool activity was reported by the Cortex Agent.")

    if response.request_id:
        st.caption(f"Request ID: {response.request_id}")


def render_processing_indicator(message: str, container=None) -> None:
    """Render a lightweight inline spinner without dimming the page."""
    target = container if container is not None else st
    target.markdown(
        """<div class="ra-processing">
            <span class="ra-processing-spinner"></span>
            <span>{}</span>
        </div>""".format(html.escape(str(message))),
        unsafe_allow_html=True,
    )


def render_document_table(rows: list[dict]) -> None:
    """Render the document inventory as a theme-aware HTML table."""
    headers = [
        "Filename",
        "Type",
        "Category",
        "Status",
        "Pages",
        "Chunks",
        "Active",
        "Uploaded",
    ]
    header_html = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body_parts = []
    for row in rows:
        status = str(row.get("Status", ""))
        active = bool(row.get("Active", False))
        status_class = "indexed" if status.upper() == "INDEXED" else "other"
        active_html = (
            '<span class="ra-active-dot"></span>Active'
            if active
            else '<span class="ra-inactive-dot"></span>Inactive'
        )
        body_parts.append(
            "<tr>"
            f'<td class="ra-doc-name">{html.escape(str(row.get("Filename", "")))}</td>'
            f"<td>{html.escape(str(row.get('Type', '')))}</td>"
            f"<td>{html.escape(str(row.get('Category', '')))}</td>"
            f'<td><span class="ra-doc-status {status_class}">{html.escape(status)}</span></td>'
            f"<td>{html.escape(str(row.get('Pages', '')))}</td>"
            f"<td>{html.escape(str(row.get('Chunks', '')))}</td>"
            f'<td><span class="ra-doc-active">{active_html}</span></td>'
            f"<td>{html.escape(str(row.get('Uploaded', '')))}</td>"
            "</tr>"
        )

    table_html = (
        '<div class="ra-doc-table-wrap">'
        '<table class="ra-doc-table">'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{''.join(body_parts)}</tbody>"
        "</table>"
        "</div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)


def _render_document_management_impl(controller: RetailAssistController) -> None:
    """
    Render the document-management console.

    All document operations are delegated to DocumentStore so the UI only
    handles presentation and user actions.
    """
    store = getattr(controller, "document_store", None)
    if store is None:
        return

    st.markdown(
        """
        <div class="ra-section-label">Knowledge base management</div>
        <div class="ra-section-title">Document management</div>
        <div class="ra-helper">
            Review uploaded documents, inspect metadata, retry failed processing,
            re-index documents, or deactivate documents that should no longer
            be used as retrieval evidence.
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        col1, col2, col3 = st.columns(3)

        with col1:
            try:
                available_categories = store.list_categories()
            except Exception:
                available_categories = []

            category_filter = st.selectbox(
                "Category",
                ["All"] + available_categories,
                key="doc_category_filter",
            )

        with col2:
            status_filter = st.selectbox(
                "Status",
                [
                    "All",
                    "UPLOADING",
                    "PARSING",
                    "INDEXING",
                    "INDEXED",
                    "FAILED",
                    "DELETED",
                ],
                key="doc_status_filter",
            )

        with col3:
            active_only = st.checkbox(
                "Active documents only",
                value=False,
                key="doc_active_only",
            )

        try:
            documents = store.list_documents(
                category=category_filter,
                status=status_filter,
                active_only=active_only,
            )
        except Exception as exc:
            st.error(f"Unable to load document metadata: {exc}")
            return

        if not documents:
            st.info("No documents match the selected filters.")
            return

        rows = []
        for doc in documents:
            rows.append(
                {
                    "Filename": doc.get("ORIGINAL_FILENAME", ""),
                    "Type": str(doc.get("FILE_TYPE", "")).upper(),
                    "Category": doc.get("CATEGORY", ""),
                    "Status": doc.get("PROCESSING_STATUS", ""),
                    "Pages": doc.get("PAGE_COUNT", 0),
                    "Chunks": doc.get("CHUNK_COUNT", 0),
                    "Active": bool(doc.get("ACTIVE", False)),
                    "Uploaded": str(doc.get("CREATED_AT", "")),
                }
            )

        render_document_table(rows)

        labels = [
            f"{doc.get('ORIGINAL_FILENAME', '')} — "
            f"{doc.get('PROCESSING_STATUS', '')} — "
            f"{doc.get('DOCUMENT_ID', '')[:8]}"
            for doc in documents
        ]

        selected_label = st.selectbox(
            "Select a document",
            labels,
            key="selected_document_label",
        )

        selected_index = labels.index(selected_label)
        selected = documents[selected_index]
        document_id = str(selected["DOCUMENT_ID"])

        with st.expander("View metadata", expanded=False):
            metadata_rows = {
                "Document ID": selected.get("DOCUMENT_ID"),
                "Original filename": selected.get("ORIGINAL_FILENAME"),
                "Sanitized filename": selected.get("SANITIZED_FILENAME"),
                "File type": selected.get("FILE_TYPE"),
                "File size (bytes)": selected.get("FILE_SIZE"),
                "Content hash": selected.get("CONTENT_HASH"),
                "Category": selected.get("CATEGORY"),
                "Status": selected.get("PROCESSING_STATUS"),
                "Page count": selected.get("PAGE_COUNT"),
                "Character count": selected.get("CHARACTER_COUNT"),
                "Chunk count": selected.get("CHUNK_COUNT"),
                "Uploaded by": selected.get("UPLOADED_BY"),
                "Created": selected.get("CREATED_AT"),
                "Updated": selected.get("UPDATED_AT"),
                "Active": selected.get("ACTIVE"),
                "Error": selected.get("ERROR_MESSAGE"),
            }
            st.json(metadata_rows)

        action1, action2, action3, action4 = st.columns(4)

        status = str(selected.get("PROCESSING_STATUS", "")).upper()
        active = bool(selected.get("ACTIVE", False))

        with action1:
            retry_clicked = st.button(
                "↻ Retry",
                width="stretch",
                disabled=status == "INDEXED" and active,
                key=f"retry_{document_id}",
            )

        with action2:
            reindex_clicked = st.button(
                "⟳ Re-index",
                width="stretch",
                key=f"reindex_{document_id}",
            )

        with action3:
            delete_clicked = st.button(
                "🗑️ Delete",
                width="stretch",
                disabled=not active,
                key=f"delete_{document_id}",
            )

        with action4:
            refresh_clicked = st.button(
                "🔄 Refresh",
                width="stretch",
                key=f"refresh_{document_id}",
            )

        if retry_clicked:
            action_started = time.perf_counter()
            logger.info(
                "Document management retry clicked | document_id=%s | filename=%s",
                document_id,
                selected.get("ORIGINAL_FILENAME"),
            )
            action_status = st.empty()
            render_processing_indicator(
                "Retrying document and rebuilding its index...", action_status
            )
            try:
                chunk_count = store.retry_document(document_id)
                logger.info(
                    "Document management retry succeeded | document_id=%s | chunks=%s | duration=%.3fs",
                    document_id,
                    chunk_count,
                    time.perf_counter() - action_started,
                )
                action_status.empty()
                st.success(
                    f"Retry completed for {selected['ORIGINAL_FILENAME']} "
                    f"({chunk_count} chunks)."
                )
                local_rerun()
            except Exception as exc:
                action_status.empty()
                logger.exception(
                    "Document management retry failed | document_id=%s | duration=%.3fs",
                    document_id,
                    time.perf_counter() - action_started,
                )
                st.error(f"Retry failed: {exc}")

        if reindex_clicked:
            action_started = time.perf_counter()
            logger.info(
                "Document management re-index clicked | document_id=%s | filename=%s",
                document_id,
                selected.get("ORIGINAL_FILENAME"),
            )
            action_status = st.empty()
            render_processing_indicator(
                "Re-indexing document and refreshing Cortex Search...", action_status
            )
            try:
                chunk_count = store.reindex_document(document_id)
                logger.info(
                    "Document management re-index succeeded | document_id=%s | chunks=%s | duration=%.3fs",
                    document_id,
                    chunk_count,
                    time.perf_counter() - action_started,
                )
                action_status.empty()
                st.success(
                    f"Re-indexed {selected['ORIGINAL_FILENAME']} "
                    f"({chunk_count} chunks)."
                )
                local_rerun()
            except Exception as exc:
                action_status.empty()
                logger.exception(
                    "Document management re-index failed | document_id=%s | duration=%.3fs",
                    document_id,
                    time.perf_counter() - action_started,
                )
                st.error(f"Re-index failed: {exc}")

        if delete_clicked:
            action_started = time.perf_counter()
            logger.info(
                "Document management delete clicked | document_id=%s | filename=%s",
                document_id,
                selected.get("ORIGINAL_FILENAME"),
            )
            action_status = st.empty()
            render_processing_indicator(
                "Deactivating document and removing it from retrieval...", action_status
            )
            try:
                store.delete_document(document_id)
                logger.info(
                    "Document management delete succeeded | document_id=%s | duration=%.3fs",
                    document_id,
                    time.perf_counter() - action_started,
                )
                action_status.empty()
                st.success(
                    f"{selected['ORIGINAL_FILENAME']} was deactivated. "
                    "It will no longer be eligible for retrieval."
                )
                local_rerun()
            except Exception as exc:
                action_status.empty()
                logger.exception(
                    "Document management delete failed | document_id=%s | duration=%.3fs",
                    document_id,
                    time.perf_counter() - action_started,
                )
                st.error(f"Delete failed: {exc}")

        if refresh_clicked:
            action_started = time.perf_counter()
            logger.info(
                "Document management refresh clicked | document_id=%s", document_id
            )
            action_status = st.empty()
            render_processing_indicator("Refreshing Cortex Search...", action_status)
            try:
                refresh_method = getattr(store, "refresh_search", None)
                if refresh_method is None:
                    st.info(
                        "Document metadata refreshed. Cortex Search will refresh "
                        "according to its configured target lag."
                    )
                else:
                    refresh_method()
                    logger.info(
                        "Document management refresh succeeded | document_id=%s | duration=%.3fs",
                        document_id,
                        time.perf_counter() - action_started,
                    )
                    action_status.empty()
                    st.success("Cortex Search refresh requested.")
            except Exception as exc:
                action_status.empty()
                logger.exception(
                    "Document management refresh failed | document_id=%s | duration=%.3fs",
                    document_id,
                    time.perf_counter() - action_started,
                )
                st.warning(f"Search refresh could not be requested: {exc}")


def _render_support_ai_impl(controller: RetailAssistController) -> None:
    """Render the main interactive SupportAI area locally."""
    # -----------------------------------------------------
    # Assistant mode selection
    # -----------------------------------------------------

    st.markdown(
        """
        <div class="ra-section-label">Workspace</div>
        <div class="ra-section-title">Choose your support intelligence</div>
        <div class="ra-helper">
            Use policy-grounded answers, operational analytics, or the intelligent
            Cortex Agent for questions that cross both sources.
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.container():
        mode = st.selectbox(
            "Assistant Mode",
            [
                "Policy RAG",
                "Support Analytics",
                "Cortex Agent",
            ],
            key="assistant_mode",
            label_visibility="collapsed",
        )

    mode_tagline = {
        "Policy RAG": "Trusted policy answers with source-backed evidence.",
        "Support Analytics": "Natural-language insights from live support-ticket data.",
        "Cortex Agent": "One intelligent interface that routes policy + analytics automatically.",
    }[mode]
    st.markdown(
        f'<div class="ra-context-card">✦ &nbsp;<strong>{html.escape(mode)}</strong> &nbsp;·&nbsp; {html.escape(mode_tagline)}</div>',
        unsafe_allow_html=True,
    )

    provider = "Snowflake Cortex"
    provider_name = "snowflake"

    if mode == "Policy RAG":
        st.markdown(
            """
            <div class="ra-section-label">Generation</div>
            <div class="ra-section-title">AI Provider</div>
            <div class="ra-helper">
                Select the provider to use for the next response.
                Retrieval continues through Snowflake Cortex Search.
            </div>
            """,
            unsafe_allow_html=True,
        )

        provider = st.selectbox(
            "AI Provider",
            [
                "Snowflake Cortex",
                "Groq (GPT-OSS 20B)",
            ],
            key="ai_provider",
        )

        provider_name = {
            "Snowflake Cortex": "snowflake",
            "Groq (GPT-OSS 20B)": "openai",
        }[provider]

        try:
            current_provider = controller.get_provider()

            if current_provider != provider_name:
                logger.info(
                    "UI provider selection changed | from=%s | to=%s",
                    current_provider,
                    provider_name,
                )
                controller.set_provider(provider_name)
            else:
                logger.debug(
                    "UI provider unchanged | provider=%s",
                    provider_name,
                )

        except Exception as exc:
            logger.exception(
                "UI provider selection failed | provider=%s",
                provider_name,
            )
            st.error(f"Unable to select {provider}: {exc}")

    # -----------------------------------------------------
    # Knowledge base document upload
    # -----------------------------------------------------

    st.markdown(
        """
        <div class="ra-section-label">Knowledge base</div>
        <div class="ra-section-title">Upload documents</div>
        <div class="ra-helper">
            Upload PDF, DOCX, Markdown, or TXT files to add
            new knowledge to the customer-support system.
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        uploaded_files = st.file_uploader(
            "Choose documents",
            type=["pdf", "docx", "md", "txt"],
            accept_multiple_files=True,
            help="Maximum 5 files, 20 MB per file.",
        )

        if uploaded_files:
            valid_files, upload_errors = validate_uploaded_files(uploaded_files)

            for error in upload_errors:
                logger.warning("Upload validation error: %s", error)
                st.error(error)

            if valid_files:
                st.markdown("**Selected documents**")
                detected_categories = {}

                for uploaded_file in valid_files:
                    file_bytes = uploaded_file.getvalue()

                    file_size_mb = len(file_bytes) / (1024 * 1024)

                    detected_category = "Detecting..."

                    if controller.document_store is not None:
                        detected_category = detect_category_cached(
                            controller,
                            file_bytes,
                            uploaded_file.name,
                        )

                    detected_categories[uploaded_file.name] = detected_category

                    st.write(
                        f"📄 **{uploaded_file.name}** "
                        f"• {file_size_mb:.2f} MB "
                        f"• **Category: {detected_category}**"
                    )

                    st.caption("Document ID will be assigned during upload.")

                if st.button(
                    "⬆️ Upload to Snowflake",
                    type="primary",
                    width="stretch",
                    key="main_upload_to_snowflake",
                ):
                    logger.info(
                        "Document upload action triggered | files=%s",
                        len(valid_files),
                    )

                    if controller.document_store is None:
                        logger.error(
                            "Document upload requested but document store is unavailable."
                        )
                        st.error("Document upload requires Snowflake mode.")

                    else:
                        successful_uploads = 0
                        uploaded_document_ids = []

                        for uploaded_file in valid_files:
                            file_bytes = uploaded_file.getvalue()

                            try:
                                upload_start = time.perf_counter()
                                logger.info(
                                    "Document upload started | filename=%s | size_bytes=%s",
                                    uploaded_file.name,
                                    len(file_bytes),
                                )

                                progress_bar = st.progress(0)
                                progress_status = st.empty()

                                def upload_progress(percent, stage, detail=""):
                                    progress_bar.progress(
                                        max(0, min(100, int(percent)))
                                    )
                                    message = f"{stage}"
                                    if detail:
                                        message += f" • {detail}"
                                    progress_status.caption(message)

                                upload_progress(
                                    1, "Starting upload", uploaded_file.name
                                )
                                staged_path = controller.document_store.upload_to_stage(
                                    file_bytes=file_bytes,
                                    filename=uploaded_file.name,
                                    document_id=None,
                                    category=detected_categories.get(
                                        uploaded_file.name
                                    ),
                                    progress_callback=upload_progress,
                                )
                                progress_bar.progress(100)
                                progress_status.caption("Indexed and ready")

                                # DocumentStore owns the canonical ID.
                                # Returned path:
                                # @STAGE/<document_id>/<filename>

                                stage_parts = staged_path.split(
                                    "/",
                                    2,
                                )

                                document_id = (
                                    stage_parts[1]
                                    if len(stage_parts) >= 2
                                    else "Unknown"
                                )

                                if document_id != "Unknown":
                                    uploaded_document_ids.append(document_id)

                                st.success(
                                    f"{uploaded_file.name} uploaded successfully."
                                )

                                st.caption(f"Document ID: {document_id}")

                                st.caption(f"Stage: {staged_path}")

                                successful_uploads += 1

                                logger.info(
                                    "Document upload completed | filename=%s | duration=%.3fs",
                                    uploaded_file.name,
                                    time.perf_counter() - upload_start,
                                )

                            except Exception as exc:
                                logger.exception(
                                    "Document upload failed | filename=%s | duration=%.3fs",
                                    uploaded_file.name,
                                    time.perf_counter() - upload_start,
                                )
                                st.error(
                                    f"Failed to upload {uploaded_file.name}: {exc}"
                                )

                        if successful_uploads:
                            st.session_state["supportai_pending_document_ids"] = sorted(
                                set(uploaded_document_ids)
                            )
                            logger.info(
                                "Pending retrieval scope set to newly uploaded documents | document_count=%s",
                                len(uploaded_document_ids),
                            )
                            st.info(
                                f"{successful_uploads} document(s) uploaded to Snowflake."
                            )

    # -----------------------------------------------------
    # FAQ / customer support
    # -----------------------------------------------------

    mode_content = {
        "Policy RAG": (
            "Ask your policy question",
            "Ask about any policy available in the knowledge base.",
            "Example: Does the warranty cover accidental damage?",
        ),
        "Support Analytics": (
            "Ask your support analytics question",
            "Ask about support-ticket volume, status, resolution time, ratings, escalations, refunds, or other structured metrics.",
            "Example: Which category has the highest average resolution time?",
        ),
        "Cortex Agent": (
            "Ask SupportAI",
            "Ask a policy question, an analytics question, or a combined question. The agent automatically selects the appropriate Snowflake tools.",
            "Example: What does our refund policy say and how many refund tickets did we receive last month?",
        ),
    }
    section_title, helper_text, question_placeholder = mode_content[mode]

    st.markdown(
        f"""
        <div class="ra-section-label">Customer support</div>
        <div class="ra-section-title">{html.escape(section_title)}</div>
        <div class="ra-helper">
            {html.escape(helper_text)}
        </div>
        """,
        unsafe_allow_html=True,
    )

    question = st.text_area(
        "Question",
        placeholder=question_placeholder,
        height=118,
        label_visibility="collapsed",
        key="supportai_question",
    )

    st.markdown(
        '<div style="height:6px;"></div>',
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([3.15, 1])

    with col1:
        ask_clicked = st.button(
            "✦  Ask SupportAI",
            type="primary",
            width="stretch",
            key="ask_supportai",
        )

    with col2:
        clear_clicked = st.button(
            "↺  Clear",
            width="stretch",
            key="clear_supportai",
        )

    if clear_clicked:
        logger.info(
            "Clear button clicked; preserving pending upload scope until the next question."
        )
        local_rerun()

    if ask_clicked:
        if not question.strip():
            logger.warning("Ask action rejected because question was empty.")
            st.warning("Please enter a question.")
            return

        request_start = time.perf_counter()

        logger.info(
            "UI question processing started | mode=%s | provider=%s | question_length=%s",
            mode,
            provider_name if mode == "Policy RAG" else "snowflake-native",
            len(question.strip()),
        )

        rag_service = getattr(controller, "rag_service", None)
        retriever = getattr(rag_service, "retriever", None) if rag_service else None
        set_scope = getattr(retriever, "set_document_scope", None)
        retrieval_document_ids = st.session_state.get(
            "supportai_pending_document_ids",
            [],
        )

        try:
            # Uploaded-document scoping is part of the existing Policy RAG flow.
            # Analytics and Agent modes do not use the RAG retriever directly.
            if mode == "Policy RAG" and callable(set_scope):
                set_scope(retrieval_document_ids or None)

            request_status = st.empty()

            if mode == "Policy RAG":
                processing_message = (
                    f"Retrieving evidence and generating with {provider}..."
                )
            elif mode == "Support Analytics":
                processing_message = (
                    "Analyzing support-ticket data with Cortex Analyst..."
                )
            else:
                processing_message = "Routing your question through the Cortex Agent..."

            render_processing_indicator(processing_message, request_status)

            if mode == "Policy RAG":
                response = controller.ask_policy(
                    question.strip(),
                    top_k=3,
                )
                request_status.empty()
                elapsed = time.perf_counter() - request_start
                logger.info(
                    "UI Policy RAG completed | provider=%s | duration=%.3fs | evidence=%s",
                    provider_name,
                    elapsed,
                    len(response.evidence or []),
                )
                render_response(response, question, provider, elapsed)

            elif mode == "Support Analytics":
                response = controller.ask_analytics(question.strip())
                request_status.empty()
                elapsed = time.perf_counter() - request_start
                logger.info(
                    "UI Support Analytics completed | duration=%.3fs | sql_present=%s",
                    elapsed,
                    bool(response.sql),
                )
                render_analytics_response(response, question, elapsed)

            else:
                response = controller.ask_agent(question.strip())
                request_status.empty()
                elapsed = time.perf_counter() - request_start
                logger.info(
                    "UI Cortex Agent completed | duration=%.3fs",
                    elapsed,
                )
                render_agent_response(response, elapsed)

        except ValueError as exc:
            logger.warning(
                "UI question validation failed | mode=%s | error=%s",
                mode,
                exc,
            )
            st.error(str(exc))

        except RuntimeError as exc:
            logger.exception(
                "UI request failed with runtime error | mode=%s | duration=%.3fs",
                mode,
                time.perf_counter() - request_start,
            )
            st.error(str(exc))

        except Exception:
            logger.exception(
                "Unexpected UI request failure | mode=%s | duration=%.3fs",
                mode,
                time.perf_counter() - request_start,
            )
            st.error(
                "SupportAI could not generate a response. "
                "Please check the Snowflake/provider configuration and try again."
            )

        finally:
            # Consume the one-question upload preference regardless of whether
            # the request succeeded. The document itself remains in Snowflake
            # and is searchable normally on future Policy RAG questions.
            st.session_state.pop("supportai_pending_document_ids", None)
            if callable(set_scope):
                set_scope(None)
            logger.info(
                "Question-scoped retrieval preference cleared; complete knowledge base is active."
            )


if FRAGMENT_AVAILABLE:
    _render_support_ai_fragment = st.fragment(_render_support_ai_impl)
else:
    _render_support_ai_fragment = _render_support_ai_impl

if FRAGMENT_AVAILABLE:
    render_document_management = st.fragment(_render_document_management_impl)
else:
    render_document_management = _render_document_management_impl


def render_welcome_animation() -> None:
    """Show a short, cinematic product-intro animation on first load only."""
    if st.session_state.get("supportai_welcome_shown", False):
        return

    st.session_state["supportai_welcome_shown"] = True

    welcome_html = textwrap.dedent(
        """
        <style>
        /* =========================================================
           SupportAI — cinematic product-intro welcome
           Intentionally restrained: premium, precise, fast.
           ========================================================= */

        .supportai-welcome-overlay {
            position: fixed;
            inset: 0;
            z-index: 999999;
            overflow: hidden;
            display: grid;
            place-items: center;
            background:
                radial-gradient(circle at 50% 48%, rgba(99,102,241,0.18), transparent 19%),
                radial-gradient(circle at 18% 16%, rgba(56,189,248,0.11), transparent 25%),
                radial-gradient(circle at 84% 78%, rgba(139,92,246,0.12), transparent 26%),
                #050912;
            pointer-events: auto;
            animation: supportai-intro-exit 4.9s cubic-bezier(.76,0,.24,1) forwards;
        }

        .supportai-welcome-overlay::before {
            content: "";
            position: absolute;
            inset: 0;
            background:
                linear-gradient(
                    115deg,
                    transparent 0%,
                    transparent 34%,
                    rgba(255,255,255,0.035) 46%,
                    rgba(129,140,248,0.08) 50%,
                    rgba(255,255,255,0.025) 54%,
                    transparent 66%,
                    transparent 100%
                );
            transform: translateX(-120%);
            animation: supportai-intro-sheen 4.3s cubic-bezier(.35,0,.2,1) 0.55s infinite;
            pointer-events: none;
        }

        .supportai-welcome-noise {
            position: absolute;
            inset: 0;
            opacity: 0.13;
            background-image:
                radial-gradient(rgba(255,255,255,0.42) 0.6px, transparent 0.8px);
            background-size: 5px 5px;
            mix-blend-mode: soft-light;
            pointer-events: none;
        }

        .supportai-welcome-grid {
            position: absolute;
            left: -16%;
            right: -16%;
            bottom: -22%;
            height: 58%;
            opacity: 0.16;
            background-image:
                linear-gradient(rgba(148,163,184,0.10) 1px, transparent 1px),
                linear-gradient(90deg, rgba(148,163,184,0.10) 1px, transparent 1px);
            background-size: 58px 58px;
            transform: perspective(900px) rotateX(63deg);
            transform-origin: center bottom;
            mask-image: linear-gradient(to top, black, transparent 88%);
            animation: supportai-intro-grid 8s ease-in-out infinite alternate;
            pointer-events: none;
        }

        .supportai-welcome-radar {
            position: absolute;
            width: min(70vw, 920px);
            height: min(70vw, 920px);
            min-width: 440px;
            min-height: 440px;
            border-radius: 50%;
            border: 1px solid rgba(165,180,252,0.07);
            box-shadow:
                0 0 90px rgba(99,102,241,0.05),
                inset 0 0 70px rgba(56,189,248,0.025);
            animation: supportai-intro-radar 7.2s ease-in-out infinite alternate;
        }

        .supportai-welcome-ring {
            position: absolute;
            width: min(52vw, 720px);
            height: min(20vw, 270px);
            min-width: 470px;
            min-height: 210px;
            border-radius: 50%;
            border: 1px solid rgba(129,140,248,0.12);
            transform: rotate(-12deg);
            box-shadow: 0 0 42px rgba(99,102,241,0.04);
            animation: supportai-intro-ring 7.5s ease-in-out infinite alternate;
        }

        .supportai-welcome-ring.two {
            width: min(63vw, 860px);
            height: min(25vw, 330px);
            border-color: rgba(56,189,248,0.065);
            transform: rotate(17deg) scaleY(0.76);
            animation-delay: -2.8s;
        }

        .supportai-welcome-core {
            position: absolute;
            width: min(23vw, 320px);
            height: min(23vw, 320px);
            min-width: 220px;
            min-height: 220px;
            border-radius: 50%;
            background:
                radial-gradient(circle at center,
                    rgba(165,180,252,0.23) 0%,
                    rgba(99,102,241,0.10) 24%,
                    rgba(56,189,248,0.035) 48%,
                    transparent 72%);
            filter: blur(5px);
            opacity: 0;
            animation:
                supportai-intro-core 1.2s cubic-bezier(.2,.8,.2,1) 0.32s forwards,
                supportai-intro-breathe 4.8s ease-in-out 1.52s infinite alternate;
        }

        .supportai-welcome-emblem {
            position: relative;
            z-index: 6;
            width: 82px;
            height: 82px;
            border-radius: 24px;
            display: grid;
            place-items: center;
            margin: 0 auto 25px;
            background:
                linear-gradient(145deg, rgba(255,255,255,0.14), rgba(129,140,248,0.08));
            border: 1px solid rgba(255,255,255,0.18);
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,0.18),
                0 12px 45px rgba(0,0,0,0.18),
                0 0 38px rgba(99,102,241,0.13);
            backdrop-filter: blur(14px);
            -webkit-backdrop-filter: blur(14px);
            opacity: 0;
            transform: translateY(18px) scale(0.86);
            animation: supportai-intro-emblem 0.9s cubic-bezier(.2,.8,.2,1) 0.1s forwards;
        }

        .supportai-welcome-emblem::before {
            content: "";
            position: absolute;
            inset: -9px;
            border-radius: 28px;
            border: 1px solid rgba(129,140,248,0.10);
            transform: scale(0.72);
            opacity: 0;
            animation: supportai-intro-emblem-ring 1.1s ease 0.54s forwards;
        }

        .supportai-welcome-emblem-mark {
            color: #ffffff;
            font-size: 2.25rem;
            line-height: 1;
            font-weight: 900;
            text-shadow:
                0 0 15px rgba(255,255,255,0.28),
                0 0 34px rgba(129,140,248,0.38);
        }

        .supportai-welcome-content {
            position: relative;
            z-index: 5;
            width: min(94vw, 1060px);
            text-align: center;
            padding: 28px 24px 40px;
            margin-top: -3px;
        }

        .supportai-welcome-kicker {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            width: max-content;
            max-width: 90vw;
            margin: 0 auto 14px;
            color: #adbaff;
            font-size: clamp(0.65rem, 1.15vw, 0.78rem);
            font-weight: 850;
            letter-spacing: 0.34em;
            text-transform: uppercase;
            text-indent: 0.34em;
            opacity: 0;
            animation: supportai-intro-kicker 0.7s ease 0.52s forwards;
        }

        .supportai-welcome-kicker::before,
        .supportai-welcome-kicker::after {
            content: "";
            display: block;
            width: clamp(28px, 4vw, 56px);
            height: 1px;
            flex: 0 0 auto;
            background: linear-gradient(90deg, transparent, rgba(165,180,252,0.58));
        }

        .supportai-welcome-kicker::after {
            background: linear-gradient(90deg, rgba(165,180,252,0.58), transparent);
        }

        .supportai-welcome-title {
            position: relative;
            display: block;
            width: fit-content;
            margin: 0 auto;
            text-align: center;
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
            font-size: clamp(2.75rem, 4.2vw, 4.5rem);
            line-height: 0.87;
            font-weight: 950;
            letter-spacing: -0.075em;
            opacity: 0;
            transform: translateY(20px) scale(0.985);
            text-shadow:
                0 0 14px rgba(255,255,255,0.12),
                0 0 46px rgba(129,140,248,0.26),
                0 0 94px rgba(56,189,248,0.10);
            animation: supportai-intro-title 1.05s cubic-bezier(.18,.82,.18,1) 0.62s forwards;
            filter: none !important;
            mix-blend-mode: normal !important;
        }

        .supportai-welcome-title::before {
            content: "";
            position: absolute;
            inset: -14% -4%;
            background:
                linear-gradient(
                    105deg,
                    transparent 37%,
                    rgba(255,255,255,0.04) 43%,
                    rgba(255,255,255,0.98) 50%,
                    rgba(186,230,253,0.42) 54%,
                    rgba(255,255,255,0.03) 61%,
                    transparent 66%
                );
            transform: translateX(-145%);
            mix-blend-mode: screen;
            pointer-events: none;
            animation: supportai-intro-title-shine 2.45s cubic-bezier(.35,0,.2,1) 1.12s infinite;
        }

        .supportai-welcome-title::after {
            content: "";
            position: absolute;
            left: 8%;
            right: 8%;
            bottom: -10px;
            height: 2px;
            border-radius: 99px;
            background:
                linear-gradient(
                    90deg,
                    transparent,
                    rgba(129,140,248,0.95),
                    rgba(56,189,248,0.95),
                    rgba(129,140,248,0.95),
                    transparent
                );
            box-shadow: 0 0 26px rgba(99,102,241,0.40);
            transform: scaleX(0);
            animation: supportai-intro-line 0.95s cubic-bezier(.2,.8,.2,1) 1.18s forwards;
        }

        .supportai-welcome-tagline {
            max-width: 860px;
            margin: 36px auto 0;
            color: #dbe5f5;
            font-size: clamp(0.98rem, 1.85vw, 1.22rem);
            line-height: 1.7;
            letter-spacing: 0.004em;
            opacity: 0;
            transform: translateY(14px);
            animation: supportai-intro-copy 0.85s ease 1.48s forwards;
        }

        .supportai-welcome-capabilities {
            display: flex;
            justify-content: center;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 21px;
            opacity: 0;
            transform: translateY(10px);
            animation: supportai-intro-copy 0.75s ease 1.72s forwards;
        }

        .supportai-welcome-capability {
            padding: 7px 11px;
            border-radius: 999px;
            color: #aebbd2;
            background: rgba(15,23,42,0.34);
            border: 1px solid rgba(148,163,184,0.12);
            font-size: 0.65rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            backdrop-filter: blur(9px);
            -webkit-backdrop-filter: blur(9px);
        }

        .supportai-welcome-status {
            display: inline-flex;
            align-items: center;
            gap: 9px;
            margin-top: 21px;
            color: #94a3b8;
            font-size: 0.66rem;
            font-weight: 800;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            opacity: 0;
            animation: supportai-intro-copy 0.7s ease 1.94s forwards;
        }

        .supportai-welcome-status-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: #34d399;
            box-shadow:
                0 0 0 5px rgba(52,211,153,0.07),
                0 0 16px rgba(52,211,153,0.48);
            animation: supportai-intro-status 1.5s ease-in-out infinite;
        }

        .supportai-welcome-orbit-dot {
            position: absolute;
            z-index: 4;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #eef2ff;
            box-shadow:
                0 0 0 5px rgba(224,231,255,0.04),
                0 0 22px rgba(129,140,248,0.58);
            animation: supportai-intro-dot 6.1s linear infinite;
        }

        .supportai-welcome-orbit-dot.d2 {
            width: 6px;
            height: 6px;
            opacity: 0.60;
            animation-delay: -1.8s;
        }

        .supportai-welcome-orbit-dot.d3 {
            width: 4px;
            height: 4px;
            opacity: 0.42;
            animation-delay: -3.9s;
        }

        @keyframes supportai-intro-emblem {
            to { opacity: 1; transform: translateY(0) scale(1); }
        }

        @keyframes supportai-intro-emblem-ring {
            0% { opacity: 0; transform: scale(0.72); }
            65% { opacity: 1; transform: scale(1.02); }
            100% { opacity: 0.42; transform: scale(1); }
        }

        @keyframes supportai-intro-kicker {
            from { opacity: 0; transform: translateY(9px); }
            to { opacity: 1; transform: translateY(0); }
        }

        @keyframes supportai-intro-title {
            from { opacity: 0; transform: translateY(20px) scale(0.985); filter: blur(4px); }
            to { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
        }

        @keyframes supportai-intro-title-shine {
            0%, 16% { transform: translateX(-145%); opacity: 0; }
            22% { opacity: 1; }
            60% { transform: translateX(145%); opacity: 1; }
            72%, 100% { transform: translateX(145%); opacity: 0; }
        }

        @keyframes supportai-intro-line {
            to { transform: scaleX(1); }
        }

        @keyframes supportai-intro-copy {
            from { opacity: 0; transform: translateY(14px); }
            to { opacity: 1; transform: translateY(0); }
        }

        @keyframes supportai-intro-core {
            0% { opacity: 0; transform: scale(0.62); }
            70% { opacity: 1; transform: scale(1.05); }
            100% { opacity: 0.86; transform: scale(1); }
        }

        @keyframes supportai-intro-breathe {
            from { transform: scale(0.98); opacity: 0.70; }
            to { transform: scale(1.06); opacity: 1; }
        }

        @keyframes supportai-intro-radar {
            from { transform: scale(0.985); opacity: 0.42; }
            to { transform: scale(1.025); opacity: 0.76; }
        }

        @keyframes supportai-intro-ring {
            from { transform: rotate(-12deg) translateX(-10px); opacity: 0.35; }
            to { transform: rotate(-4deg) translateX(17px); opacity: 0.78; }
        }

        @keyframes supportai-intro-grid {
            from { transform: perspective(900px) rotateX(63deg) translate3d(0, 5px, 0); }
            to { transform: perspective(900px) rotateX(63deg) translate3d(-28px, -4px, 0); }
        }

        @keyframes supportai-intro-dot {
            0% { top: 19%; left: 18%; transform: scale(0.78); }
            25% { top: 10%; left: 78%; transform: scale(1); }
            50% { top: 76%; left: 84%; transform: scale(0.88); }
            75% { top: 88%; left: 18%; transform: scale(1.04); }
            100% { top: 19%; left: 18%; transform: scale(0.78); }
        }

        @keyframes supportai-intro-status {
            0%, 100% { transform: scale(0.84); opacity: 0.72; }
            50% { transform: scale(1.14); opacity: 1; }
        }

        @keyframes supportai-intro-sheen {
            0%, 18% { transform: translateX(-120%); opacity: 0; }
            30% { opacity: 1; }
            64% { transform: translateX(120%); opacity: 1; }
            78%, 100% { transform: translateX(120%); opacity: 0; }
        }

        @keyframes supportai-intro-exit {
            0%, 78% { opacity: 1; visibility: visible; }
            100% { opacity: 0; visibility: hidden; pointer-events: none; }
        }

        @media (prefers-reduced-motion: reduce) {
            .supportai-welcome-overlay,
            .supportai-welcome-overlay::before,
            .supportai-welcome-grid,
            .supportai-welcome-radar,
            .supportai-welcome-ring,
            .supportai-welcome-core,
            .supportai-welcome-emblem,
            .supportai-welcome-emblem::before,
            .supportai-welcome-kicker,
            .supportai-welcome-title,
            .supportai-welcome-title::before,
            .supportai-welcome-title::after,
            .supportai-welcome-tagline,
            .supportai-welcome-capabilities,
            .supportai-welcome-status,
            .supportai-welcome-status-dot,
            .supportai-welcome-orbit-dot {
                animation: none !important;
            }

            .supportai-welcome-overlay {
                opacity: 0;
                visibility: hidden;
                pointer-events: none;
            }
        }

        @media (max-width: 700px) {
            .supportai-welcome-content {
                width: 96vw;
                padding: 24px 14px 32px;
            }

            .supportai-welcome-kicker {
                width: 92vw;
                gap: 8px;
            }

            .supportai-welcome-emblem {
                width: 68px;
                height: 68px;
                border-radius: 20px;
                margin-bottom: 20px;
            }

            .supportai-welcome-emblem-mark {
                font-size: 1.85rem;
            }

            .supportai-welcome-tagline {
                max-width: 88vw;
                font-size: 0.94rem;
            }

            .supportai-welcome-radar {
                width: 128vw;
                height: 128vw;
                min-width: 0;
                min-height: 0;
            }

            .supportai-welcome-ring,
            .supportai-welcome-ring.two {
                width: 124vw;
                min-width: 0;
            }
        }
        </style>

        <div class="supportai-welcome-overlay" aria-label="Welcome to SupportAI">
            <div class="supportai-welcome-noise"></div>
            <div class="supportai-welcome-grid"></div>
            <div class="supportai-welcome-radar"></div>
            <div class="supportai-welcome-ring"></div>
            <div class="supportai-welcome-ring two"></div>
            <div class="supportai-welcome-core"></div>

            <div class="supportai-welcome-orbit-dot d1"></div>
            <div class="supportai-welcome-orbit-dot d2"></div>
            <div class="supportai-welcome-orbit-dot d3"></div>

            <div class="supportai-welcome-content">
                <div class="supportai-welcome-emblem">
                    <div class="supportai-welcome-emblem-mark">✦</div>
                </div>

                <div class="supportai-welcome-kicker">
                    <span>Welcome to</span>
                </div>

                <h1 class="supportai-welcome-title"
                    style="color:#ffffff !important; -webkit-text-fill-color:#ffffff !important; opacity:1 !important;">
                    SupportAI
                </h1>

                <div class="supportai-welcome-tagline">
                    Intelligent customer support powered by trusted knowledge,
                    operational analytics, and Snowflake-native intelligence.
                </div>

                <div class="supportai-welcome-capabilities">
                    <span class="supportai-welcome-capability">Trusted Knowledge</span>
                    <span class="supportai-welcome-capability">Support Analytics</span>
                    <span class="supportai-welcome-capability">Agent Orchestration</span>
                </div>

                <div class="supportai-welcome-status">
                    <span class="supportai-welcome-status-dot"></span>
                    Preparing your support workspace
                </div>
            </div>
        </div>
        """
    )

    if callable(getattr(st, "html", None)):
        st.html(welcome_html)
    else:
        st.markdown(welcome_html, unsafe_allow_html=True)


def run_app(
    controller: RetailAssistController,
) -> None:
    """Render the SupportAI Streamlit application."""

    logger.debug("Rendering SupportAI UI.")

    st.set_page_config(
        page_title="SupportAI | Customer Support Assistant",
        page_icon="✦",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # ---------------------------------------------------------
    # Appearance preference (UI-only)
    # ---------------------------------------------------------

    if "supportai_theme" not in st.session_state:
        st.session_state["supportai_theme"] = "light"

    theme = st.session_state["supportai_theme"]
    inject_box_styles(theme)

    st.markdown(
        f'<div class="ra-theme-marker-{html.escape(theme)}" aria-hidden="true"></div>',
        unsafe_allow_html=True,
    )

    # Single icon toggle beside the native Streamlit toolbar.
    with st.container(key="supportai_theme_toggle"):
        next_icon = "☀" if theme == "dark" else "◐"
        toggle_clicked = st.button(
            next_icon,
            key="supportai_theme_button",
            help="Switch to the light workspace theme."
            if theme == "dark"
            else "Switch to the dark workspace theme.",
            type="secondary",
        )

    if toggle_clicked:
        st.session_state["supportai_theme"] = "light" if theme == "dark" else "dark"
        st.rerun()

    render_welcome_animation()

    # ---------------------------------------------------------
    # Product hero
    # ---------------------------------------------------------

    st.markdown(
        '<div class="ra-bg-ambience" aria-hidden="true"></div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        """
    <div class="ra-hero">
        <div class="ra-brand-row">
            <div class="ra-brand-icon">✦</div>
            <div>
                <div class="ra-brand-title">SupportAI</div>
                <div class="ra-brand-subtitle">
                    An intelligent customer-support workspace for trusted policy answers,
                    support analytics, and agent-powered resolution.
                </div>
            </div>
        </div>

    <div class="ra-status-strip">
        <span class="ra-status"><span class="ra-status-dot"></span> Production-ready workspace</span>
        <span class="ra-status">Snowflake Native Intelligence</span>
        <span class="ra-status">Grounded &amp; Traceable</span>
    </div>
    </div>

    <div class="ra-product-strip">
        <div class="ra-product-tile">
            <div class="ra-product-kicker">Knowledge</div>
            <div class="ra-product-value">Policy-grounded responses</div>
        </div>
        <div class="ra-product-tile">
            <div class="ra-product-kicker">Operations</div>
            <div class="ra-product-value">Live support-ticket insights</div>
        </div>
        <div class="ra-product-tile">
            <div class="ra-product-kicker">Automation</div>
            <div class="ra-product-value">Cortex Agent orchestration</div>
        </div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # =========================================================
    # MAIN NAVIGATION
    # =========================================================

    main_tab, management_tab = st.tabs(
        [
            "✦ SupportAI",
            "📚 Document Management",
        ]
    )

    # =========================================================
    # MAIN TAB
    # =========================================================

    with main_tab:
        _render_support_ai_fragment(controller)

    # =========================================================
    # DOCUMENT MANAGEMENT TAB
    # =========================================================

    with management_tab:
        render_document_management(controller)

    # =========================================================
    # FOOTER
    # =========================================================

    st.markdown(
        """
        <div class="ra-footer">
            <strong>SupportAI</strong> • Grounded answers from approved policy evidence
            <span class="ra-footer-credit">Built for intelligent customer support</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
