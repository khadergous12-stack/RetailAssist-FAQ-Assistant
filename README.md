# SupportAI

SupportAI is a policy-grounded customer-support assistant that answers retail FAQ questions using approved policy documents, Retrieval-Augmented Generation (RAG), and Snowflake Cortex Search.

The extended version adds **multi-provider generation** and **dynamic document management**. Users can upload PDF, DOCX, TXT, and Markdown documents through the Streamlit UI, store and process them in Snowflake, retrieve relevant evidence through the same Cortex Search service, and choose at runtime between **Snowflake Cortex** and **OpenRouter (Grok 4.3)** for answer generation.

---

## Key Features

- Policy-grounded customer-support answers
- Retrieval-Augmented Generation (RAG)
- Snowflake Cortex Search as the retrieval layer
- Snowflake Cortex generation support
- OpenRouter generation using the OpenAI-compatible API with **Grok 4.3**
- Runtime AI-provider selection from the Streamlit UI
- Dynamic document upload from the Streamlit UI
- PDF, DOCX, TXT, and Markdown document support
- Automatic document category detection
- Document metadata and chunk tracking
- Duplicate document handling
- Retry, re-index, delete, and refresh document-management actions
- Relevant evidence/source display in the UI
- Uploaded-document page/section metadata where available
- Provider-neutral RAG architecture
- Centralized configuration through `config/settings.py`
- Graceful provider error handling
- Local/demo mode for development and testing
- Automated tests and 20-question provider evaluation
- Document-upload progress from validation through indexing
- Inline processing spinner for questions and document-management actions
- Streamlit fragment-based UI updates to reduce unnecessary full-page reruns
- One-question retrieval preference for a newly uploaded document, followed by normal all-active-document retrieval
- Retrieval and generation latency shown with each answer
- Structured logging for uploads, parsing, chunking, indexing, retrieval, generation, and document-management actions

---

# Architecture

The application keeps **retrieval independent from generation**.

```text
                         ┌────────────────────────┐
                         │      Streamlit UI      │
                         │ FAQ + Document Upload  │
                         │   + AI Provider UI     │
                         └────────────┬───────────┘
                                      │
                                      ▼
                         ┌────────────────────────┐
                         │       Controller       │
                         │ RetailAssistController │
                         └────────────┬───────────┘
                                      │
                                      ▼
                         ┌────────────────────────┐
                         │       RAGService       │
                         │ Provider-neutral RAG   │
                         └────────────┬───────────┘
                                      │
                         ┌────────────┴────────────┐
                         │                         │
                         ▼                         ▼
                  ┌─────────────────┐      ┌─────────────────┐
                  │    Retriever    │      │    Generator    │
                  │     Contract    │      │     Contract    │
                  └────────┬────────┘      └────────┬────────┘
                           │                        │
                           ▼                        ▼
                  ┌─────────────────┐      ┌─────────────────┐
                  │ Snowflake       │      │ Provider Factory│
                  │ Cortex Search   │      └────────┬────────┘
                  └────────┬────────┘               │
                           │               ┌─────────┴─────────┐
                           │               │                   │
                           ▼               ▼                   ▼
                  ┌─────────────────┐  Snowflake          OpenRouter
                  │ Final Evidence  │  Cortex             / Grok 4.3
                  └────────┬────────┘  Generator           Generator
                           │
                           └──────────────┬────────────────────┘
                                          ▼
                                Grounded Prompt / Answer
```

### Provider-selection flow

```text
User Question
     ↓
Snowflake Cortex Search
     ↓
RAG Evidence Filtering
     ↓
Same Retrieved Context
     ├───────────────┐
     ▼               ▼
Snowflake Cortex  OpenRouter (Grok 4.3)
     │               │
     └───────┬───────┘
             ▼
        Final Answer
```

**Important:** OpenRouter is a **generation provider only**. It does not replace Snowflake Cortex Search retrieval.

---

# Supported Generation Providers

## 1. Snowflake Cortex

The default provider uses the Snowflake-backed generation implementation.

## 2. OpenRouter (Grok 4.3)

OpenRouter is used through its OpenAI-compatible API.

The provider adapter lives under:

```text
providers/openai/generator.py
```

The UI presents this provider as **OpenRouter (Grok 4.3)**. The internal provider identifier remains the existing OpenAI-compatible adapter name so the provider factory and RAG contracts remain unchanged.

