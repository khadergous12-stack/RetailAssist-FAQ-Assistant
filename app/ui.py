import re
import html
import textwrap
import hashlib

import streamlit as st

from app.controller import RetailAssistController
from rag.service import RAGResponse


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


def inject_box_styles() -> None:
    """
    Inject professional, industry-style CSS for the SupportAI interface.
    This function changes presentation only; application logic is unchanged.
    """

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
                max-width: 1180px;
                padding-top: 4.6rem;
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

            .ra-source-text {
                color: #64748b;
                line-height: 1.7;
                font-size: 0.92rem;
            }

            /* =========================================================
               Footer
               ========================================================= */

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

            </style>
            """
        ),
        unsafe_allow_html=True,
    )


def render_response(
    response: RAGResponse,
    question: str,
    provider: str = "",
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

    answer_html = (
        '<div class="ra-answer-box">'
        '<div class="ra-answer-label">POLICY-GROUNDED RESPONSE</div>'
        f'<div class="ra-answer-text">{html.escape(answer)}</div>'
        f'<div class="ra-source-meta" style="margin-top:14px;">'
        f"Generated by: <strong>{html.escape(provider_label)}</strong>"
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

    for source_number, chunk in enumerate(response.evidence, start=1):
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


def render_document_management(controller: RetailAssistController) -> None:
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

        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True,
        )

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
                use_container_width=True,
                disabled=status == "INDEXED" and active,
                key=f"retry_{document_id}",
            )

        with action2:
            reindex_clicked = st.button(
                "⟳ Re-index",
                use_container_width=True,
                key=f"reindex_{document_id}",
            )

        with action3:
            delete_clicked = st.button(
                "🗑️ Delete",
                use_container_width=True,
                disabled=not active,
                key=f"delete_{document_id}",
            )

        with action4:
            refresh_clicked = st.button(
                "🔄 Refresh",
                use_container_width=True,
                key=f"refresh_{document_id}",
            )

        if retry_clicked:
            try:
                chunk_count = store.retry_document(document_id)
                st.success(
                    f"Retry completed for {selected['ORIGINAL_FILENAME']} "
                    f"({chunk_count} chunks)."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Retry failed: {exc}")

        if reindex_clicked:
            try:
                chunk_count = store.reindex_document(document_id)
                st.success(
                    f"Re-indexed {selected['ORIGINAL_FILENAME']} "
                    f"({chunk_count} chunks)."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Re-index failed: {exc}")

        if delete_clicked:
            try:
                store.delete_document(document_id)
                st.success(
                    f"{selected['ORIGINAL_FILENAME']} was deactivated. "
                    "It will no longer be eligible for retrieval."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Delete failed: {exc}")

        if refresh_clicked:
            try:
                refresh_method = getattr(store, "refresh_search", None)
                if refresh_method is None:
                    st.info(
                        "Document metadata refreshed. Cortex Search will refresh "
                        "according to its configured target lag."
                    )
                else:
                    refresh_method()
                    st.success("Cortex Search refresh requested.")
            except Exception as exc:
                st.warning(f"Search refresh could not be requested: {exc}")


def run_app(
    controller: RetailAssistController,
) -> None:
    """Render the SupportAI Streamlit application."""

    st.set_page_config(
        page_title="SupportAI | Customer Support Assistant",
        page_icon="✦",
        layout="centered",
        initial_sidebar_state="collapsed",
    )

    inject_box_styles()

    # ---------------------------------------------------------
    # Product hero
    # ---------------------------------------------------------

    st.markdown(
        """
    <div class="ra-hero">
        <div class="ra-brand-row">
            <div class="ra-brand-icon">✦</div>
            <div>
                <div class="ra-brand-title">SupportAI</div>
                <div class="ra-brand-subtitle">
                    AI-powered customer support grounded in trusted policy knowledge.
                    Ask a question and receive a concise answer backed by approved evidence.
                </div>
            </div>
        </div>

    <div class="ra-status-strip">
        <span class="ra-status">
            <span class="ra-status-dot"></span>
                Policy-grounded
            </span>
        <span class="ra-status">Snowflake Cortex</span>
        <span class="ra-status">FAQ Knowledge Base</span>
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
        # -----------------------------------------------------
        # AI provider selection
        # -----------------------------------------------------

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
                "OpenRouter (OpenAI)",
            ],
            key="ai_provider",
        )

        provider_name = {
            "Snowflake Cortex": "snowflake",
            "OpenRouter (OpenAI)": "openai",
        }[provider]

        try:
            controller.set_provider(provider_name)
        except Exception as exc:
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
                    st.error(error)

                if valid_files:
                    st.markdown("**Selected documents**")

                    for uploaded_file in valid_files:
                        file_bytes = uploaded_file.getvalue()

                        file_size_mb = len(file_bytes) / (1024 * 1024)

                        detected_category = "Detecting..."

                        if controller.document_store is not None:
                            try:
                                detected_category = (
                                    controller.document_store.detect_category_from_file(
                                        file_bytes,
                                        uploaded_file.name,
                                    )
                                )
                            except Exception:
                                detected_category = "Unable to detect"

                        st.write(
                            f"📄 **{uploaded_file.name}** "
                            f"• {file_size_mb:.2f} MB "
                            f"• **Category: {detected_category}**"
                        )

                        st.caption("Document ID will be assigned during upload.")

                    if st.button(
                        "⬆️ Upload to Snowflake",
                        type="primary",
                        use_container_width=True,
                        key="main_upload_to_snowflake",
                    ):
                        if controller.document_store is None:
                            st.error("Document upload requires Snowflake mode.")

                        else:
                            successful_uploads = 0

                            for uploaded_file in valid_files:
                                file_bytes = uploaded_file.getvalue()

                                try:
                                    staged_path = (
                                        controller.document_store.upload_to_stage(
                                            file_bytes=file_bytes,
                                            filename=uploaded_file.name,
                                            document_id=None,
                                            category=None,
                                        )
                                    )

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

                                    st.success(
                                        f"{uploaded_file.name} uploaded successfully."
                                    )

                                    st.caption(f"Document ID: {document_id}")

                                    st.caption(f"Stage: {staged_path}")

                                    successful_uploads += 1

                                except Exception as exc:
                                    st.error(
                                        f"Failed to upload {uploaded_file.name}: {exc}"
                                    )

                            if successful_uploads:
                                st.info(
                                    f"{successful_uploads} document(s) "
                                    "uploaded to Snowflake."
                                )

        # -----------------------------------------------------
        # FAQ / customer support
        # -----------------------------------------------------

        st.markdown(
            """
            <div class="ra-section-label">Customer support</div>
            <div class="ra-section-title">Ask your policy question</div>
            <div class="ra-helper">
                Ask about any policy available in the knowledge base.
            </div>
            """,
            unsafe_allow_html=True,
        )

        question = st.text_area(
            "Question",
            placeholder=("Example: Does the warranty cover accidental damage?"),
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
                use_container_width=True,
                key="ask_supportai",
            )

        with col2:
            clear_clicked = st.button(
                "↺  Clear",
                use_container_width=True,
                key="clear_supportai",
            )

        if clear_clicked:
            st.rerun()

        if ask_clicked:
            if not question.strip():
                st.warning("Please enter a question.")
                return

            try:
                with st.spinner(
                    f"Retrieving evidence and generating with {provider}..."
                ):
                    response = controller.ask(question.strip())

                render_response(
                    response,
                    question,
                    provider,
                )

            except ValueError as exc:
                st.error(str(exc))

            except RuntimeError as exc:
                st.error(str(exc))

            except Exception:
                st.error(
                    f"{provider} could not generate a response. "
                    "Please check the provider configuration and try again."
                )

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
            SupportAI • Grounded answers from approved policy evidence
        </div>
        """,
        unsafe_allow_html=True,
    )
