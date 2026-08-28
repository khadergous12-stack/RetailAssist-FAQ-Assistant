# RetailAssist FAQ Assistant
=======
# SupportAI

A policy-grounded customer-support assistant that answers retail FAQ questions using approved policy documents, retrieval-augmented generation (RAG), and Snowflake Cortex Search.

The project has been extended with **dynamic document upload and Snowflake-backed knowledge management**, allowing new PDF, DOCX, TXT, and Markdown documents to be uploaded from the Streamlit UI, categorized, stored in Snowflake, chunked for retrieval, and used as policy evidence without changing application code.

## Key Features

- Policy-grounded customer-support answers
- Retrieval-Augmented Generation (RAG)
- Snowflake document storage
- Snowflake Cortex Search integration
- Snowflake Cortex generation support
- Dynamic document upload from the Streamlit UI
- PDF, DOCX, TXT, and Markdown document support
- Automatic document category detection
- Document metadata and chunk tracking
- Duplicate document handling
- Relevant evidence/source display in the UI
- Provider-neutral RAG architecture
- Local/demo mode for development and testing
- Automated tests and manual FAQ validation

## Architecture

```text
                         ┌──────────────────────┐
                         │     Streamlit UI     │
                         │  Ask / Upload Docs   │
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │      Controller      │
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │      RAGService      │
                         └──────────┬───────────┘
                                    │
                   ┌────────────────┴────────────────┐
                   │                                 │
          ┌────────▼────────┐               ┌────────▼────────┐
          │    Retriever    │               │    Generator    │
          │    Contract     │               │    Contract     │
          └────────┬────────┘               └────────┬────────┘
                   │                                 │
          ┌────────▼────────┐               ┌────────▼────────┐
          │ Snowflake       │               │ Snowflake       │
          │ Retriever       │               │ Cortex          │
          └────────┬────────┘               │ Generator       │
                   │                        └─────────────────┘
          ┌────────▼────────┐
          │ Cortex Search   │
          └────────┬────────┘
                   │
          ┌────────▼────────┐
          │ Policy Chunks   │
          └─────────────────┘

Document Upload Flow

User Upload
    ↓
PDF / DOCX / TXT / MD
    ↓
Document Validation
    ↓
Category Detection
    ↓
Duplicate Check
    ↓
Snowflake Document Storage
    ↓
Chunk Ingestion
    ↓
Cortex Search
    ↓
Available as RAG Evidence
```

## Project Structure

```text
<<<<<<< HEAD
RetailAssist FAQ Assistant/
=======
SupportAI/
>>>>>>> supportai
│
├── app/
│   ├── controller.py
│   ├── demo_main.py
│   ├── demo_providers.py
│   ├── main.py
│   └── ui.py
│
├── data/
│   ├── payments_faq.md
│   ├── refunds_faq.md
│   ├── returns_faq.md
│   ├── shipping_faq.md
│   ├── warranty_faq.md
│   └── evaluation_questions.csv
│
├── providers/
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
├── requirements.txt
└── ...
```

## Supported Document Formats

The dynamic upload feature accepts:

| Format | Supported |
|---|---|
| Markdown (`.md`) | Yes |
| PDF (`.pdf`) | Yes |
| DOCX (`.docx`) | Yes |
| TXT (`.txt`) | Yes |

Uploaded documents are processed through the same knowledge pipeline and become available as retrieval evidence after ingestion.

## Dynamic Document Upload

The Streamlit knowledge-base section allows an authorized user to upload new policy documents without modifying the source code.

The upload pipeline performs:

1. File type validation
2. Document text extraction
3. Category detection
4. Duplicate document handling
5. Snowflake document storage
6. Policy chunk ingestion
7. Retrieval availability through Cortex Search

The existing application architecture remains provider-neutral; document persistence is isolated in the Snowflake provider layer.

## Knowledge Base

The original policy corpus contains five FAQ categories:

```text
PAYMENTS
REFUNDS
RETURNS
SHIPPING
WARRANTY
```

New documents can be added dynamically through the UI while preserving the same category and evidence model.

## Snowflake Integration

Snowflake is used for persistent policy storage and retrieval.

The main Snowflake components are:

- Snowflake connection/session management
- Document storage
- Policy source metadata
- Policy chunk storage
- Cortex Search retrieval
- Snowflake Cortex generation

### SQL Setup

Run the required SQL scripts in the project-defined order:

```text
1. sql/foundation.sql
2. sql/02_seed.sql
3. sql/chunking.sql
4. sql/documents.sql
5. sql/search.sql
6. sql/validation.sql
```

The exact order can be adjusted if your Snowflake environment already contains the foundation objects required by the document-upload flow.

## Configuration

Create the local environment file:

### Windows PowerShell

```powershell
Copy-Item .env.example .env
```

Configure the required Snowflake values used by the application, for example:

```text
SNOWFLAKE_ACCOUNT
SNOWFLAKE_USER
SNOWFLAKE_PASSWORD
SNOWFLAKE_WAREHOUSE
SNOWFLAKE_DATABASE
SNOWFLAKE_SCHEMA
SNOWFLAKE_ROLE
SNOWFLAKE_CORTEX_MODEL
```

Use the authentication method implemented by `providers/snowflake/connection.py`.

**Never commit `.env` or expose credentials in logs, screenshots, README files, or GitHub.**

## Installation

Use Python 3.11 or a version supported by the installed Snowflake dependencies.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Run the Application

From the project root:

```powershell
$env:PYTHONPATH = "."
streamlit run app/main.py
```

The application provides:

- Customer-support FAQ question input
- Policy-grounded answer generation
- Supporting evidence/source display
- Dynamic knowledge-base document upload
- Snowflake upload/integration workflow

## Local Demo Mode

For local UI development without a live Snowflake/Cortex dependency:

```powershell
$env:PYTHONPATH = "."
streamlit run app/demo_main.py
```

The demo uses the provider implementations intended for local testing.

## Testing

Run the automated test suite from the project root:

```powershell
$env:PYTHONPATH = "."
python -m pytest -q
```

The project was also manually tested against the FAQ questions and the dynamic document/Snowflake workflow.

### Validation Completed

The final validation covered:

- Payment FAQ retrieval
- Shipping FAQ retrieval
- Refund FAQ retrieval
- Return FAQ retrieval
- Warranty FAQ retrieval
- Unsupported-question/refusal behavior
- Source relevance and evidence display
- Duplicate-document handling
- Dynamic document upload
- Snowflake document ingestion
- Snowflake retrieval integration
- Cortex Search test document flow

All planned application tests for the current feature extension were completed successfully.

## Retrieval and Grounding

<<<<<<< HEAD
RetailAssist is designed to answer only from retrieved policy evidence.
=======
SupportAI is designed to answer only from retrieved policy evidence.
>>>>>>> supportai

The RAG flow is:

```text
User Question
     ↓
Retriever
     ↓
Relevant Policy Chunks
     ↓
Grounded RAG Context
     ↓
Generator
     ↓
Policy-Grounded Answer
     ↓
Supporting Sources
```

If sufficient policy evidence is not found, the application should avoid inventing an answer and return the configured unsupported-question response.

## Source Relevance

The retrieval layer includes relevance/source selection logic so that multiple documents containing similar FAQ wording do not unnecessarily dominate the displayed evidence.

This is especially important when the same policy question exists in different document formats or when legacy and newly uploaded documents contain overlapping content.

The system tracks document identity and metadata so retrieval results can be associated with the correct uploaded source.

## Security

- Keep `.env` out of source control.
- Never commit passwords, PATs, or Snowflake credentials.
- Do not print complete environment configuration.
- Use the minimum Snowflake role privileges required by the application.
- Keep Snowflake-specific imports and logic inside `providers/snowflake/`.
- Do not place secrets in screenshots or demo recordings.

## Troubleshooting

### `ModuleNotFoundError`

Activate the virtual environment and reinstall dependencies:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Set the project root on `PYTHONPATH`:

```powershell
$env:PYTHONPATH = "."
```

### Snowflake authentication failure

Check the account, user, authentication method, warehouse, database, schema, role, and network policy.

### Cortex Search unavailable

Verify that the Snowflake account/region provides Cortex Search and that the active role has access to the required service and underlying objects.

### Uploaded document is not retrieved

Debug in this order:

```text
Upload
  ↓
Text extraction
  ↓
Category/document metadata
  ↓
Snowflake storage
  ↓
Chunk ingestion
  ↓
Cortex Search indexing
  ↓
Retrieval
  ↓
RAG response
```

### Wrong or duplicate sources appear

Check document IDs and chunk metadata first. Do not solve retrieval problems only by changing the generation prompt.

## Git Workflow

The extended functionality was developed on a dedicated feature branch:

```text
feature/dynamic-document-upload
```

After testing, the feature branch was merged into `main` through a GitHub pull request.

Recommended workflow for future changes:

```powershell
git checkout -b feature/<feature-name>
git add .
git commit -m "feat: <description>"
git push -u origin feature/<feature-name>
```

Then open a pull request into `main`.

```

## Project Summary

RetailAssist demonstrates how a retail customer-support assistant can combine **RAG, policy-grounded generation, dynamic knowledge ingestion, and Snowflake** into a maintainable application architecture.
=======
SupportAI demonstrates how a retail customer-support assistant can combine **RAG, policy-grounded generation, dynamic knowledge ingestion, and Snowflake** into a maintainable application architecture.

The extended version moves beyond a fixed FAQ dataset: new supported policy documents can be uploaded through the application, persisted in Snowflake, processed into retrieval chunks, and incorporated into the knowledge base while keeping the core RAG orchestration independent of Snowflake-specific implementation details.