---

# Runtime Provider Selection

The main Streamlit interface contains an **AI Provider** dropdown near the top of the page.

Available options:

```text
Snowflake Cortex
OpenRouter (Grok 4.3)
```

Changing the selection affects the **next generation request** without restarting the application.

The active provider and request latency are shown with the answer:

```text
Generated by: OpenRouter (Grok 4.3)
Latency: 1.38s
```

The retrieval path remains the same for both providers.

---

# Dynamic Document Management

The application supports knowledge-base management through a dedicated **Document Management** tab.

The main SupportAI tab contains:

- FAQ interaction
- Document upload
- AI provider selection
- Upload processing progress

The Document Management tab contains:

- Uploaded-document listing
- Category/status filtering
- Metadata inspection
- Retry
- Re-index
- Delete
- Refresh

Administrative actions display an inline processing spinner while the backend operation runs, and each action is logged with document ID and elapsed time.

---

# Supported Document Formats

| Format | Extension | Supported |
|---|---|---|
| Markdown | `.md` | Yes |
| PDF | `.pdf` | Yes |
| Microsoft Word | `.docx` | Yes |
| Plain text | `.txt` | Yes |

Uploaded documents are stored and processed through the Snowflake document pipeline and become available as retrieval evidence after successful indexing.

---

# Document Upload Flow

```text
User selects file
        ↓
Validate extension / size / empty file
        ↓
Create document metadata
        ↓
Upload to Snowflake stage
        ↓
Parse document content
        ↓
Store parsed content
        ↓
Create searchable chunks
        ↓
Store chunk metadata
        ↓
Refresh Cortex Search
        ↓
Mark document indexed / ready
        ↓
Available as RAG evidence
```

The UI shows progress through these stages so users can see the pipeline moving from validation to parsing, chunking, storage, and indexing.

Backend logs record the document ID, sanitized filename, stage transitions, page/chunk counts, refresh request, processing status, duration, and failures without logging secrets or extracted document content.

---

# Upload Retrieval Scope

A newly uploaded document receives a **temporary one-question retrieval preference**.

```text
No new upload
      ↓
Search all active/indexed documents

New upload
      ↓
Next question searches the newly uploaded document(s)
      ↓
Temporary scope is consumed
      ↓
Future questions search the normal active knowledge base again
```

The uploaded document remains active and indexed after that question. The **Clear** button only clears the displayed interaction; it does not delete the document or permanently change retrieval scope.

This behavior allows users to immediately test newly uploaded knowledge without locking the application into upload-only retrieval mode.

---

# Document Lifecycle

Typical document states include:

```text
UPLOADING
PARSING
INDEXING
INDEXED
FAILED
DELETED
```

Only active, indexed uploaded documents should participate in retrieval.

Deleted documents are deactivated and excluded from the active search source.

---

# RAG Pipeline

SupportAI treats Cortex Search results as **candidates**, not automatically valid evidence.

```text
User Question
     ↓
Cortex Search Candidate Retrieval
     ↓
Question / Chunk Scoring
     ↓
Intent Filtering
     ↓
Variant Protection
     ↓
Evidence Selection
     ↓
Grounded Prompt
     ↓
Selected Generator
     ↓
Final Answer
```

The filtering layer considers signals such as:

- lexical overlap
- FAQ/section-heading relevance
- query intent
- candidate intent
- distinctive terms
- variant compatibility
- retrieval score

This helps avoid cases such as using standard-shipping evidence for an express-shipping question.

---

# Grounding and Refusal Behavior

The assistant is designed to answer only from retrieved policy evidence.

When sufficient evidence is unavailable, the application returns the configured refusal message:

```text
I couldn't find a policy that answers that question.
```

The system should not fabricate a policy answer from general knowledge.

---

# Evidence Display

Responses can display supporting source information including:

- Document name
- Category
- Chunk index
- Section heading
- Page number when available for uploaded PDF/DOCX documents

Example:

```text
Generated by: OpenRouter (Grok 4.3)
Latency: 1.38s

Source:
supportai_shipping_policy.pdf

Page: 2
Section: 2. Express Shipping
```

---

# UI Performance and Processing Feedback

The UI is designed to avoid unnecessary full-page reruns during interactive operations.

The main SupportAI experience and Document Management experience use Streamlit fragments when the installed Streamlit version supports them. Provider selection and section-level actions therefore remain localized to the relevant part of the page.

