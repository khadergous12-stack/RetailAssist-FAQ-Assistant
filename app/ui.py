import re
import html
import textwrap
import hashlib
import logging
import time

import streamlit as st

from app.controller import RetailAssistController
from services.auth_service import AuthService
from services.admin_service import AdminService
from services.activity_service import ActivityService
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

            body:has(.ra-theme-marker-dark) div[data-testid="stSelectbox"] [data-baseweb="select"] > div > div,
            body:has(.ra-theme-marker-dark) div[data-testid="stMultiSelect"] [data-baseweb="select"] > div > div {
                background: transparent !important;
                color: #f8fafc !important;
            }

            /* Dark dropdowns / BaseWeb popovers.
               Streamlit renders these menus in a portal, so target the menu,
               listbox and option nodes directly rather than relying on the
               selectbox container alone. */
            body:has(.ra-theme-marker-dark) div[data-baseweb="popover"],
            body:has(.ra-theme-marker-dark) div[data-baseweb="popover"] > div,
            body:has(.ra-theme-marker-dark) div[data-baseweb="menu"],
            body:has(.ra-theme-marker-dark) div[data-baseweb="menu"] > div,
            body:has(.ra-theme-marker-dark) [role="listbox"] {
                background: #0f172a !important;
                background-color: #0f172a !important;
                border-color: #334155 !important;
                color: #e5e7eb !important;
            }

            body:has(.ra-theme-marker-dark) [role="option"],
            body:has(.ra-theme-marker-dark) [data-baseweb="option"],
            body:has(.ra-theme-marker-dark) [role="option"] > div,
            body:has(.ra-theme-marker-dark) [data-baseweb="option"] > div {
                background: transparent !important;
                background-color: transparent !important;
                color: #e5e7eb !important;
            }

            body:has(.ra-theme-marker-dark) [role="option"]:hover,
            body:has(.ra-theme-marker-dark) [data-baseweb="option"]:hover,
            body:has(.ra-theme-marker-dark) [aria-selected="true"][role="option"] {
                background: #1e293b !important;
                background-color: #1e293b !important;
                color: #f8fafc !important;
            }

            body:has(.ra-theme-marker-dark) [data-baseweb="popover"] input,
            body:has(.ra-theme-marker-dark) [data-baseweb="menu"] input {
                background: #0f172a !important;
                color: #e5e7eb !important;
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


            /* =========================================================
               Signature product-surface refresh
               Keeps the existing application logic and controls intact.
               ========================================================= */

            .stApp {
                background:
                    radial-gradient(circle at 4% 12%, rgba(99,102,241,0.15), transparent 23%),
                    radial-gradient(circle at 96% 10%, rgba(6,182,212,0.16), transparent 25%),
                    radial-gradient(circle at 18% 82%, rgba(236,72,153,0.10), transparent 25%),
                    radial-gradient(circle at 82% 74%, rgba(245,158,11,0.08), transparent 23%),
                    linear-gradient(135deg, #f4f0ff 0%, #f8f6ff 35%, #eef8fb 68%, #fff7f3 100%) !important;
            }

            .ra-hero {
                min-height: 0 !important;
                padding: 18px 34px 30px !important;
                border: 1px solid rgba(255,255,255,0.18) !important;
                background:
                    radial-gradient(circle at 88% -10%, rgba(99,102,241,0.50), transparent 31%),
                    radial-gradient(circle at 12% 110%, rgba(236,72,153,0.20), transparent 28%),
                    radial-gradient(circle at 72% 100%, rgba(6,182,212,0.13), transparent 30%),
                    linear-gradient(135deg, #081426 0%, #0e1930 48%, #182452 100%) !important;
                box-shadow:
                    0 28px 70px rgba(15,23,42,0.20),
                    inset 0 1px 0 rgba(255,255,255,0.07) !important;
            }

            .ra-hero::after {
                width: 520px;
                height: 520px;
                right: -180px;
                top: -240px;
                background: radial-gradient(circle at 38% 42%, rgba(129,140,248,0.32), rgba(56,189,248,0.07) 38%, transparent 70%);
                animation: ra-signature-orb 11s ease-in-out infinite alternate !important;
            }

            .ra-hero::before {
                opacity: 0.55;
                animation: ra-signature-sheen 9s cubic-bezier(.35,0,.2,1) infinite !important;
            }

            @keyframes ra-signature-orb {
                0% { transform: translate3d(0,0,0) scale(.94); }
                50% { transform: translate3d(-24px,18px,0) scale(1.04); }
                100% { transform: translate3d(-8px,34px,0) scale(1.08); }
            }

            @keyframes ra-signature-sheen {
                0%, 58% { transform: translateX(-70%) rotate(0deg); opacity: 0; }
                68% { opacity: .8; }
                88%, 100% { transform: translateX(70%) rotate(0deg); opacity: 0; }
            }

            .ra-hero-eyebrow,
            .ra-hero-system {
                display: inline-flex;
                align-items: center;
                gap: 8px;
                min-height: 32px;
                padding: 7px 12px;
                border-radius: 999px;
                border: 1px solid rgba(255,255,255,0.15);
                background: rgba(255,255,255,0.055);
                color: #cbd5e1;
                font-size: .68rem;
                font-weight: 850;
                letter-spacing: .10em;
                text-transform: uppercase;
                backdrop-filter: blur(12px);
                -webkit-backdrop-filter: blur(12px);
            }

            .ra-hero-system {
                border-color: rgba(129,140,248,0.18);
                color: #aeb9d5;
            }

            .ra-hero-system b {
                color: #818cf8;
                font-weight: 900;
            }

            .ra-live-dot {
                width: 7px;
                height: 7px;
                border-radius: 50%;
                background: #34d399;
                box-shadow: 0 0 0 4px rgba(52,211,153,.10), 0 0 15px rgba(52,211,153,.55);
                animation: ra-live-pulse 2.2s ease-in-out infinite;
            }

            @keyframes ra-live-pulse {
                0%, 100% { transform: scale(.88); opacity: .75; }
                50% { transform: scale(1.18); opacity: 1; }
            }

            .ra-brand-row {
                gap: 18px !important;
                align-items: center !important;
            }

            .ra-brand-copy {
                transform: translateY(10px);
            }

            .ra-brand-icon {
                width: 66px !important;
                height: 66px !important;
                border-radius: 19px !important;
                background: linear-gradient(145deg, rgba(255,255,255,.16), rgba(129,140,248,.11)) !important;
                border: 1px solid rgba(255,255,255,.18) !important;
                box-shadow: 0 14px 32px rgba(0,0,0,.18), inset 0 1px 0 rgba(255,255,255,.15);
                position: relative;
                overflow: hidden;
            }

            .ra-brand-icon::after {
                content: "";
                position: absolute;
                inset: -50%;
                background: conic-gradient(from 20deg, transparent, rgba(255,255,255,.18), transparent 35%);
                animation: ra-icon-sweep 5.5s linear infinite;
            }

            .ra-brand-icon span {
                position: relative;
                z-index: 2;
                font-size: 30px;
                filter: drop-shadow(0 4px 10px rgba(255,255,255,.20));
            }

            @keyframes ra-icon-sweep {
                to { transform: rotate(360deg); }
            }

            .ra-brand-copy {
                max-width: 900px;
            }

            .ra-brand-title {
                font-size: clamp(2.15rem, 4vw, 3.25rem) !important;
                letter-spacing: -.055em !important;
            }

            .ra-brand-subtitle {
                max-width: 820px;
                margin-top: 10px !important;
                font-size: 1.02rem !important;
                line-height: 1.62 !important;
                color: #c7d2e5 !important;
            }

            .ra-status-strip {
                margin-top: 18px !important;
                gap: 10px !important;
            }

            .ra-status {
                padding: 8px 13px !important;
                background: rgba(255,255,255,.065) !important;
                border-color: rgba(255,255,255,.14) !important;
                box-shadow: inset 0 1px 0 rgba(255,255,255,.05), 0 8px 20px rgba(0,0,0,.10) !important;
                color: #dbe5f5 !important;
            }

            .ra-product-strip {
                grid-template-columns: repeat(3, minmax(0,1fr));
                gap: 14px;
                margin: 16px 0 30px !important;
            }

            .ra-product-tile {
                min-height: 104px;
                display: grid;
                grid-template-columns: auto 1fr auto;
                align-items: center;
                gap: 13px;
                position: relative;
                overflow: hidden;
                padding: 17px 18px !important;
                border-radius: 19px !important;
                border: 1px solid rgba(255,255,255,.72) !important;
                background: rgba(255,255,255,.68) !important;
                box-shadow: 0 14px 38px rgba(31,41,55,.07), inset 0 1px 0 rgba(255,255,255,.9) !important;
                backdrop-filter: blur(18px);
                -webkit-backdrop-filter: blur(18px);
            }

            .ra-product-tile::before {
                content: "";
                position: absolute;
                inset: 0;
                background: linear-gradient(115deg, transparent 18%, rgba(255,255,255,.48) 50%, transparent 82%);
                transform: translateX(-120%);
                transition: transform .75s cubic-bezier(.2,.7,.2,1);
                pointer-events: none;
            }

            .ra-product-tile:hover::before { transform: translateX(120%); }

            .ra-product-tile::after {
                width: 150px !important;
                height: 150px !important;
                right: -55px !important;
                top: -75px !important;
                opacity: .65;
            }

            .ra-product-tile-knowledge::after { background: radial-gradient(circle, rgba(99,102,241,.18), transparent 68%) !important; }
            .ra-product-tile-operations::after { background: radial-gradient(circle, rgba(6,182,212,.17), transparent 68%) !important; }
            .ra-product-tile-automation::after { background: radial-gradient(circle, rgba(236,72,153,.16), transparent 68%) !important; }

            .ra-product-icon {
                position: relative;
                z-index: 2;
                width: 42px;
                height: 42px;
                display: flex;
                align-items: center;
                justify-content: center;
                border-radius: 13px;
                color: #fff;
                font-size: 20px;
                font-weight: 800;
                background: linear-gradient(145deg, #4f46e5, #7c3aed);
                box-shadow: 0 9px 20px rgba(79,70,229,.22);
            }

            .ra-product-tile-operations .ra-product-icon { background: linear-gradient(145deg, #0891b2, #2563eb); box-shadow: 0 9px 20px rgba(8,145,178,.20); }
            .ra-product-tile-automation .ra-product-icon { background: linear-gradient(145deg, #db2777, #7c3aed); box-shadow: 0 9px 20px rgba(219,39,119,.18); }

            .ra-product-copy { position: relative; z-index: 2; min-width: 0; }
            .ra-product-kicker { margin-bottom: 4px !important; font-size: .66rem !important; }
            .ra-product-value { font-size: .94rem !important; }
            .ra-product-desc {
                margin-top: 3px;
                color: #64748b;
                font-size: .72rem;
                line-height: 1.35;
            }

            .ra-product-arrow {
                position: relative;
                z-index: 2;
                width: 28px;
                height: 28px;
                display: flex;
                align-items: center;
                justify-content: center;
                border-radius: 50%;
                color: #64748b;
                background: rgba(255,255,255,.62);
                border: 1px solid rgba(148,163,184,.18);
                transition: transform .2s ease, color .2s ease, background .2s ease;
            }

            .ra-product-tile:hover .ra-product-arrow {
                transform: translate(2px,-2px);
                color: #4f46e5;
                background: #fff;
            }

            /* Product navigation: make Streamlit's tabs read like a premium workspace switcher. */
            .stTabs {
                margin-top: 6px;
            }

            .stTabs [data-baseweb="tab-list"] {
                position: relative;
                gap: 4px !important;
                padding: 5px !important;
                border: 1px solid rgba(148,163,184,.20) !important;
                border-radius: 16px !important;
                background: rgba(255,255,255,.52) !important;
                box-shadow: 0 12px 32px rgba(31,41,55,.055), inset 0 1px 0 rgba(255,255,255,.9) !important;
                backdrop-filter: blur(16px);
                -webkit-backdrop-filter: blur(16px);
            }

            .stTabs [data-baseweb="tab"] {
                min-height: 46px !important;
                padding: 9px 18px !important;
                border-radius: 12px !important;
                color: #64748b !important;
                font-weight: 800 !important;
                letter-spacing: -.01em;
                transition: background .2s ease, color .2s ease, transform .2s ease, box-shadow .2s ease !important;
            }

            .stTabs [data-baseweb="tab"]:hover {
                color: #334155 !important;
                background: rgba(255,255,255,.68) !important;
                transform: translateY(-1px);
            }

            .stTabs [data-baseweb="tab"][aria-selected="true"] {
                color: #312e81 !important;
                background: linear-gradient(135deg, rgba(238,242,255,.98), rgba(250,245,255,.98)) !important;
                box-shadow: 0 7px 20px rgba(79,70,229,.10), inset 0 1px 0 rgba(255,255,255,.95) !important;
            }

            .stTabs [data-baseweb="tab-highlight"] {
                display: none !important;
            }

            /* Dark-mode counterpart for the refreshed surfaces. */
            body:has(.ra-theme-marker-dark) .stApp {
                background:
                    radial-gradient(circle at 5% 10%, rgba(99,102,241,.22), transparent 24%),
                    radial-gradient(circle at 94% 12%, rgba(6,182,212,.14), transparent 24%),
                    radial-gradient(circle at 20% 85%, rgba(236,72,153,.10), transparent 25%),
                    linear-gradient(135deg, #050a14 0%, #0b1120 52%, #11182c 100%) !important;
            }

            body:has(.ra-theme-marker-dark) .ra-product-tile,
            body:has(.ra-theme-marker-dark) .stTabs [data-baseweb="tab-list"] {
                background: rgba(10,17,32,.72) !important;
                border-color: rgba(148,163,184,.16) !important;
                box-shadow: 0 18px 42px rgba(0,0,0,.22), inset 0 1px 0 rgba(255,255,255,.035) !important;
            }

            body:has(.ra-theme-marker-dark) .ra-product-desc { color: #94a3b8 !important; }
            body:has(.ra-theme-marker-dark) .ra-product-arrow { background: rgba(30,41,59,.72); color: #94a3b8; }
            body:has(.ra-theme-marker-dark) .stTabs [data-baseweb="tab"] { color: #94a3b8 !important; }
            body:has(.ra-theme-marker-dark) .stTabs [data-baseweb="tab"]:hover { color: #e2e8f0 !important; background: rgba(30,41,59,.72) !important; }
            body:has(.ra-theme-marker-dark) .stTabs [data-baseweb="tab"][aria-selected="true"] { color: #eef2ff !important; background: linear-gradient(135deg, rgba(79,70,229,.28), rgba(124,58,237,.20)) !important; }

            @media (max-width: 900px) {
                .ra-product-strip { grid-template-columns: 1fr !important; }
                .ra-product-tile { min-height: 88px; }
                .ra-hero-topline { align-items: flex-start; flex-direction: column; margin-bottom: 28px; }
                .ra-hero-system { display: none; }
            }

            @media (max-width: 600px) {
                .ra-hero { min-height: 0 !important; padding: 18px 18px 24px !important; }
                .ra-brand-title { font-size: 2rem !important; }
                .ra-brand-subtitle { font-size: .90rem !important; }
                .ra-brand-icon { width: 54px !important; height: 54px !important; }
                .ra-status-strip { margin-top: 18px !important; }
                .ra-status { font-size: .68rem !important; }
                .stTabs [data-baseweb="tab"] { padding: 8px 11px !important; font-size: .78rem !important; }
            }

            @media (prefers-reduced-motion: reduce) {
                .ra-live-dot,
                .ra-brand-icon::after,
                .ra-hero::before,
                .ra-hero::after { animation: none !important; }
                .ra-product-tile::before { display: none !important; }
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
    Clean the Cortex Agent response for customer-facing display.
    Removes process narration, internal execution language,
    and unfinished follow-up text.
    """
    if not answer:
        return ""

    text = str(answer).strip()

    # Remove common process narration at the beginning.
    opening_patterns = [
        r"^\s*I'll look into .*?\.\s*",
        r"^\s*I will look into .*?\.\s*",
        r"^\s*Let me check .*?\.\s*",
        r"^\s*Let me .*?\.\s*",
        r"^\s*I'll pull .*?\.\s*",
        r"^\s*I have .*?\.\s*",
        r"^\s*Now I'll .*?\.\s*",
        r"^\s*Here's .*?\.\s*",
        r"^\s*I found .*?\.\s*",
        r"^\s*I've found .*?\.\s*",
    ]

    for pattern in opening_patterns:
        text = re.sub(
            pattern,
            "",
            text,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )

    # Remove common internal/process statements.
    process_patterns = [
        r"(?im)^\s*.*?(?:tool call|tool execution|executing sql|running sql|querying the database).*?$",
        r"(?im)^\s*.*?(?:orchestration|routing decision|server skill|system action).*?$",
        r"(?im)^\s*.*?(?:request id|latency|response time).*?$",
    ]

    for pattern in process_patterns:
        text = re.sub(pattern, "", text)

    # Remove dangling one-word continuations.
    text = re.sub(
        r"(?m)\n\s*(?:What|How|Why|Would you like|Anything else)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Remove timeout / incomplete continuation text.
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

    # Remove unwanted follow-up questions at the end.
    text = re.sub(
        r"\s*Would you like (?:me|to) .*?$",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Normalize excessive blank lines.
    text = re.sub(r"\n{3,}", "\n\n", text)

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

    with st.expander("Agent activity", expanded=False):
        used_tools = getattr(response, "used_tools", None) or []

        displayed_tools = set()

        for tool in used_tools:
            normalized = str(tool).strip().lower()

            if "policysearch" in normalized or "cortex_search" in normalized:
                displayed_tools.add("PolicySearch — Cortex Search")

            elif "supportanalytics" in normalized or "cortex_analyst" in normalized:
                displayed_tools.add("SupportAnalytics — Cortex Analyst")

        if displayed_tools:
            for tool_name in (
                "PolicySearch — Cortex Search",
                "SupportAnalytics — Cortex Analyst",
            ):
                if tool_name in displayed_tools:
                    st.write(f"✓ {tool_name}")
        else:
            st.caption("No customer-facing tool activity was reported.")

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

    # ---------------------------------------------------------
    # Admin-only document upload
    # ---------------------------------------------------------

    st.markdown(
        """
        <div class="ra-section-label">Knowledge base</div>
        <div class="ra-section-title">Upload a support document</div>
        <div class="ra-helper">
            Add PDF, DOCX, Markdown, or TXT documents to the SupportAI knowledge base.
            Knowledge-base changes are managed by administrators.
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
            key="supportai_admin_document_uploader",
        )

        if uploaded_files:
            valid_files, upload_errors = validate_uploaded_files(uploaded_files)

            for error in upload_errors:
                logger.warning("Admin upload validation error: %s", error)
                st.error(error)

            if valid_files:
                detected_categories = {}

                for uploaded_file in valid_files:
                    file_bytes = uploaded_file.getvalue()
                    try:
                        detected_category = detect_category_cached(
                            controller,
                            file_bytes,
                            uploaded_file.name,
                        )
                    except Exception:
                        logger.exception(
                            "Admin upload category detection failed | filename=%s",
                            uploaded_file.name,
                        )
                        detected_category = "Unable to detect"

                    detected_categories[uploaded_file.name] = detected_category
                    file_size_mb = len(file_bytes) / (1024 * 1024)
                    st.caption(
                        f"{uploaded_file.name} • {file_size_mb:.2f} MB • "
                        f"Category: {detected_category}"
                    )

                if st.button(
                    "Upload to Snowflake",
                    type="primary",
                    width="stretch",
                    key="supportai_admin_upload_documents",
                ):
                    if controller.document_store is None:
                        logger.error(
                            "Admin document upload requested but document store is unavailable."
                        )
                        st.error(
                            "Document upload is currently unavailable. "
                            "Please check the Snowflake configuration."
                        )
                    else:
                        successful_uploads = 0
                        uploaded_document_ids = []

                        for uploaded_file in valid_files:
                            file_bytes = uploaded_file.getvalue()
                            upload_start = time.perf_counter()
                            progress_bar = st.progress(0)
                            progress_status = st.empty()

                            def upload_progress(percent, stage, detail=""):
                                progress_bar.progress(max(0, min(100, int(percent))))
                                message = str(stage)
                                if detail:
                                    message += f" • {detail}"
                                progress_status.caption(message)

                            try:
                                upload_progress(
                                    1,
                                    "Starting upload",
                                    uploaded_file.name,
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
                                upload_progress(100, "Indexed and ready")

                                stage_parts = str(staged_path).split("/", 2)
                                document_id = (
                                    stage_parts[1]
                                    if len(stage_parts) >= 2
                                    else "Unknown"
                                )
                                if document_id != "Unknown":
                                    uploaded_document_ids.append(document_id)

                                successful_uploads += 1
                                logger.info(
                                    "Admin document upload completed | filename=%s | duration=%.3fs",
                                    uploaded_file.name,
                                    time.perf_counter() - upload_start,
                                )
                            except Exception:
                                logger.exception(
                                    "Admin document upload failed | filename=%s | duration=%.3fs",
                                    uploaded_file.name,
                                    time.perf_counter() - upload_start,
                                )
                                st.error(
                                    f"Unable to upload {uploaded_file.name}. "
                                    "Please retry or check the document configuration."
                                )
                            finally:
                                progress_bar.empty()
                                progress_status.empty()

                        if successful_uploads:
                            st.session_state["supportai_pending_document_ids"] = sorted(
                                set(uploaded_document_ids)
                            )
                            st.success(
                                f"{successful_uploads} document(s) uploaded successfully "
                                "and added to the knowledge base."
                            )
                            _log_supportai_activity(
                                controller,
                                st.session_state.get("supportai_user"),
                                "DOCUMENT_UPLOAD",
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

    with st.container(border=True):
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

        try:
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
                _log_supportai_activity(
                    controller,
                    st.session_state.get("supportai_user"),
                    "QUERY",
                    mode="Policy RAG",
                    latency_seconds=elapsed,
                )

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
                _log_supportai_activity(
                    controller,
                    st.session_state.get("supportai_user"),
                    "QUERY",
                    mode="Support Analytics",
                    latency_seconds=elapsed,
                )

            else:
                response = controller.ask_agent(question.strip())
                request_status.empty()
                elapsed = time.perf_counter() - request_start
                logger.info(
                    "UI Cortex Agent completed | duration=%.3fs",
                    elapsed,
                )
                render_agent_response(response, elapsed)
                _log_supportai_activity(
                    controller,
                    st.session_state.get("supportai_user"),
                    "QUERY",
                    mode="Cortex Agent",
                    latency_seconds=elapsed,
                )

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
            logger.info(
                "SupportAI question processing finished; complete knowledge base remains active."
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

    # Render the animation as real HTML. Using st.markdown here can expose
    # nested div markup as literal text in Streamlit's Markdown renderer.
    if callable(getattr(st, "html", None)):
        st.html(welcome_html)
    else:
        st.markdown(welcome_html, unsafe_allow_html=True)


# ============================================================
# Authentication UI
# ============================================================


def initialize_auth_state() -> None:
    """Initialize authentication state for the current Streamlit session."""
    defaults = {
        "supportai_authenticated": False,
        "supportai_user": None,
        "supportai_auth_screen": "login",
        "supportai_auth_notice": None,
        "supportai_auth_reset_notice": None,
        "supportai_welcome_shown": False,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def inject_auth_styles(theme: str = "light") -> None:
    """Style the authentication experience as a fixed, viewport-fitted page."""
    dark = str(theme).strip().lower() == "dark"
    text = "#f8fafc" if dark else "#101828"
    muted = "#aeb8ca" if dark else "#667085"
    card = "rgba(13,20,38,.96)" if dark else "rgba(255,255,255,.96)"
    field = "rgba(255,255,255,.075)" if dark else "#f6f8fc"
    border = "rgba(148,163,184,.24)" if dark else "#dce2ec"

    st.markdown(
        f"""
    <style>
    /* AUTH PAGE — fixed to the viewport on desktop. */
    html:has(.supportai-auth-root),
    body:has(.supportai-auth-root),
    body:has(.supportai-auth-root) .stApp,
    body:has(.supportai-auth-root) [data-testid="stAppViewContainer"],
    body:has(.supportai-auth-root) [data-testid="stMain"] {{
        overflow: hidden !important;
        scrollbar-width: none !important;
        height:100% !important;
    }}
    body:has(.supportai-auth-root) ::-webkit-scrollbar {{
        display: none !important;
        width: 0 !important;
        height: 0 !important;
    }}
    body:has(.supportai-auth-root) section.main > div.block-container {{
        max-width: 100% !important;
        padding: 0 !important;
        margin: 0 !important;
    }}

    .st-key-supportai_auth_root {{
        width: min(1280px, calc(100vw - 56px)) !important;
        height: calc(100vh - 34px) !important;
        min-height: 0 !important;
        max-height: none !important;
        margin: 0 auto !important;
        padding: 14px 0 10px !important;
        box-sizing: border-box !important;
        display: flex !important;
        flex-direction: column !important;
        overflow: hidden !important;
    }}
    .supportai-auth-brandline {{
        flex: 0 0 auto;
        display:flex;
        align-items:center;
        justify-content:space-between;
        margin:0 0 10px;
        padding:0 4px;
        justify-content:flex-start;
    }}
    .supportai-auth-brand {{
        display:flex; align-items:center; gap:10px;
        color:{text}; font-weight:850; font-size:1rem;
    }}
    .supportai-auth-brand-mark {{
        width:36px; height:36px; border-radius:12px;
        display:grid; place-items:center; color:#fff;
        background:linear-gradient(135deg,#4f46e5,#9333ea);
        box-shadow:0 10px 24px rgba(79,70,229,.25);
    }}
    .supportai-auth-security {{ display:none !important; }}

    .st-key-supportai_auth_root > div[data-testid="stHorizontalBlock"] {{
        flex:1 1 auto !important;
        min-height:0 !important;
        height:calc(100% - 52px) !important;
        align-items:stretch !important;
    }}
    .st-key-supportai_auth_root > div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {{
        min-height:0 !important;
        height:100% !important;
    }}

    .supportai-auth-promo {{
        height:100%; min-height:0; box-sizing:border-box;
        padding:30px 34px 26px;
        position:relative; overflow:hidden; color:#fff;
        border-radius:28px;
        background:
            radial-gradient(circle at 96% 4%,rgba(99,102,241,.72),transparent 31%),
            radial-gradient(circle at 2% 100%,rgba(217,70,239,.30),transparent 32%),
            linear-gradient(145deg,#080e1e,#121c3d 57%,#293b91);
        box-shadow:0 28px 70px rgba(15,23,42,.18);
    }}
    .supportai-auth-promo:before {{
        content:""; position:absolute; width:330px; height:330px;
        right:-155px; top:-150px; border:1px solid rgba(255,255,255,.13);
        border-radius:50%;
        box-shadow:0 0 0 52px rgba(255,255,255,.025),0 0 0 104px rgba(255,255,255,.015);
    }}
    .supportai-auth-promo:after {{
        content:""; position:absolute; width:220px; height:220px;
        left:-125px; bottom:-135px; border-radius:50%;
        background:rgba(217,70,239,.14); filter:blur(10px);
    }}
    .supportai-auth-promo-content {{ position:relative; z-index:2; }}
    .supportai-auth-promo-mark {{
        width:52px; height:52px; border-radius:17px;
        display:grid; place-items:center; font-size:1.4rem;
        background:rgba(255,255,255,.10); border:1px solid rgba(255,255,255,.20);
        box-shadow:inset 0 1px rgba(255,255,255,.15);
    }}
    .supportai-auth-promo-title {{
        margin-top:18px; font-size:clamp(1.85rem,2.9vw,2.75rem);
        line-height:1.02; font-weight:900; letter-spacing:-.055em;
    }}
    .supportai-auth-promo-copy {{
        max-width:500px; margin-top:12px; color:rgba(255,255,255,.70);
        font-size:.91rem; line-height:1.6;
    }}
    .supportai-auth-features {{ display:grid; gap:10px; margin-top:26px; }}
    .supportai-auth-feature {{
        display:flex; align-items:center; gap:12px; padding:12px 14px;
        border:1px solid rgba(255,255,255,.12); border-radius:14px;
        background:rgba(255,255,255,.055);
        box-shadow:inset 0 1px rgba(255,255,255,.04);
    }}
    .supportai-auth-feature-icon {{
        width:34px; height:34px; flex:0 0 34px; display:grid; place-items:center;
        border-radius:10px; background:rgba(255,255,255,.10);
    }}
    .supportai-auth-feature-title {{ font-size:.78rem; font-weight:800; }}
    .supportai-auth-feature-copy {{ margin-top:2px; color:rgba(255,255,255,.52); font-size:.64rem; }}
    .supportai-auth-promo-footer {{
        position:absolute; left:42px; bottom:22px; z-index:2;
        color:rgba(255,255,255,.40); font-size:.60rem; letter-spacing:.07em;
    }}

    .st-key-supportai_auth_form_wrap {{
        height:100% !important; min-height:0 !important;
        padding:26px 32px !important; box-sizing:border-box !important;
        display:flex !important; flex-direction:column !important; justify-content:center !important;
        border:1px solid {border} !important; border-radius:28px !important;
        background:{card} !important;
        box-shadow:0 28px 70px rgba(15,23,42,.10) !important;
        backdrop-filter:blur(18px); -webkit-backdrop-filter:blur(18px);
        overflow:hidden !important;
    }}
    .supportai-auth-form-title {{
        color:{text}; font-size:clamp(1.65rem,2.2vw,2.2rem);
        line-height:1.08; font-weight:900; letter-spacing:-.045em;
    }}
    .supportai-auth-form-copy {{
        margin:7px 0 18px; color:{muted}; font-size:.76rem; line-height:1.45;
    }}
    .supportai-auth-admin-link {{ color:{muted}; font-size:.64rem; }}
    .st-key-supportai_auth_admin_button button {{
        min-height:36px !important; height:36px !important; border-radius:11px !important;
        padding:0 12px !important; font-size:.66rem !important; font-weight:800 !important;
        border:1px solid {border} !important; background:rgba(255,255,255,.42) !important;
        box-shadow:0 8px 18px rgba(15,23,42,.06), inset 0 1px rgba(255,255,255,.9) !important;
        backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
    }}
    .st-key-supportai_auth_admin_button button:hover {{
        transform:translateY(-1px); border-color:rgba(99,102,241,.28) !important;
        background:rgba(255,255,255,.7) !important;
    }}
    .supportai-auth-admin-badge {{
        display:inline-flex; padding:6px 10px; border:1px solid {border};
        border-radius:999px; color:{muted}; font-size:.62rem; font-weight:750; margin-bottom:13px;
    }}
    .supportai-auth-footer {{ margin-top:10px; color:{muted}; text-align:center; font-size:.58rem; }}
    [data-testid="stForm"] {{ border:0 !important; padding:0 !important; background:transparent !important; }}
    .st-key-supportai_auth_form_wrap [data-testid="stTextInput"] label {{
        color:{text} !important; font-size:.70rem !important; font-weight:700 !important;
    }}
    .st-key-supportai_auth_form_wrap [data-testid="stTextInput"] input {{
        min-height:42px !important; height:42px !important; border-radius:11px !important;
        background:{field} !important; border:1px solid {border} !important;
    }}
    .st-key-supportai_auth_form_wrap button[kind="primaryFormSubmit"] {{
        min-height:42px !important; border-radius:11px !important; font-weight:800 !important;
    }}
    .st-key-supportai_auth_form_wrap [data-baseweb="tab-list"] {{
        gap:26px; border-bottom:1px solid {border}; margin-bottom:18px;
    }}
    .st-key-supportai_auth_form_wrap [data-baseweb="tab"] {{ padding:8px 0 11px; font-size:.78rem; }}

    @media(max-height:760px) and (min-width:721px) {{
        .st-key-supportai_auth_root {{ padding-top:8px !important; padding-bottom:4px !important; }}
        .supportai-auth-brandline {{ margin-bottom:6px; }}
        .supportai-auth-promo {{ padding:26px 30px 22px; }}
        .supportai-auth-promo-title {{ font-size:2.15rem; }}
        .supportai-auth-promo-copy {{ font-size:.82rem; margin-top:10px; }}
        .supportai-auth-features {{ margin-top:16px; gap:7px; }}
        .supportai-auth-feature {{ padding:9px 11px; }}
        .supportai-auth-promo-footer {{ bottom:14px; }}
        .st-key-supportai_auth_form_wrap {{ padding:22px 28px !important; }}
        .supportai-auth-form-copy {{ margin:5px 0 12px; }}
        .st-key-supportai_auth_form_wrap [data-testid="stTextInput"] input {{ min-height:40px !important; height:40px !important; }}
        .st-key-supportai_auth_form_wrap button[kind="primaryFormSubmit"] {{ min-height:40px !important; }}
    }}
    @media(max-width:950px) {{
        .st-key-supportai_auth_root {{ width:calc(100vw - 36px) !important; min-height:590px !important; padding-top:16px !important; }}
        .supportai-auth-promo {{ padding:30px; }}
        .st-key-supportai_auth_form_wrap {{ padding:30px !important; }}
        .supportai-auth-promo-title {{ font-size:2rem; }}
        .supportai-auth-feature {{ padding:9px 11px; }}
    }}
    @media(max-width:720px) {{
        html:has(.supportai-auth-root), body:has(.supportai-auth-root) {{ overflow:auto !important; }}
        body:has(.supportai-auth-root) section.main > div.block-container {{ padding:0 !important; }}
        .st-key-supportai_auth_root {{ width:calc(100vw - 20px) !important; height:auto !important; min-height:0 !important; max-height:none !important; padding:12px 0 14px !important; overflow:visible !important; }}
        .st-key-supportai_auth_root > div[data-testid="stHorizontalBlock"] {{ height:auto !important; display:block !important; }}
        .st-key-supportai_auth_root > div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {{ height:auto !important; }}
        .supportai-auth-promo {{ display:none; }}
        .supportai-auth-security {{ display:none; }}
        .st-key-supportai_auth_form_wrap {{ min-height:520px !important; height:auto !important; padding:28px 24px !important; }}
    }}
    </style>
    """,
        unsafe_allow_html=True,
    )


def _auth_service(controller: RetailAssistController) -> AuthService:
    """Reuse the controller's Snowflake session when available."""
    return AuthService(session=getattr(controller, "session", None))


def _safe_auth_close(auth: AuthService) -> None:
    """Close an auth service safely."""
    try:
        auth.close()
    except Exception:
        logger.exception("Authentication service cleanup failed.")


def _log_supportai_activity(
    controller: RetailAssistController,
    user: dict | None,
    activity_type: str,
    mode: str | None = None,
    status: str = "SUCCESS",
    latency_seconds: float | None = None,
) -> None:
    """Best-effort activity logging; activity tracking must never break the app."""
    user = user or {}
    user_id = str(user.get("user_id") or "").strip()
    if not user_id:
        return

    activity_service = None
    try:
        activity_service = ActivityService(session=getattr(controller, "session", None))
        activity_service.log_activity(
            user_id=user_id,
            activity_type=activity_type,
            mode=mode,
            status=status,
            latency_seconds=latency_seconds,
        )
    except Exception:
        logger.exception("SupportAI activity logging failed | type=%s", activity_type)
    finally:
        if activity_service is not None:
            activity_service.close()


def _complete_login(
    user: dict,
    expected_role: str,
    controller: RetailAssistController | None = None,
) -> bool:
    """Validate the database role and establish authenticated session state."""
    actual_role = str(user.get("role") or "").strip().upper()
    expected_role = expected_role.upper()

    if actual_role != expected_role:
        if actual_role == "ADMIN":
            st.error("This is an admin account. Please use the Admin Portal.")
        else:
            st.error("This is a user account. Please use User Sign In.")
        return False

    st.session_state["supportai_authenticated"] = True
    st.session_state["supportai_user"] = user
    st.session_state["supportai_auth_screen"] = "login"
    st.session_state["supportai_welcome_shown"] = False

    if controller is not None:
        _log_supportai_activity(
            controller,
            user,
            "LOGIN",
        )

    logger.info(
        "SupportAI login successful | user_id=%s | role=%s",
        user.get("user_id"),
        actual_role,
    )
    st.rerun()
    return True


def _logout_supportai(controller: RetailAssistController | None = None) -> None:
    """Log out the current authentication session."""
    current_user = st.session_state.get("supportai_user") or {}

    if controller is not None and current_user:
        _log_supportai_activity(
            controller,
            current_user,
            "LOGOUT",
        )

    st.session_state["supportai_authenticated"] = False
    st.session_state["supportai_user"] = None
    st.session_state["supportai_auth_screen"] = "login"
    st.session_state["supportai_auth_notice"] = None
    st.session_state["supportai_welcome_shown"] = False
    st.rerun()


def render_user_login_form(controller: RetailAssistController) -> None:
    """Render the normal user sign-in form."""
    with st.form("supportai_user_login_form", clear_on_submit=False):
        email = st.text_input(
            "Email",
            placeholder="you@example.com",
            key="supportai_user_login_email",
        )
        password = st.text_input(
            "Password",
            type="password",
            placeholder="Enter your password",
            key="supportai_user_login_password",
        )
        submitted = st.form_submit_button(
            "Sign In",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        return

    if not email.strip() or not password:
        st.error("Please enter your email and password.")
        return

    auth = _auth_service(controller)
    try:
        user = auth.login(email=email, password=password)
        if user is None:
            status = getattr(auth, "last_login_status", "")
            if status == "inactive":
                st.error("This account is inactive. Please contact an administrator.")
            elif status == "password_reset_pending_verification":
                st.warning(
                    "Your password reset has been completed, but an administrator must "
                    "verify the reset before you can sign in with the new password."
                )
            elif status == "password_reset_rejected":
                st.error(
                    "Your latest password reset was rejected by an administrator. "
                    "Please request a new password reset link."
                )
            else:
                st.error("Invalid email or password.")
            return
        _complete_login(user, "USER", controller)
    except Exception:
        logger.exception("SupportAI user login failed.")
        st.error("Unable to sign in right now. Please try again.")
    finally:
        _safe_auth_close(auth)


def render_admin_login_form(controller: RetailAssistController) -> None:
    """Render the admin portal sign-in form only."""
    with st.form("supportai_admin_login_form", clear_on_submit=False):
        email = st.text_input(
            "Admin email",
            placeholder="admin@example.com",
            key="supportai_admin_login_email",
        )
        password = st.text_input(
            "Admin password",
            type="password",
            placeholder="Enter your admin password",
            key="supportai_admin_login_password",
        )
        submitted = st.form_submit_button(
            "Enter Admin Portal",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        return

    if not email.strip() or not password:
        st.error("Please enter your admin email and password.")
        return

    auth = _auth_service(controller)
    try:
        user = auth.login(email=email, password=password)
        if user is None:
            if getattr(auth, "last_login_status", "") == "inactive":
                st.error(
                    "This administrator account is inactive. Please contact an administrator."
                )
            else:
                st.error("Invalid admin email or password.")
            return
        _complete_login(user, "ADMIN", controller)
    except Exception:
        logger.exception("SupportAI admin login failed.")
        st.error("Unable to sign in right now. Please try again.")
    finally:
        _safe_auth_close(auth)


def _get_password_reset_query() -> tuple[str, str]:
    """Read reset identifiers from the emailed Streamlit query string."""
    try:
        reset_id = str(st.query_params.get("reset_id") or "").strip()
        token = str(st.query_params.get("token") or "").strip()
    except Exception:
        return "", ""
    return reset_id, token


def _clear_password_reset_query() -> None:
    """Remove password-reset credentials from the browser URL."""
    try:
        if "reset_id" in st.query_params:
            del st.query_params["reset_id"]
        if "token" in st.query_params:
            del st.query_params["token"]
    except Exception:
        logger.debug("Unable to clear password-reset query parameters.")


def render_password_reset_form(
    controller: RetailAssistController,
    reset_id: str,
    token: str,
) -> None:
    """Render the password form reached from a secure emailed reset link."""
    st.markdown(
        '<div class="supportai-auth-form-copy">Create a new password using the secure link sent to your registered email address. The link is temporary and can only be used once.</div>',
        unsafe_allow_html=True,
    )

    auth = _auth_service(controller)
    token_valid = False
    try:
        token_valid = bool(
            auth.validate_password_reset_token(
                reset_id=reset_id,
                token=token,
            )
        )
    except ValueError as exc:
        st.error(str(exc))
    except Exception:
        logger.exception(
            "Failed to validate password reset token | reset_id=%s", reset_id
        )
        st.error("This password-reset link could not be validated.")
    finally:
        _safe_auth_close(auth)

    if not token_valid:
        st.info(
            "Please request a new password-reset link if this link has expired or was already used."
        )
        if st.button(
            "Request a New Reset Link",
            key="supportai_reset_request_new_link",
            type="primary",
        ):
            _clear_password_reset_query()
            st.session_state["supportai_auth_screen"] = "forgot_password"
            st.rerun()
        return

    with st.form("supportai_password_reset_form", clear_on_submit=False):
        new_password = st.text_input(
            "New password",
            type="password",
            placeholder="At least 8 characters",
            key="supportai_password_reset_new_password",
        )
        confirm_password = st.text_input(
            "Confirm new password",
            type="password",
            placeholder="Re-enter your new password",
            key="supportai_password_reset_confirm_password",
        )
        submitted = st.form_submit_button(
            "Create New Password",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        return

    if not new_password or not confirm_password:
        st.error("Please enter and confirm your new password.")
        return

    if new_password != confirm_password:
        st.error("Passwords do not match.")
        return

    auth = _auth_service(controller)
    try:
        auth.complete_password_reset(
            reset_id=reset_id,
            token=token,
            new_password=new_password,
        )
        _clear_password_reset_query()
        st.session_state["supportai_auth_screen"] = "login"
        st.session_state["supportai_auth_notice"] = (
            "Your password has been reset successfully. An administrator must verify "
            "the reset before you can sign in with the new password."
        )
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))
    except Exception:
        logger.exception("Failed to complete password reset | reset_id=%s", reset_id)
        st.error(
            "Unable to complete this password reset. Please request a new reset link if needed."
        )
    finally:
        _safe_auth_close(auth)


def render_forgot_password_form(controller: RetailAssistController) -> None:
    """Render the secure password-reset request form."""
    st.markdown(
        '<div class="supportai-auth-form-copy">Enter your registered account email. If the account is eligible, SupportAI will email you a secure, time-limited reset link. Your password is never sent by email.</div>',
        unsafe_allow_html=True,
    )

    with st.form("supportai_forgot_password_form", clear_on_submit=False):
        email = st.text_input(
            "Account email",
            placeholder="you@example.com",
            key="supportai_forgot_password_email",
        )
        submitted = st.form_submit_button(
            "Email Me a Reset Link",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        return

    if not email.strip():
        st.error("Please enter your registered email address.")
        return

    auth = _auth_service(controller)
    try:
        auth.request_password_reset(email=email)
        st.session_state["supportai_auth_reset_notice"] = (
            "If this account is eligible, a secure reset link has been sent to the "
            "registered email address. Check your inbox and follow the link to create "
            "your new password."
        )
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))
    except Exception:
        logger.exception("SupportAI password reset request failed.")
        st.error("Unable to send the reset link right now. Please try again.")
    finally:
        _safe_auth_close(auth)


def render_user_signup_form(controller: RetailAssistController) -> None:
    """Render public signup; every new account is always created as USER."""
    with st.form("supportai_user_signup_form", clear_on_submit=False):
        name = st.text_input(
            "Full name",
            placeholder="Your name",
            key="supportai_signup_name",
        )
        email = st.text_input(
            "Email",
            placeholder="you@example.com",
            key="supportai_signup_email",
        )
        password = st.text_input(
            "Password",
            type="password",
            placeholder="At least 8 characters",
            key="supportai_signup_password",
        )
        confirm_password = st.text_input(
            "Confirm password",
            type="password",
            placeholder="Re-enter your password",
            key="supportai_signup_confirm_password",
        )
        submitted = st.form_submit_button(
            "Create Account",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        return

    if password != confirm_password:
        st.error("Passwords do not match.")
        return

    auth = _auth_service(controller)
    try:
        user = auth.signup(name=name, email=email, password=password)
        logger.info("SupportAI signup successful | user_id=%s", user.get("user_id"))
        st.session_state["supportai_auth_screen"] = "login"
        st.session_state["supportai_auth_notice"] = (
            "Account created successfully. You can now sign in."
        )
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))
    except Exception:
        logger.exception("SupportAI signup failed.")
        st.error("Unable to create the account right now. Please try again.")
    finally:
        _safe_auth_close(auth)


def render_auth_screen(controller: RetailAssistController, theme: str) -> bool:
    """Render the SupportAI authentication and secure password-reset experience."""
    initialize_auth_state()

    reset_id, reset_token = _get_password_reset_query()
    has_reset_link = bool(reset_id and reset_token)

    if has_reset_link and st.session_state.get("supportai_authenticated"):
        st.session_state["supportai_authenticated"] = False
        st.session_state["supportai_user"] = None
        st.session_state["supportai_auth_screen"] = "password_reset"

    if has_reset_link:
        st.session_state["supportai_auth_screen"] = "password_reset"

    if st.session_state.get("supportai_authenticated"):
        return True

    inject_auth_styles(theme)
    notice = st.session_state.pop("supportai_auth_notice", None)
    auth_screen = st.session_state.get("supportai_auth_screen")
    admin_mode = auth_screen == "admin"
    reset_mode = auth_screen == "forgot_password"
    password_reset_mode = auth_screen == "password_reset"
    reset_notice = st.session_state.pop("supportai_auth_reset_notice", None)

    with st.container(key="supportai_auth_root", border=False):
        st.markdown(
            """
            <div class="supportai-auth-brandline">
                <div class="supportai-auth-brand">
                    <span class="supportai-auth-brand-mark">✦</span>
                    <span>SupportAI</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        left, right = st.columns([0.94, 1.06], gap="small", vertical_alignment="top")

        with left:
            if admin_mode:
                title = "Manage the<br>workspace."
                copy = "A protected administrator gateway for the people responsible for the SupportAI environment."
                features = [
                    ("♙", "Administrator controls", "Provisioned accounts only."),
                    ("▦", "Operational visibility", "Governed support intelligence."),
                    ("✓", "Secure access", "Role verified against Snowflake."),
                ]
            elif reset_mode or password_reset_mode:
                title = "Get back to<br>your workspace."
                copy = "Recover your SupportAI account securely using email verification and administrator review."
                features = [
                    (
                        "✓",
                        "Secure email link",
                        "The reset token is temporary and one-time.",
                    ),
                    (
                        "♙",
                        "Administrator verified",
                        "Completed resets require admin approval before sign-in.",
                    ),
                    (
                        "✦",
                        "Protected access",
                        "Passwords are hashed and never sent by email.",
                    ),
                ]
            else:
                title = "Support work.<br>Made intelligent."
                copy = "One secure workspace for policy-grounded answers, live support-ticket intelligence and Cortex Agent orchestration."
                features = [
                    ("⌁", "Trusted policy answers", "Grounded in approved knowledge."),
                    (
                        "◫",
                        "Live support analytics",
                        "Understand ticket operations naturally.",
                    ),
                    ("✧", "Cortex Agent", "Route questions across trusted tools."),
                ]

            feature_html = "".join(
                f"""<div class="supportai-auth-feature"><div class="supportai-auth-feature-icon">{icon}</div><div><div class="supportai-auth-feature-title">{heading}</div><div class="supportai-auth-feature-copy">{desc}</div></div></div>"""
                for icon, heading, desc in features
            )
            promo_html = textwrap.dedent(
                f"""
                <div class="supportai-auth-promo">
                    <div class="supportai-auth-promo-content">
                        <div class="supportai-auth-promo-mark">✦</div>
                        <div class="supportai-auth-promo-title">{title}</div>
                        <div class="supportai-auth-promo-copy">{copy}</div>
                        <div class="supportai-auth-features">{feature_html}</div>
                    </div>
                </div>
                """
            ).strip()
            if callable(getattr(st, "html", None)):
                st.html(promo_html)
            else:
                st.markdown(promo_html, unsafe_allow_html=True)

        with right:
            with st.container(key="supportai_auth_form_wrap", border=True):
                if admin_mode:
                    st.markdown(
                        '<div class="supportai-auth-admin-badge">✦ Administrator portal</div>'
                        '<div class="supportai-auth-form-title">Welcome, admin.</div>'
                        '<div class="supportai-auth-form-copy">Sign in with your provisioned administrator account.</div>',
                        unsafe_allow_html=True,
                    )
                    render_admin_login_form(controller)
                    if st.button(
                        "← Back to user sign in",
                        key="supportai_back_to_user",
                        use_container_width=False,
                    ):
                        st.session_state["supportai_auth_screen"] = "login"
                        st.rerun()
                elif password_reset_mode:
                    st.markdown(
                        '<div class="supportai-auth-admin-badge">✦ Secure password reset</div>'
                        '<div class="supportai-auth-form-title">Create a new password</div>'
                        '<div class="supportai-auth-form-copy">Your reset link was delivered to your registered email address.</div>',
                        unsafe_allow_html=True,
                    )
                    render_password_reset_form(controller, reset_id, reset_token)
                    if st.button(
                        "← Back to sign in",
                        key="supportai_back_from_password_reset",
                        use_container_width=False,
                    ):
                        _clear_password_reset_query()
                        st.session_state["supportai_auth_screen"] = "login"
                        st.rerun()
                elif reset_mode:
                    st.markdown(
                        '<div class="supportai-auth-admin-badge">✦ Password recovery</div>'
                        '<div class="supportai-auth-form-title">Forgot your password?</div>'
                        '<div class="supportai-auth-form-copy">We will send a secure reset link directly to your registered email.</div>',
                        unsafe_allow_html=True,
                    )
                    if reset_notice:
                        st.success(reset_notice)
                    render_forgot_password_form(controller)
                    if st.button(
                        "← Back to sign in",
                        key="supportai_back_from_forgot_password",
                        use_container_width=False,
                    ):
                        st.session_state["supportai_auth_screen"] = "login"
                        st.rerun()
                else:
                    auth_title_left, auth_title_right = st.columns(
                        [2.7, 1], gap="small", vertical_alignment="top"
                    )
                    with auth_title_left:
                        st.markdown(
                            '<div class="supportai-auth-form-title">Welcome!</div>'
                            '<div class="supportai-auth-form-copy">Sign in to continue to your SupportAI workspace.</div>',
                            unsafe_allow_html=True,
                        )
                    with auth_title_right:
                        with st.container(
                            key="supportai_auth_admin_button", border=False
                        ):
                            if st.button(
                                "Admin Portal ↗",
                                key="supportai_open_admin_portal",
                                use_container_width=True,
                            ):
                                st.session_state["supportai_auth_screen"] = "admin"
                                st.rerun()

                    if notice:
                        st.success(notice)

                    sign_in_tab, create_tab = st.tabs(["Sign In", "Create Account"])
                    with sign_in_tab:
                        render_user_login_form(controller)
                        if st.button(
                            "Forgot password?",
                            key="supportai_forgot_password_link",
                            use_container_width=False,
                        ):
                            st.session_state["supportai_auth_screen"] = (
                                "forgot_password"
                            )
                            st.rerun()
                    with create_tab:
                        st.markdown(
                            '<div class="supportai-auth-form-copy">Create a standard user account. Administrator accounts are provisioned separately.</div>',
                            unsafe_allow_html=True,
                        )
                        render_user_signup_form(controller)

                    st.markdown(
                        '<div class="supportai-auth-divider"></div>'
                        '<div class="supportai-auth-admin-link">Administrator access is restricted and provisioned separately.</div>',
                        unsafe_allow_html=True,
                    )

    return False


def inject_account_bar_styles(theme: str = "light") -> None:
    """Style the authenticated account bar without relying on auth-only CSS."""
    dark = str(theme).strip().lower() == "dark"
    text = "#f8fafc" if dark else "#172033"
    muted = "#b2bdcf" if dark else "#667085"
    surface = "rgba(17,24,39,.52)" if dark else "rgba(255,255,255,.48)"
    border = "rgba(255,255,255,.18)" if dark else "rgba(255,255,255,.72)"

    st.markdown(
        f"""
    <style>
    .st-key-supportai_account_shell {{
        width:min(1280px,calc(100vw - 56px)) !important;
        min-height:92px !important;
        margin:12px auto 18px !important;
        padding:12px 18px !important;
        box-sizing:border-box !important;
        border:1px solid {border} !important;
        border-radius:22px !important;
        background:linear-gradient(135deg,{surface},rgba(255,255,255,.16)) !important;
        box-shadow:0 16px 38px rgba(15,23,42,.08),inset 0 1px rgba(255,255,255,.86) !important;
        backdrop-filter:blur(24px) saturate(155%) !important;
        -webkit-backdrop-filter:blur(24px) saturate(155%) !important;
    }}
    .st-key-supportai_account_shell > div {{ min-height:0 !important; }}
    .st-key-supportai_account_shell [data-testid="stHorizontalBlock"] {{
        align-items:center !important;
        min-height:64px !important;
    }}
    .supportai-account-inner {{
        display:flex !important; align-items:center !important; gap:11px !important;
        min-width:0 !important; padding:0 3px !important;
    }}
    .supportai-account-avatar {{
        width:56px; height:56px; flex:0 0 56px; border-radius:16px;
        display:grid; place-items:center; color:#fff;
        background:linear-gradient(135deg,#4f46e5,#9333ea);
        box-shadow:0 8px 20px rgba(79,70,229,.24);
        font-size:.70rem; font-weight:850;
    }}
    .supportai-account-copy {{ min-width:0; }}
    .supportai-account-primary {{
        display:flex; align-items:center; gap:9px; min-width:0; white-space:nowrap;
    }}
    .supportai-account-subtitle {{
        margin-top:3px; color:{muted}; font-size:.60rem; line-height:1.3;
    }}
    .supportai-account-name {{ color:{text}; font-size:.80rem; font-weight:800; }}
    .supportai-account-role {{
        padding:4px 8px; border-radius:999px;
        background:rgba(99,102,241,.11); border:1px solid rgba(99,102,241,.18);
        color:#6366f1; font-size:.53rem; font-weight:850; letter-spacing:.06em;
    }}
    .st-key-supportai_account_shell button {{
        min-height:48px !important; height:48px !important; border-radius:14px !important;
        border:1px solid rgba(255,255,255,.72) !important;
        background:rgba(255,255,255,.44) !important;
        box-shadow:0 8px 20px rgba(15,23,42,.07),inset 0 1px rgba(255,255,255,.9) !important;
        backdrop-filter:blur(14px) !important; -webkit-backdrop-filter:blur(14px) !important;
        font-weight:750 !important;
    }}
    .st-key-supportai_account_shell button:hover {{
        background:rgba(255,255,255,.68) !important;
        border-color:rgba(99,102,241,.28) !important;
        transform:translateY(-1px);
    }}
    @media(max-width:720px) {{
        .st-key-supportai_account_shell {{ width:calc(100vw - 20px) !important; min-height:82px !important; margin:6px auto 10px !important; padding:10px 12px !important; }}
    }}
    </style>
    """,
        unsafe_allow_html=True,
    )


def render_authenticated_header(
    controller: RetailAssistController,
    theme: str = "light",
) -> None:
    """Render one aligned glass account bar for both users and admins."""
    inject_account_bar_styles(theme)
    user = st.session_state.get("supportai_user") or {}
    raw_name = str(user.get("name") or "User").strip()
    role = str(user.get("role") or "USER").upper().strip()
    initials = "".join(part[0] for part in raw_name.split()[:2]).upper() or "U"

    with st.container(key="supportai_account_shell", border=False):
        left, middle, right = st.columns(
            [0.7, 7.6, 1.5], gap="small", vertical_alignment="center"
        )
        with left:
            st.markdown(
                f'<div class="supportai-account-avatar">{html.escape(initials)}</div>',
                unsafe_allow_html=True,
            )
        with middle:
            st.markdown(
                f"""<div class="supportai-account-copy">
                    <div class="supportai-account-primary">
                        <span class="supportai-account-name">{html.escape(raw_name)}</span>
                        <span class="supportai-account-role">{html.escape(role)}</span>
                    </div>
                    <div class="supportai-account-subtitle">Signed in to your SupportAI workspace</div>
                </div>""",
                unsafe_allow_html=True,
            )
        with right:
            if st.button(
                "Sign out",
                key="supportai_logout_button",
                use_container_width=True,
            ):
                _logout_supportai(controller)


# ============================================================
# Admin Dashboard
# ============================================================


def inject_admin_dashboard_styles(theme: str = "light") -> None:
    """Render the premium, motion-rich administrator control center."""
    dark = str(theme).strip().lower() == "dark"
    text_color = "#eef3ff" if dark else "#152033"
    muted = "#a9b5c9" if dark else "#69778e"
    surface = "rgba(9,17,36,.72)" if dark else "rgba(255,255,255,.70)"
    soft_surface = "rgba(15,23,42,.74)" if dark else "rgba(255,255,255,.52)"
    border = "rgba(255,255,255,.18)" if dark else "rgba(255,255,255,.82)"
    card_border = "rgba(99,102,241,.20)" if dark else "rgba(99,102,241,.16)"

    st.markdown(
        f"""
    <style>
    /* ===================== ADMIN COMMAND CENTER ===================== */
    .st-key-supportai_admin_dashboard {{
        width:min(1360px,calc(100vw - 52px)) !important;
        margin:0 auto 38px !important;
        position:relative;
        isolation:isolate;
    }}
    .st-key-supportai_admin_dashboard::before,
    .st-key-supportai_admin_dashboard::after {{
        content:"";
        position:absolute;
        pointer-events:none;
        z-index:-1;
        border-radius:50%;
        filter:blur(26px);
    }}
    .st-key-supportai_admin_dashboard::before {{
        width:420px;height:420px;left:-190px;top:90px;
        background:radial-gradient(circle,rgba(99,102,241,.15),transparent 68%);
        animation:sai-admin-drift-a 12s ease-in-out infinite alternate;
    }}
    .st-key-supportai_admin_dashboard::after {{
        width:380px;height:380px;right:-170px;top:420px;
        background:radial-gradient(circle,rgba(14,165,233,.13),transparent 68%);
        animation:sai-admin-drift-b 15s ease-in-out infinite alternate;
    }}
    @keyframes sai-admin-drift-a {{ from {{transform:translate3d(0,0,0) scale(.96)}} to {{transform:translate3d(55px,35px,0) scale(1.06)}} }}
    @keyframes sai-admin-drift-b {{ from {{transform:translate3d(0,0,0) scale(1)}} to {{transform:translate3d(-45px,-25px,0) scale(1.08)}} }}
    @keyframes sai-admin-float {{ 0%,100%{{transform:translateY(0)}} 50%{{transform:translateY(-7px)}} }}
    @keyframes sai-admin-pulse {{ 0%,100%{{box-shadow:0 0 0 0 rgba(34,197,94,.20)}} 50%{{box-shadow:0 0 0 7px rgba(34,197,94,0)}} }}
    @keyframes sai-admin-sweep {{ 0%{{transform:translateX(-130%)}} 35%,100%{{transform:translateX(130%)}} }}
    @keyframes sai-admin-grid {{ from{{background-position:0 0,0 0}} to{{background-position:36px 36px,36px 36px}} }}
    .st-key-supportai_admin_dashboard > div {{position:relative;z-index:1}}

    /* tab rail */
    .st-key-supportai_admin_dashboard [data-baseweb="tab-list"] {{
        display:flex;gap:6px !important;padding:7px !important;margin:0 0 20px !important;
        border:1px solid {border} !important;border-radius:22px !important;background:{surface} !important;
        backdrop-filter:blur(28px) saturate(160%) !important;-webkit-backdrop-filter:blur(28px) saturate(160%) !important;
        box-shadow:0 20px 55px rgba(15,23,42,.10),inset 0 1px rgba(255,255,255,.88) !important;
    }}
    .st-key-supportai_admin_dashboard [data-baseweb="tab"] {{
        position:relative;flex:1 1 0;min-width:0;border-radius:15px !important;
        padding:12px 14px !important;color:{muted} !important;font-weight:850 !important;font-size:.72rem !important;
        transition:all .22s ease !important;
    }}
    .st-key-supportai_admin_dashboard [data-baseweb="tab"]::before {{
        content:"";position:absolute;inset:1px;border-radius:14px;background:linear-gradient(135deg,rgba(99,102,241,.07),transparent 55%);
        opacity:0;transition:opacity .22s ease;pointer-events:none;
    }}
    .st-key-supportai_admin_dashboard [data-baseweb="tab"]:hover {{color:#4f46e5 !important;transform:translateY(-1px);}}
    .st-key-supportai_admin_dashboard [data-baseweb="tab"]:hover::before {{opacity:1}}
    .st-key-supportai_admin_dashboard [aria-selected="true"] {{
        color:#4338ca !important;background:linear-gradient(135deg,rgba(99,102,241,.16),rgba(56,189,248,.10)) !important;
        box-shadow:0 10px 24px rgba(79,70,229,.12),inset 0 1px rgba(255,255,255,.92) !important;
    }}
    .st-key-supportai_admin_dashboard [aria-selected="true"]::after {{
        content:"";position:absolute;left:20%;right:20%;bottom:2px;height:2px;border-radius:999px;
        background:linear-gradient(90deg,#4f46e5,#06b6d4);box-shadow:0 0 12px rgba(79,70,229,.35);
    }}

    /* hero */
    .supportai-admin-hero {{
        position:relative;overflow:hidden;min-height:210px;padding:30px 32px;margin:0 0 20px;border-radius:30px;
        border:1px solid {border};color:{text_color};
        background:
            radial-gradient(circle at 92% 12%,rgba(99,102,241,.28),transparent 25%),
            radial-gradient(circle at 8% 90%,rgba(14,165,233,.13),transparent 29%),
            linear-gradient(135deg,rgba(255,255,255,.84),rgba(238,242,255,.62));
        box-shadow:0 32px 85px rgba(15,23,42,.12),inset 0 1px rgba(255,255,255,.95);
        backdrop-filter:blur(28px) saturate(150%);-webkit-backdrop-filter:blur(28px) saturate(150%);
    }}
    .supportai-admin-hero::before {{
        content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(99,102,241,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(99,102,241,.035) 1px,transparent 1px);
        background-size:36px 36px;mask-image:linear-gradient(to right,transparent,black 20%,black 80%,transparent);animation:sai-admin-grid 18s linear infinite;pointer-events:none;
    }}
    .supportai-admin-hero::after {{
        content:"";position:absolute;width:34%;height:180%;top:-40%;left:-40%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.30),transparent);
        transform:skewX(-18deg);animation:sai-admin-sweep 10s ease-in-out infinite;pointer-events:none;
    }}
    body:has(.ra-theme-marker-dark) .supportai-admin-hero {{
        background:radial-gradient(circle at 92% 12%,rgba(99,102,241,.32),transparent 26%),radial-gradient(circle at 8% 90%,rgba(14,165,233,.14),transparent 30%),linear-gradient(135deg,rgba(10,18,38,.92),rgba(21,27,57,.82));
        border-color:rgba(148,163,184,.20);
    }}
    /* Dark-mode hero text and status pills */
    body:has(.ra-theme-marker-dark) .supportai-admin-hero-kicker {{
        color:#818cf8 !important;
    }}
    body:has(.ra-theme-marker-dark) .supportai-admin-title {{
        color:#f8fafc !important;
    }}
    body:has(.ra-theme-marker-dark) .supportai-admin-copy {{
        color:#b7c4d8 !important;
    }}
    body:has(.ra-theme-marker-dark) .supportai-admin-meta-pill {{
        color:#dbe5f3 !important;
        background:rgba(15,23,42,.58) !important;
        border-color:rgba(148,163,184,.24) !important;
        box-shadow:inset 0 1px rgba(255,255,255,.08),0 8px 20px rgba(0,0,0,.12);
    }}
    body:has(.ra-theme-marker-dark) .supportai-admin-meta-pill:hover {{
        background:rgba(30,41,59,.70) !important;
        border-color:rgba(129,140,248,.32) !important;
    }}
    .supportai-admin-hero-grid {{position:relative;z-index:2;display:grid;grid-template-columns:minmax(0,1fr) 150px;gap:28px;align-items:center}}
    .supportai-admin-hero-kicker {{color:#6366f1;font-size:.62rem;font-weight:950;letter-spacing:.18em;text-transform:uppercase}}
    .supportai-admin-title {{margin-top:6px;font-size:2.30rem;line-height:1.02;font-weight:950;letter-spacing:-.06em}}
    .supportai-admin-copy {{margin-top:10px;max-width:760px;color:{muted};font-size:.82rem;line-height:1.55}}
    .supportai-admin-hero-meta {{display:flex;flex-wrap:wrap;gap:8px;margin-top:17px}}
    .supportai-admin-meta-pill {{display:inline-flex;align-items:center;gap:7px;padding:7px 10px;border-radius:999px;border:1px solid rgba(99,102,241,.14);background:rgba(255,255,255,.44);color:{muted};font-size:.59rem;font-weight:900;backdrop-filter:blur(12px)}}
    .supportai-admin-meta-dot {{width:7px;height:7px;border-radius:50%;background:#22c55e;animation:sai-admin-pulse 2.3s infinite}}
    .supportai-admin-hero-orb {{width:126px;height:126px;border-radius:50%;position:relative;background:radial-gradient(circle at 36% 34%,rgba(255,255,255,.95),rgba(129,140,248,.24) 34%,rgba(79,70,229,.07) 65%,transparent 70%);border:1px solid rgba(255,255,255,.72);box-shadow:0 20px 48px rgba(79,70,229,.20),inset 0 1px rgba(255,255,255,.90);animation:sai-admin-float 6s ease-in-out infinite}}
    .supportai-admin-hero-orb::before {{content:"✦";position:absolute;inset:0;display:grid;place-items:center;color:#4f46e5;font-size:34px;text-shadow:0 0 24px rgba(99,102,241,.35)}}
    .supportai-admin-hero-orb::after {{content:"";position:absolute;inset:18px;border-radius:50%;border:1px solid rgba(99,102,241,.20);box-shadow:0 0 0 18px rgba(99,102,241,.035),0 0 0 36px rgba(14,165,233,.025)}}

    /* KPI cards */
    .supportai-admin-stat {{position:relative;overflow:hidden;min-height:128px;padding:19px;border-radius:21px;border:1px solid {card_border};background:{surface};color:{text_color};box-shadow:0 18px 44px rgba(15,23,42,.075),inset 0 1px rgba(255,255,255,.82);backdrop-filter:blur(22px) saturate(145%);transition:transform .24s ease,box-shadow .24s ease,border-color .24s ease}}
    .supportai-admin-stat::before {{content:"";position:absolute;left:0;right:0;top:0;height:2px;background:linear-gradient(90deg,#4f46e5,#06b6d4,#8b5cf6);opacity:.75}}
    .supportai-admin-stat::after {{content:"";position:absolute;width:120px;height:120px;right:-50px;top:-50px;border-radius:50%;background:radial-gradient(circle,rgba(99,102,241,.16),transparent 68%);pointer-events:none}}
    .supportai-admin-stat:hover {{transform:translateY(-5px);border-color:rgba(99,102,241,.30);box-shadow:0 26px 58px rgba(79,70,229,.14),inset 0 1px rgba(255,255,255,.90)}}
    .supportai-admin-stat-label {{color:{muted};font-size:.58rem;font-weight:900;letter-spacing:.10em;text-transform:uppercase}}
    .supportai-admin-stat-value {{margin-top:9px;font-size:1.88rem;font-weight:950;letter-spacing:-.05em}}
    .supportai-admin-stat-note {{margin-top:4px;color:{muted};font-size:.61rem}}

    /* general cards */
    .supportai-admin-card {{position:relative;overflow:hidden;padding:20px;border-radius:22px;border:1px solid {card_border};background:{surface};box-shadow:0 18px 46px rgba(15,23,42,.075),inset 0 1px rgba(255,255,255,.80);backdrop-filter:blur(22px) saturate(145%)}}
    .supportai-admin-card::before {{content:"";position:absolute;width:210px;height:210px;right:-120px;top:-120px;border-radius:50%;background:radial-gradient(circle,rgba(99,102,241,.09),transparent 68%);pointer-events:none}}
    .supportai-admin-card-title {{font-size:.84rem;font-weight:900;color:{text_color}}}
    .supportai-admin-card-copy {{margin-top:5px;color:{muted};font-size:.66rem;line-height:1.5}}
    .supportai-admin-bar-row {{margin-top:16px}}
    .supportai-admin-bar-head {{display:flex;justify-content:space-between;gap:10px;color:{muted};font-size:.64rem;font-weight:850}}
    .supportai-admin-bar-track {{margin-top:7px;height:9px;border-radius:999px;background:rgba(148,163,184,.12);overflow:hidden}}
    .supportai-admin-bar-fill {{height:100%;border-radius:999px;background:linear-gradient(90deg,#4f46e5,#06b6d4);box-shadow:0 0 14px rgba(79,70,229,.16)}}

    /* admin list items — intentionally no glossy hover */
    .supportai-admin-event {{padding:13px 0;border-bottom:1px solid rgba(148,163,184,.13)}}
    .supportai-admin-event:last-child {{border-bottom:0}}
    .supportai-admin-event-main {{display:flex;justify-content:space-between;gap:12px;align-items:baseline}}
    .supportai-admin-event-user {{color:{text_color};font-size:.72rem;font-weight:900}}
    .supportai-admin-event-type {{color:#6366f1;font-size:.57rem;font-weight:900;letter-spacing:.08em;text-transform:uppercase}}
    .supportai-admin-event-meta {{margin-top:4px;color:{muted};font-size:.61rem}}
    .supportai-admin-user-card {{margin-top:10px;padding:16px;border-radius:18px;border:1px solid {card_border};background:{soft_surface};box-shadow:0 12px 30px rgba(15,23,42,.055),inset 0 1px rgba(255,255,255,.74);transition:transform .20s ease,box-shadow .20s ease,border-color .20s ease}}
    .supportai-admin-user-card:hover {{transform:translateY(-2px);border-color:rgba(99,102,241,.24);box-shadow:0 17px 38px rgba(79,70,229,.09),inset 0 1px rgba(255,255,255,.84)}}
    .supportai-admin-user-name {{color:{text_color};font-size:.84rem;font-weight:900}}
    .supportai-admin-user-email {{margin-top:3px;color:{muted};font-size:.65rem}}
    .supportai-admin-pill {{display:inline-flex;align-items:center;padding:4px 8px;border-radius:999px;font-size:.55rem;font-weight:900;margin-right:5px}}
    .supportai-admin-empty {{padding:18px 16px;border-radius:17px;background:rgba(99,102,241,.055);border:1px dashed rgba(99,102,241,.16);color:{muted};font-size:.68rem}}
    .supportai-admin-recovery {{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:16px 18px;border-radius:18px;border:1px solid rgba(245,158,11,.18);background:linear-gradient(135deg,rgba(245,158,11,.08),rgba(99,102,241,.055));box-shadow:0 14px 32px rgba(245,158,11,.05)}}
    .supportai-admin-recovery-badge {{display:inline-flex;align-items:center;gap:7px;padding:7px 10px;border-radius:999px;background:rgba(245,158,11,.10);color:#b45309;font-size:.57rem;font-weight:900}}
    .supportai-admin-mini-card {{position:relative;overflow:hidden;padding:16px;border-radius:18px;border:1px solid {card_border};background:{soft_surface};box-shadow:inset 0 1px rgba(255,255,255,.76);transition:transform .20s ease,box-shadow .20s ease}}
    .supportai-admin-mini-card::after {{content:"";position:absolute;width:90px;height:90px;right:-35px;bottom:-35px;border-radius:50%;background:radial-gradient(circle,rgba(6,182,212,.09),transparent 68%)}}
    .supportai-admin-mini-card:hover {{transform:translateY(-3px);box-shadow:0 16px 34px rgba(79,70,229,.09),inset 0 1px rgba(255,255,255,.86)}}
    .supportai-admin-mini-label {{color:{muted};font-size:.57rem;font-weight:900;text-transform:uppercase;letter-spacing:.08em}}
    .supportai-admin-mini-value {{margin-top:7px;color:{text_color};font-size:1.08rem;font-weight:950}}
    .supportai-admin-mini-note {{margin-top:3px;color:{muted};font-size:.60rem;line-height:1.4}}

    /* small command tiles */
    .supportai-admin-command-grid {{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:0 0 20px}}
    .supportai-admin-command {{position:relative;overflow:hidden;min-height:86px;padding:15px 16px;border:1px solid {card_border};border-radius:18px;background:{soft_surface};box-shadow:0 12px 28px rgba(15,23,42,.055),inset 0 1px rgba(255,255,255,.78);transition:transform .20s ease,border-color .20s ease,box-shadow .20s ease}}
    .supportai-admin-command:hover {{transform:translateY(-3px);border-color:rgba(99,102,241,.28);box-shadow:0 20px 38px rgba(79,70,229,.11),inset 0 1px rgba(255,255,255,.88)}}
    .supportai-admin-command-icon {{display:inline-grid;place-items:center;width:31px;height:31px;border-radius:10px;background:linear-gradient(135deg,rgba(99,102,241,.16),rgba(6,182,212,.10));color:#4f46e5;font-weight:950}}
    .supportai-admin-command-title {{margin-top:9px;color:{text_color};font-size:.68rem;font-weight:900}}
    .supportai-admin-command-note {{margin-top:2px;color:{muted};font-size:.57rem}}

    @media(max-width:900px) {{
        .st-key-supportai_admin_dashboard {{width:calc(100vw - 24px) !important}}
        .supportai-admin-hero-grid {{grid-template-columns:1fr}}
        .supportai-admin-hero-orb {{display:none}}
        .supportai-admin-command-grid {{grid-template-columns:1fr}}
        .st-key-supportai_admin_dashboard [data-baseweb="tab"] {{padding:10px 8px !important;font-size:.67rem !important}}
    }}
    @media(prefers-reduced-motion:reduce) {{ *,*::before,*::after {{animation:none !important;transition:none !important}} }}
    </style>
    """,
        unsafe_allow_html=True,
    )


def _admin_glass_card(title: str, value: str, note: str = "") -> None:
    """Render an interactive admin KPI card."""
    st.html(
        textwrap.dedent(
            f"""
            <div class="supportai-admin-stat">
                <div class="supportai-admin-stat-label">{html.escape(title)}</div>
                <div class="supportai-admin-stat-value">{html.escape(value)}</div>
                <div class="supportai-admin-stat-note">{html.escape(note)}</div>
            </div>
            """
        )
    )


def _admin_bar_card(title: str, copy: str, values: list[tuple[str, int]]) -> None:
    """Render a glass card with clean visual bars."""
    max_value = max([v for _, v in values], default=0)
    rows = []
    for label, value in values:
        width = 0 if max_value <= 0 else max(4, int((value / max_value) * 100))
        rows.append(
            f"""
            <div class="supportai-admin-bar-row">
                <div class="supportai-admin-bar-head"><span>{html.escape(label)}</span><span>{value}</span></div>
                <div class="supportai-admin-bar-track"><div class="supportai-admin-bar-fill" style="width:{width}%"></div></div>
            </div>
            """
        )
    st.html(
        textwrap.dedent(
            f"""
            <div class="supportai-admin-card">
                <div class="supportai-admin-card-title">{html.escape(title)}</div>
                <div class="supportai-admin-card-copy">{html.escape(copy)}</div>
                {"".join(rows)}
            </div>
            """
        )
    )


def render_admin_dashboard(
    controller: RetailAssistController,
    theme: str = "light",
) -> None:
    """Render the administrator workspace as an interactive glass control center."""
    authenticated_user = st.session_state.get("supportai_user") or {}
    role = str(authenticated_user.get("role") or "").strip().upper()
    if role != "ADMIN":
        st.error("Administrator access required.")
        return

    inject_admin_dashboard_styles(theme)
    render_authenticated_header(controller, theme)

    with st.container(key="supportai_admin_dashboard", border=False):
        display_name = str(authenticated_user.get("name") or "Administrator")
        st.html(
            textwrap.dedent(
                f"""
                <div class="supportai-admin-hero">
                    <div class="supportai-admin-hero-grid">
                        <div>
                            <div class="supportai-admin-hero-kicker">Administration</div>
                            <div class="supportai-admin-title">Command the workspace.</div>
                            <div class="supportai-admin-copy">
                                Welcome, {html.escape(display_name)}. Monitor access, govern the knowledge base,
                                manage recovery, and understand workspace activity from one protected control center.
                            </div>
                            <div class="supportai-admin-hero-meta">
                                <span class="supportai-admin-meta-pill"><span class="supportai-admin-meta-dot"></span>System online</span>
                                <span class="supportai-admin-meta-pill">Snowflake governed</span>
                                <span class="supportai-admin-meta-pill">Administrator access</span>
                            </div>
                        </div>
                        <div class="supportai-admin-hero-orb" aria-hidden="true"></div>
                    </div>
                </div>
                """
            )
        )

        st.html(
            textwrap.dedent("""
            <div class="supportai-admin-command-grid">
                <div class="supportai-admin-command">
                    <div class="supportai-admin-command-icon">⌁</div>
                    <div class="supportai-admin-command-title">Access control</div>
                    <div class="supportai-admin-command-note">Accounts & recovery</div>
                </div>
                <div class="supportai-admin-command">
                    <div class="supportai-admin-command-icon">▦</div>
                    <div class="supportai-admin-command-title">Knowledge governance</div>
                    <div class="supportai-admin-command-note">Documents & indexing</div>
                </div>
                <div class="supportai-admin-command">
                    <div class="supportai-admin-command-icon">◫</div>
                    <div class="supportai-admin-command-title">Workspace telemetry</div>
                    <div class="supportai-admin-command-note">Usage & activity</div>
                </div>
            </div>
        """)
        )

        admin_service = AdminService(session=getattr(controller, "session", None))
        try:
            stats = admin_service.get_user_stats()
            users = admin_service.list_users()
        except Exception:
            admin_service.close()
            logger.exception("Failed to load admin dashboard data.")
            st.error("Unable to load the administration dashboard.")
            return

        overview_tab, accounts_tab, knowledge_tab, activity_tab = st.tabs(
            ["Overview", "Accounts", "Knowledge Base", "Usage & Activity"]
        )

        with overview_tab:
            st.markdown(
                '<div class="ra-section-label">Workspace snapshot</div><div class="ra-section-title">At a glance</div>',
                unsafe_allow_html=True,
            )
            metric_cols = st.columns(4, gap="medium")
            with metric_cols[0]:
                _admin_glass_card(
                    "Total accounts",
                    str(stats["total_users"]),
                    "All registered accounts",
                )
            with metric_cols[1]:
                _admin_glass_card(
                    "Active accounts", str(stats["active_users"]), "Currently enabled"
                )
            with metric_cols[2]:
                _admin_glass_card(
                    "User accounts",
                    str(stats["total_user_accounts"]),
                    "Standard workspace users",
                )
            with metric_cols[3]:
                _admin_glass_card(
                    "Administrators",
                    str(stats["total_admins"]),
                    "Protected admin accounts",
                )

            st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
            overview_left, overview_right = st.columns([1.05, 0.95], gap="medium")
            with overview_left:
                active_users = sum(
                    1
                    for user in users
                    if str(user.get("status") or "").upper() == "ACTIVE"
                )
                inactive_users = max(0, len(users) - active_users)
                _admin_bar_card(
                    "Account status",
                    "A live view of enabled versus disabled workspace accounts.",
                    [("Active", active_users), ("Inactive", inactive_users)],
                )
            with overview_right:
                admin_count = sum(
                    1
                    for user in users
                    if str(user.get("role") or "").upper() == "ADMIN"
                )
                user_count = max(0, len(users) - admin_count)
                _admin_bar_card(
                    "Role distribution",
                    "Current account mix across the SupportAI workspace.",
                    [("Users", user_count), ("Administrators", admin_count)],
                )

            st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
            st.markdown(
                '<div class="ra-section-label">Command summary</div><div class="ra-section-title">Control center health</div>',
                unsafe_allow_html=True,
            )
            health_cols = st.columns(3, gap="medium")
            with health_cols[0]:
                st.html(
                    textwrap.dedent("""
                    <div class="supportai-admin-mini-card">
                        <div class="supportai-admin-mini-label">Access posture</div>
                        <div class="supportai-admin-mini-value">Protected</div>
                        <div class="supportai-admin-mini-note">Role-aware administrator controls are active.</div>
                    </div>
                """)
                )
            with health_cols[1]:
                st.html(
                    textwrap.dedent("""
                    <div class="supportai-admin-mini-card">
                        <div class="supportai-admin-mini-label">Knowledge control</div>
                        <div class="supportai-admin-mini-value">Managed</div>
                        <div class="supportai-admin-mini-note">Documents are governed from the admin workspace.</div>
                    </div>
                """)
                )
            with health_cols[2]:
                st.html(
                    textwrap.dedent("""
                    <div class="supportai-admin-mini-card">
                        <div class="supportai-admin-mini-label">Activity visibility</div>
                        <div class="supportai-admin-mini-value">Traceable</div>
                        <div class="supportai-admin-mini-note">Workspace usage is available in the activity center.</div>
                    </div>
                """)
                )

            st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
            reset_service = _auth_service(controller)
            try:
                reset_requests = reset_service.list_pending_password_resets()
            except Exception:
                logger.exception("Failed to load password reset requests.")
                reset_requests = []
            finally:
                _safe_auth_close(reset_service)
            if reset_requests:
                st.html(
                    textwrap.dedent(f"""
                    <div class="supportai-admin-recovery">
                        <div>
                            <div class="supportai-admin-card-title">{len(reset_requests)} password reset(s) awaiting verification</div>
                            <div class="supportai-admin-card-copy">Users have completed their emailed password reset. Review each request and approve or reject it.</div>
                        </div>
                        <span class="supportai-admin-recovery-badge">● Action required</span>
                    </div>
                """)
                )
            else:
                st.html(
                    '<div class="supportai-admin-empty">No completed password-reset requests are awaiting verification. Workspace recovery queue is clear.</div>'
                )

        with accounts_tab:
            st.markdown(
                '<div class="ra-section-label">Access management</div><div class="ra-section-title">Accounts & recovery</div>',
                unsafe_allow_html=True,
            )

            reset_service = _auth_service(controller)
            try:
                reset_requests = reset_service.list_pending_password_resets()
            except Exception:
                logger.exception("Failed to load password reset requests.")
                reset_requests = []
                st.error("Unable to load password-reset requests.")
            finally:
                _safe_auth_close(reset_service)

            if reset_requests:
                authenticated_admin = st.session_state.get("supportai_user") or {}
                admin_user_id = str(authenticated_admin.get("user_id") or "").strip()

                for reset_request in reset_requests:
                    reset_id = str(reset_request.get("reset_id") or "")
                    reset_name = str(reset_request.get("name") or "User")
                    reset_email = str(reset_request.get("email") or "")
                    requested_at = str(reset_request.get("requested_at") or "—")
                    completed_at = str(reset_request.get("completed_at") or "—")
                    user_status = str(
                        reset_request.get("user_status") or "ACTIVE"
                    ).upper()

                    st.html(
                        textwrap.dedent(f"""
                        <div class="supportai-admin-user-card">
                            <div class="supportai-admin-user-name">{html.escape(reset_name)}</div>
                            <div class="supportai-admin-user-email">{html.escape(reset_email)}</div>
                            <div style="margin-top:7px; color:#94a3b8; font-size:.60rem;">
                                Requested {html.escape(requested_at)} • Password changed {html.escape(completed_at)} • Account {html.escape(user_status)}
                            </div>
                        </div>
                    """)
                    )

                    approve_col, reject_col = st.columns(2, gap="small")
                    with approve_col:
                        approve_clicked = st.button(
                            "Approve Reset",
                            key=f"supportai_approve_reset_{reset_id}",
                            type="primary",
                            use_container_width=True,
                        )
                    with reject_col:
                        reject_clicked = st.button(
                            "Reject Reset",
                            key=f"supportai_reject_reset_{reset_id}",
                            use_container_width=True,
                        )

                    if approve_clicked or reject_clicked:
                        if not admin_user_id:
                            st.error("Unable to verify the administrator session.")
                            continue

                        reset_service = _auth_service(controller)
                        try:
                            if approve_clicked:
                                reset_service.approve_password_reset(
                                    reset_id=reset_id,
                                    admin_user_id=admin_user_id,
                                )
                                st.success(
                                    f"Password reset approved for {reset_email}. The user can now sign in with the new password."
                                )
                            else:
                                reset_service.reject_password_reset(
                                    reset_id=reset_id,
                                    admin_user_id=admin_user_id,
                                )
                                st.warning(
                                    f"Password reset rejected for {reset_email}. The user cannot sign in with that reset password."
                                )
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
                        except Exception:
                            logger.exception(
                                "Failed to verify password reset | reset_id=%s | action=%s",
                                reset_id,
                                "APPROVE" if approve_clicked else "REJECT",
                            )
                            st.error("Unable to verify this password reset right now.")
                        finally:
                            _safe_auth_close(reset_service)
            else:
                st.html(
                    '<div class="supportai-admin-empty">No completed password-reset requests are awaiting verification.</div>'
                )

            st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="ra-section-label">Workspace accounts</div><div class="ra-section-title">Users</div>',
                unsafe_allow_html=True,
            )
            filter_col, count_col = st.columns([3.5, 1], gap="medium")
            with filter_col:
                account_filter = (
                    st.text_input(
                        "Filter accounts",
                        placeholder="Search by name or email",
                        key="supportai_admin_account_filter",
                        label_visibility="collapsed",
                    )
                    .strip()
                    .lower()
                )
            with count_col:
                st.html(
                    textwrap.dedent(f"""
                    <div class="supportai-admin-mini-card" style="min-height:unset; padding:11px 14px;">
                        <div class="supportai-admin-mini-label">Visible accounts</div>
                        <div class="supportai-admin-mini-value" style="font-size:1rem;">{len(users)}</div>
                    </div>
                """)
                )

            filtered_users = [
                user
                for user in users
                if not account_filter
                or account_filter in str(user.get("name") or "").lower()
                or account_filter in str(user.get("email") or "").lower()
            ]
            if not filtered_users:
                st.html(
                    '<div class="supportai-admin-empty">No accounts match the current filter.</div>'
                )
            for user in filtered_users:
                user_id = str(user.get("user_id") or "")
                name = str(user.get("name") or "Unknown User")
                email = str(user.get("email") or "")
                user_role = str(user.get("role") or "USER").upper()
                status = str(user.get("status") or "INACTIVE").upper()
                created_display = str(user.get("created_at") or "—")
                login_display = str(user.get("last_login") or "Never")
                left, middle, right = st.columns(
                    [4.0, 3.0, 1.35], gap="small", vertical_alignment="center"
                )
                with left:
                    st.html(
                        textwrap.dedent(f"""
                        <div class="supportai-admin-user-card" style="margin-top:0;">
                            <div class="supportai-admin-user-name">{html.escape(name)}</div>
                            <div class="supportai-admin-user-email">{html.escape(email)}</div>
                            <div style="margin-top:6px; color:#94a3b8; font-size:.58rem;">Created {html.escape(created_display)}</div>
                        </div>
                    """)
                    )
                with middle:
                    role_bg = (
                        "rgba(99,102,241,.11)"
                        if user_role == "ADMIN"
                        else "rgba(59,130,246,.10)"
                    )
                    role_color = "#6366f1" if user_role == "ADMIN" else "#2563eb"
                    status_bg = (
                        "rgba(16,185,129,.10)"
                        if status == "ACTIVE"
                        else "rgba(239,68,68,.10)"
                    )
                    status_color = "#059669" if status == "ACTIVE" else "#dc2626"
                    st.html(
                        textwrap.dedent(f"""
                        <div class="supportai-admin-user-card" style="margin-top:0;">
                            <span class="supportai-admin-pill" style="background:{role_bg}; color:{role_color};">{"Administrator" if user_role == "ADMIN" else "User"}</span>
                            <span class="supportai-admin-pill" style="background:{status_bg}; color:{status_color};">{"Active" if status == "ACTIVE" else "Inactive"}</span>
                            <div style="margin-top:8px; color:#94a3b8; font-size:.58rem;">Last login {html.escape(login_display)}</div>
                        </div>
                    """)
                    )
                with right:
                    if user_role == "ADMIN":
                        st.caption("Protected")
                    elif status == "ACTIVE":
                        if st.button(
                            "Deactivate",
                            key=f"admin_deactivate_{user_id}",
                            use_container_width=True,
                        ):
                            try:
                                changed = admin_service.deactivate_user(user_id)
                                if changed:
                                    st.success("User deactivated.")
                                    st.rerun()
                                else:
                                    st.warning("No account was changed.")
                            except Exception:
                                logger.exception(
                                    "Failed to deactivate user | user_id=%s", user_id
                                )
                                st.error("Unable to update this account.")
                    else:
                        if st.button(
                            "Activate",
                            key=f"admin_activate_{user_id}",
                            use_container_width=True,
                        ):
                            try:
                                changed = admin_service.activate_user(user_id)
                                if changed:
                                    st.success("User activated.")
                                    st.rerun()
                                else:
                                    st.warning("No account was changed.")
                            except Exception:
                                logger.exception(
                                    "Failed to activate user | user_id=%s", user_id
                                )
                                st.error("Unable to update this account.")

        with knowledge_tab:
            st.markdown(
                '<div class="ra-section-label">Knowledge base</div><div class="ra-section-title">Administrator workspace</div>',
                unsafe_allow_html=True,
            )
            st.html(
                textwrap.dedent("""
                <div class="supportai-admin-card" style="margin-bottom:14px;">
                    <div class="supportai-admin-card-title">Knowledge control</div>
                    <div class="supportai-admin-card-copy">Upload, review, retry, re-index, deactivate, and refresh support documents from one controlled workspace.</div>
                </div>
            """)
            )
            render_document_management(controller)

        with activity_tab:
            st.markdown(
                '<div class="ra-section-label">Workspace activity</div><div class="ra-section-title">Usage at a glance</div>',
                unsafe_allow_html=True,
            )
            activity_service = ActivityService(
                session=getattr(controller, "session", None)
            )
            try:
                activity_summary = activity_service.get_usage_summary()
                recent_activity = activity_service.list_recent_activity(limit=40)
                user_usage = activity_service.list_user_usage(limit=25)

                metric_cols = st.columns(4, gap="medium")
                with metric_cols[0]:
                    _admin_glass_card(
                        "Active users (7d)",
                        str(activity_summary["active_users"]),
                        "Distinct active accounts",
                    )
                with metric_cols[1]:
                    _admin_glass_card(
                        "Queries (7d)",
                        str(activity_summary["total_queries"]),
                        "All assistant queries",
                    )
                with metric_cols[2]:
                    _admin_glass_card(
                        "Logins (7d)",
                        str(activity_summary["total_logins"]),
                        "Successful sign-ins",
                    )
                with metric_cols[3]:
                    _admin_glass_card(
                        "Agent queries (7d)",
                        str(activity_summary["agent_queries"]),
                        "Cortex Agent usage",
                    )

                st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
                chart_left, chart_right = st.columns(2, gap="medium")
                with chart_left:
                    _admin_bar_card(
                        "Query mix",
                        "Distribution across the three user-facing assistant modes.",
                        [
                            ("Policy RAG", activity_summary["policy_queries"]),
                            (
                                "Support Analytics",
                                activity_summary["analytics_queries"],
                            ),
                            ("Cortex Agent", activity_summary["agent_queries"]),
                        ],
                    )
                with chart_right:
                    recent_counts = {"LOGIN": 0, "QUERY": 0, "LOGOUT": 0}
                    for event in recent_activity:
                        event_type = str(event.get("activity_type") or "").upper()
                        if event_type in recent_counts:
                            recent_counts[event_type] += 1
                    _admin_bar_card(
                        "Recent event mix",
                        "Event types visible in the latest activity window.",
                        [
                            ("Logins", recent_counts["LOGIN"]),
                            ("Queries", recent_counts["QUERY"]),
                            ("Logouts", recent_counts["LOGOUT"]),
                        ],
                    )

                st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
                recent_col, account_col = st.columns([1.08, 0.92], gap="medium")
                with recent_col:
                    st.markdown(
                        '<div class="ra-section-label">Recent activity</div><div class="ra-section-title">Latest workspace events</div>',
                        unsafe_allow_html=True,
                    )
                    if not recent_activity:
                        st.html(
                            '<div class="supportai-admin-empty">No activity has been recorded yet.</div>'
                        )
                    else:
                        event_parts = []
                        for event in recent_activity[:18]:
                            event_name = str(event.get("name") or "Unknown User")
                            event_email = str(event.get("email") or "")
                            event_type = str(event.get("activity_type") or "")
                            event_mode = str(event.get("mode") or "")
                            event_status = str(event.get("status") or "")
                            event_time = str(event.get("created_at") or "—")
                            latency = event.get("latency_seconds")
                            latency_text = (
                                f" • {float(latency):.2f}s"
                                if latency is not None
                                else ""
                            )
                            mode_text = f" • {event_mode}" if event_mode else ""
                            event_parts.append(
                                textwrap.dedent(f"""
                                <div class="supportai-admin-event">
                                    <div class="supportai-admin-event-main">
                                        <div class="supportai-admin-event-user">{html.escape(event_name)} <span style="font-weight:500; color:#94a3b8;">{html.escape(event_email)}</span></div>
                                        <div class="supportai-admin-event-type">{html.escape(event_type)}</div>
                                    </div>
                                    <div class="supportai-admin-event-meta">{html.escape(event_status)}{html.escape(mode_text)}{html.escape(latency_text)} • {html.escape(event_time)}</div>
                                </div>
                            """)
                            )
                        st.html(
                            "<div class='supportai-admin-card'>"
                            + "".join(event_parts)
                            + "</div>"
                        )
                with account_col:
                    st.markdown(
                        '<div class="ra-section-label">User usage</div><div class="ra-section-title">Usage by account</div>',
                        unsafe_allow_html=True,
                    )
                    if not user_usage:
                        st.html(
                            '<div class="supportai-admin-empty">No user usage has been recorded yet.</div>'
                        )
                    else:
                        max_activity = max(
                            [int(row.get("activity_count") or 0) for row in user_usage],
                            default=0,
                        )
                        account_parts = []
                        for usage in user_usage:
                            usage_name = str(usage.get("name") or "Unknown User")
                            usage_email = str(usage.get("email") or "")
                            activity_count = int(usage.get("activity_count") or 0)
                            policy_count = int(usage.get("policy_count") or 0)
                            analytics_count = int(usage.get("analytics_count") or 0)
                            agent_count = int(usage.get("agent_count") or 0)
                            width = (
                                0
                                if max_activity <= 0
                                else max(4, int((activity_count / max_activity) * 100))
                            )
                            account_parts.append(
                                textwrap.dedent(f"""
                                <div class="supportai-admin-user-card">
                                    <div class="supportai-admin-user-name">{html.escape(usage_name)}</div>
                                    <div class="supportai-admin-user-email">{html.escape(usage_email)}</div>
                                    <div style="margin-top:8px; height:7px; background:rgba(148,163,184,.12); border-radius:999px; overflow:hidden;"><div style="height:100%; width:{width}%; border-radius:999px; background:linear-gradient(90deg,#4f46e5,#06b6d4);"></div></div>
                                    <div style="margin-top:7px; color:#64748b; font-size:.60rem;">Total {activity_count} • Policy {policy_count} • Analytics {analytics_count} • Agent {agent_count}</div>
                                </div>
                            """)
                            )
                        st.html("".join(account_parts))
            except Exception:
                logger.exception("Failed to load SupportAI usage/activity.")
                st.error(
                    "Unable to load usage and activity right now. Create the SUPPORT_ACTIVITY_LOG table and try again."
                )
            finally:
                activity_service.close()

        st.markdown(
            '<div class="ra-footer"><strong>SupportAI</strong> • Administrator control center<span class="ra-footer-credit">Governed • Traceable • Snowflake Native</span></div>',
            unsafe_allow_html=True,
        )

    admin_service.close()


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

    # ---------------------------------------------------------
    # Authentication gate
    # ---------------------------------------------------------
    initialize_auth_state()

    if not render_auth_screen(controller, theme):
        return

    # Theme toggle is part of the authenticated workspace only. Keeping it out
    # of the login screen prevents it from contributing extra viewport height.
    with st.container(key="supportai_theme_toggle"):
        next_icon = "☀" if theme == "dark" else "◐"
        toggle_clicked = st.button(
            next_icon,
            key="supportai_theme_button",
            help=(
                "Switch to the light workspace theme."
                if theme == "dark"
                else "Switch to the dark workspace theme."
            ),
            type="secondary",
        )

    if toggle_clicked:
        st.session_state["supportai_theme"] = "light" if theme == "dark" else "dark"
        st.rerun()

    # ---------------------------------------------------------
    # Role-based workspace routing
    # ---------------------------------------------------------

    authenticated_user = st.session_state.get("supportai_user") or {}
    authenticated_role = str(authenticated_user.get("role") or "USER").strip().upper()

    # ADMIN → Admin Dashboard
    if authenticated_role == "ADMIN":
        render_admin_dashboard(controller, theme)
        return

    # USER → Existing SupportAI workspace
    render_authenticated_header(controller, theme)

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
        <div class="ra-brand-icon"><span>✦</span></div>
        <div class="ra-brand-copy">
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
        <div class="ra-product-tile ra-product-tile-knowledge">
            <div class="ra-product-icon">⌁</div>
            <div class="ra-product-copy">
                <div class="ra-product-kicker">Knowledge</div>
                <div class="ra-product-value">Policy-grounded responses</div>
                <div class="ra-product-desc">Trusted answers backed by retrieved evidence.</div>
            </div>
            <span class="ra-product-arrow">↗</span>
        </div>
        <div class="ra-product-tile ra-product-tile-operations">
            <div class="ra-product-icon">◫</div>
            <div class="ra-product-copy">
                <div class="ra-product-kicker">Operations</div>
                <div class="ra-product-value">Live support-ticket insights</div>
                <div class="ra-product-desc">Natural-language analytics over support data.</div>
            </div>
            <span class="ra-product-arrow">↗</span>
        </div>
        <div class="ra-product-tile ra-product-tile-automation">
            <div class="ra-product-icon">✧</div>
            <div class="ra-product-copy">
                <div class="ra-product-kicker">Automation</div>
                <div class="ra-product-value">Cortex Agent orchestration</div>
                <div class="ra-product-desc">Intelligent routing across trusted tools.</div>
            </div>
            <span class="ra-product-arrow">↗</span>
        </div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # =========================================================
    # MAIN WORKSPACE
    # =========================================================

    _render_support_ai_fragment(controller)

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