Questions use a lightweight **inline spinner** next to the processing status rather than a page-blocking overlay. The spinner communicates that retrieval and generation are running while preserving the normal page layout.

Document-management operations use the same pattern for Retry, Re-index, Delete, and Refresh.

Document upload uses staged progress indicators so the user can see the backend workflow instead of waiting on an unexplained blank state.

The application also records request duration and shows the answer latency in the response metadata.

---

# Project Structure

```text
SupportAI/
│
├── app/
│   ├── controller.py
│   ├── demo_main.py
│   ├── demo_providers.py
│   ├── main.py
│   └── ui.py
│
├── config/
│   ├── __init__.py
│   └── settings.py
│
├── data/
│   ├── payments_faq.md
│   ├── refunds_faq.md
│   ├── returns_faq.md
│   ├── shipping_faq.md
│   ├── warranty_faq.md
│   ├── evaluation_questions.csv
│   ├── provider_evaluation_results.csv
│   └── provider_evaluation_summary.csv
│
├── providers/
│   ├── factory.py
│   ├── openai/
│   │   └── generator.py
│   └── snowflake/
│       ├── connection.py
│       ├── document_store.py
│       ├── generator.py
│       └── retriever.py
│
├── rag/
│   ├── contracts.py
│   ├── prompts.py
│   └── service.py
│
├── scripts/
│   ├── evaluation_summary.py
│   ├── evaluate_providers.py
│   ├── retry_failed_openrouter.py
│   └── generate_seed.py
│
├── sql/
│   ├── foundation.sql
│   ├── 02_seed.sql
│   ├── chunking.sql
│   ├── documents.sql
│   ├── search.sql
│   └── validation.sql
│
├── tests/
│   ├── test_contracts.py
│   ├── test_evaluation.py
│   ├── test_import_boundary.py
│   ├── test_multi_provider.py
│   ├── test_provider_selection.py
│   ├── test_rag_service.py
│   ├── test_snowflake_connection.py
│   ├── test_snowflake_data.py
│   ├── test_snowflake_generator.py
│   └── test_snowflake_retriever.py
│
├── .env.example
├── .gitignore
├── README.md
└── requirements.txt
```

---

# Snowflake Integration

Snowflake is used for persistent knowledge storage and retrieval.

Main components include:

- Snowflake session/connection management
- Document metadata storage
- Extracted document content
- Uploaded document chunks
- Policy source/chunk storage
- Cortex Search
- Snowflake Cortex generation

The active Cortex Search service is:

```text
RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH
```

The Search service uses `POLICY_CHUNKS` as its source and excludes inactive rows.

---

# Configuration

Configuration is centralized in:

```text
config/settings.py
```

Create the local environment file:

```powershell
Copy-Item .env.example .env
```

Example configuration:

```env
RETAIL_ASSIST_MODE=SNOWFLAKE
RETAIL_ASSIST_LOG_LEVEL=INFO
DEFAULT_AI_PROVIDER=snowflake
OPENROUTER_API_KEY=
OPENROUTER_MODEL=x-ai/grok-4.3
OPENROUTER_MAX_TOKENS=256
OPENROUTER_TIMEOUT=60
```

Snowflake variables are configured according to the existing Snowflake connection implementation.

### Secrets

Never commit:

- OpenRouter API keys
- Snowflake credentials
- PATs
- Access tokens
- Other production secrets

The repository should contain only safe placeholder values in `.env.example`.

---

# Installation

Use a supported Python environment.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

The project uses environment loading through `python-dotenv`.

---

# Run the Application

From the project root:

```powershell
$env:PYTHONPATH = "."
streamlit run app/main.py
```

The application provides:

- FAQ question input
- AI-provider selection
- Policy-grounded responses
- Supporting evidence
- Dynamic document upload
- Document processing progress
- Document management
- Inline processing indicators

---

# Local Demo Mode

For local UI development without a live Snowflake dependency:

```powershell
$env:PYTHONPATH = "."
streamlit run app/demo_main.py
```

---

# Testing

Run the full automated test suite:

```powershell
$env:PYTHONPATH = "."
python -m pytest -q
```

The multi-provider tests cover:

- Provider factory behavior
- Unsupported provider handling
- Missing configuration
- Generator interface behavior
- OpenRouter generation success
- Provider API failure handling
- RAG-service orchestration
- Document and retriever contracts where covered by the existing suite

External provider calls are mocked in unit tests so the test suite does not require live or paid provider calls.

---

# Evaluation

The existing evaluation dataset contains 20 questions:

```text
data/evaluation_questions.csv
```

Every question is evaluated against both:

1. Snowflake Cortex
2. OpenRouter (Grok 4.3)

The evaluation retrieves and filters the evidence once and reuses the same final evidence context for both generators.

Run:

```powershell
python scripts/evaluate_providers.py
```

Results are written to:

```text
data/provider_evaluation_results.csv
```

Generate the provider summary:

```powershell
python scripts/evaluation_summary.py
```

Summary output:

```text
data/provider_evaluation_summary.csv
```

---

# Latest Evaluation Snapshot

The latest completed 20-question run observed in development produced:

| Metric | Snowflake Cortex | OpenRouter (Grok 4.3) |
|---|---:|---:|
| Questions evaluated | 20/20 | 20/20 |
| Successful responses | 20/20 | 20/20 |
| Source-correct retrievals | 17/18 | 17/18 |
| Source-correct retrieval rate | 94.4% | 94.4% |
| Unsupported questions handled | 2/2 | 2/2 |
| Average response time | 2.446s | 1.383s |

The source-correct retrieval metric is based on the answerable questions for which an expected source is defined. The supplied dataset does **not** contain an `expected_answer` field, so a numerical answer-correctness or groundedness percentage is not claimed from the CSV alone.

The identical retrieval result is expected because both generation providers receive the same final evidence context.

---

# Evaluation Observations

The evaluation retained difficult retrieval cases instead of removing them to make the results look better.

One answerable question may still select a neighboring or policy-family source even when the broader policy topic is related. These cases remain useful for future retrieval tuning.

Provider latency is also environment-dependent. The measured run above reflects the conditions under which the evaluation was executed and should not be treated as a fixed production SLA.

---

# Provider Factory

Provider creation is centralized in:

```text
providers/factory.py
```

The application requests a provider by name:

```python
generator = create_generator(
    provider_name=provider_name,
    session=session,
    settings=settings,
)
```

The factory returns the appropriate implementation.

This design means adding another generation provider should require changes primarily in:

```text
providers/<new_provider>/
providers/factory.py
```

plus configuration, UI selection, tests, and evaluation wiring.

---

# Adding Another Provider

A new provider should:

1. Implement the existing `Generator` contract.
2. Live inside the provider layer.
3. Be registered in `providers/factory.py`.
4. Receive credentials/settings through centralized configuration.
5. Be added to the UI provider dropdown.
6. Have mocked unit tests.
7. Be added to the evaluation workflow.

The provider should not introduce another retrieval system.

---

# Error Handling

The application handles:

- Unsupported provider values
- Missing provider credentials
- Missing provider model configuration
- Provider authentication failures
- Provider connection failures
- Provider timeouts
- Provider API failures
- Empty provider responses
- Retrieval failures
- Invalid or insufficient evidence
- Document validation, parsing, chunking, and indexing failures

Provider and document errors are converted into user-facing messages while detailed diagnostics remain in the application logs.

The selected provider remains available in the UI after a failed request so the user can retry or change providers.

---

# Logging

The logging layer is designed for operational debugging without exposing secrets or document contents.

Typical document-management logs include:

```text
Document ID
Sanitized filename
File type / size
Processing stage
Parsing start / completion
Page count
Chunking start / completion
Chunk count
Search refresh request
Indexing status
Operation duration
Exception details on failure
```

Typical question logs include provider, request duration, evidence count, and provider/retrieval failures where applicable.

The application does **not** intentionally log API keys, credentials, raw extracted document content, raw prompts, or stage file contents.

---

# Troubleshooting

## `ModuleNotFoundError`

Run from the project root and set:

```powershell
$env:PYTHONPATH = "."
```

Then activate the virtual environment:

```powershell
.venv\Scripts\Activate.ps1
```

and reinstall dependencies:

```powershell
pip install -r requirements.txt
```

## OpenRouter configuration error

Check:

```text
OPENROUTER_API_KEY
OPENROUTER_MODEL
OPENROUTER_MAX_TOKENS
OPENROUTER_TIMEOUT
```

The current target model is:

```text
x-ai/grok-4.3
```

## OpenRouter API failure

Check the provider configuration, account credits/model availability, timeout/network conditions, and provider logs. A provider API failure does not imply a retrieval failure because retrieval remains on Snowflake Cortex Search.

## Uploaded document not retrieved

Debug in this order:

```text
Upload
  ↓
Validation
  ↓
Stage upload
  ↓
Content extraction
  ↓
Document metadata
  ↓
Chunk creation
  ↓
Cortex Search indexing
  ↓
Retrieval
  ↓
Evidence filtering
  ↓
Generation
```

## Wrong source selected

Check:

- document IDs
- active/inactive state
- processing status
- chunk metadata
- Cortex Search results
- evidence filtering

Do not solve a retrieval problem only by modifying the generation prompt.

---

# Git Workflow

Use a dedicated feature branch for the extension:

```powershell
git checkout -b feature/multi-provider-genai
```

Commit changes:

```powershell
git add .
git commit -m "feat: complete multi-provider GenAI extension"
```

Push:

```powershell
git push -u origin feature/multi-provider-genai
```

After review and validation, merge the feature branch into the target branch according to the project's Git workflow.

---

# Recommended Final Validation

Run these before merging:

```powershell
python -m pytest -q
git status
git ls-files .env
```

The final application validation should include:

```text
Snowflake Cortex generation        ✓
OpenRouter / Grok 4.3 generation   ✓
Runtime provider switching         ✓
Shared Cortex Search retrieval     ✓
Grounded RAG evidence              ✓
PDF upload                         ✓
DOCX upload                        ✓
TXT upload                         ✓
Markdown upload                    ✓
Upload progress / lifecycle UI     ✓
Document management tab            ✓
Retry                              ✓
Re-index                           ✓
Delete                             ✓
Refresh                            ✓
Question inline spinner            ✓
Management inline spinner          ✓
Provider error handling            ✓
20-question evaluation             ✓
Automated tests                    ✓
```

---

# Known Limitations

- OpenRouter response latency can vary by model, account state, network conditions, and provider availability.
- External provider credits and rate limits can prevent otherwise valid generation requests.
- Cortex Search indexing and external provider services can introduce variable latency.
- The supplied evaluation dataset does not include expected answers, so numerical answer-correctness and groundedness require explicit review.
- Retrieval-quality tuning is still possible for difficult or ambiguous questions.
- Some questions may require additional intent/entity logic to select the exact expected policy source.
- The current progress indicator communicates the client-visible processing stages; it does not by itself guarantee that Cortex Search serving has completed an asynchronous refresh at the exact instant the refresh request returns.

---

# Project Status

```text
Core RAG architecture              COMPLETE
Snowflake Cortex Search            COMPLETE
Snowflake Cortex generation        COMPLETE
Provider contract                  COMPLETE
Provider factory                   COMPLETE
OpenRouter / Grok 4.3 generation   COMPLETE
Runtime provider switching         COMPLETE
Centralized configuration          COMPLETE
Graceful provider errors           COMPLETE
Dynamic document upload             COMPLETE
PDF support                        COMPLETE
DOCX support                       COMPLETE
TXT support                        COMPLETE
Markdown support                   COMPLETE
Document Management tab            COMPLETE
Retry / Re-index / Delete          COMPLETE
Upload lifecycle progress          COMPLETE
Question / management spinners     COMPLETE
Latency display                    COMPLETE
20-question evaluation             COMPLETE
Automated multi-provider tests     COMPLETE
README documentation               COMPLETE
```

---

# Conclusion

SupportAI demonstrates a practical enterprise-style support architecture where:

- Snowflake Cortex Search remains the authoritative retrieval layer.
- The RAG service remains provider-neutral.
- Generation providers can be switched at runtime.
- Uploaded knowledge documents can be managed dynamically.
- Uploaded documents can be immediately tested with a one-question retrieval preference without being locked into upload-only search.
- Evidence is selected before generation.
- Unsupported questions receive a controlled refusal.
- Provider and document failures are handled without exposing raw exceptions to the user.
- Logging provides operational visibility while avoiding secrets and raw document content.
- UI fragments and lightweight inline processing indicators reduce unnecessary full-page rerendering and make long-running operations clearer.
- The same retrieved context can be used to compare different generation providers.

The result is a maintainable foundation for extending SupportAI with additional generation providers without rebuilding the retrieval and document-management architecture.
