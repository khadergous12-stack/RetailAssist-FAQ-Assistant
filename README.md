# SupportAI

## AI-Powered FAQ Assistant using RAG and Snowflake

SupportAI is an AI-powered FAQ assistant designed to provide accurate, grounded answers from a controlled knowledge base.

The application combines **Retrieval-Augmented Generation (RAG)** with **Snowflake** to retrieve relevant support information and generate answers based only on available policy and FAQ content.

SupportAI also provides document management capabilities, allowing users to upload support documents, process them into searchable chunks, index them, retrieve relevant evidence, and use that evidence to generate grounded responses.

---

## Overview

Traditional FAQ systems generally depend on keyword matching or manually maintained question-and-answer pairs. This can make them difficult to maintain and less flexible when users ask questions in different ways.

SupportAI addresses this by using a Retrieval-Augmented Generation pipeline.

Instead of asking the language model to answer from its general knowledge, SupportAI:

1. Accepts a user's question.
2. Searches the available knowledge base.
3. Retrieves relevant document chunks.
4. Applies relevance and intent-based filtering.
5. Uses the filtered evidence as context.
6. Generates a grounded answer.
7. Returns the answer along with supporting evidence.

This approach helps reduce irrelevant answers and keeps responses aligned with the organization's support documentation.

---

## Key Features

### AI-Powered FAQ Assistant

Users can ask natural-language questions instead of searching for exact FAQ wording.

Example:

> How long does delivery take?

SupportAI can identify the relevant shipping policy and return an answer based on the available documentation.

---

### Retrieval-Augmented Generation

SupportAI follows a RAG architecture where retrieval happens before answer generation.

```text
User Question
     ↓
Query Processing
     ↓
Snowflake Retrieval
     ↓
Relevant Chunks
     ↓
Relevance + Intent Filtering
     ↓
Evidence
     ↓
Grounded Prompt
     ↓
LLM Generation
     ↓
Final Answer
```

---

### Snowflake-Based Knowledge Base

Snowflake is used as the central data and retrieval layer.

The application stores document metadata and processed document information in Snowflake and uses Snowflake-based retrieval to locate relevant knowledge.

---

### Document Upload

Users can upload support documents through the application.

Supported document types include:

- Markdown (`.md`)
- PDF (`.pdf`)

Uploaded documents are processed and indexed so that their contents can be used by the assistant.

---

### Document Lifecycle Management

SupportAI maintains document processing states.

Typical lifecycle:

```text
UPLOADED
   ↓
PROCESSING
   ↓
INDEXED
   ↓
ACTIVE
```

Documents that are removed from the active knowledge base are marked as:

```text
DELETED
ACTIVE = FALSE
```

This preserves document history while preventing deleted documents from being used during retrieval.

---

### Evidence-Based Answers

SupportAI does not simply generate an answer whenever a question is received.

The system first checks whether relevant evidence exists.

If suitable evidence cannot be found, the assistant returns a controlled response instead of inventing an answer.

Example:

> I couldn't find a policy that answers that question.

This helps maintain grounded responses.

---

### Relevance Filtering

Retrieved chunks are evaluated using multiple signals, including:

- Query terms
- FAQ question similarity
- Section headings
- Category information
- Intent compatibility
- Distinctive terms
- Delivery variants
- Answer type
- Retrieval score

This additional filtering layer helps prevent semantically incorrect documents from being used as evidence.

---

### Intent-Aware Retrieval

SupportAI distinguishes between different types of support questions.

Examples include:

- Duration
- Tracking
- Refund
- Cost
- Procedure
- Return
- Damage
- Eligibility

For example, a question about:

> How long does express delivery take?

should not incorrectly use a standard-delivery policy simply because both documents contain the words "delivery" and "take".

The retrieval filtering layer therefore applies variant and intent checks before evidence is accepted.

---

## Technology Stack

### Programming Language

- Python

### Application Framework

- Streamlit

### Database / Data Platform

- Snowflake

### AI Architecture

- Retrieval-Augmented Generation (RAG)
- Snowflake-based retrieval
- Grounded LLM generation

### Document Processing

- Markdown processing
- PDF processing
- Document chunking
- Metadata extraction

---

## System Architecture

```text
                    ┌─────────────────────┐
                    │      Streamlit      │
                    │         UI          │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    Application      │
                    │      / Services     │
                    └──────────┬──────────┘
                               │
                 ┌─────────────┴─────────────┐
                 │                           │
                 ▼                           ▼
        ┌─────────────────┐        ┌─────────────────┐
        │ Document        │        │ RAG Service     │
        │ Processing      │        │                 │
        └────────┬────────┘        └────────┬────────┘
                 │                          │
                 ▼                          ▼
        ┌─────────────────┐        ┌─────────────────┐
        │ Chunking &      │        │ Snowflake       │
        │ Indexing        │        │ Retriever       │
        └────────┬────────┘        └────────┬────────┘
                 │                          │
                 └────────────┬─────────────┘
                              ▼
                    ┌─────────────────────┐
                    │      Snowflake      │
                    │ Knowledge Base      │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Evidence + Context  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ LLM / Generator     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Grounded Answer     │
                    └─────────────────────┘
```

---

# RAG Pipeline

SupportAI's RAG pipeline consists of several stages.

## 1. User Question

The user enters a natural-language question through the Streamlit interface.

Example:

```text
How long does delivery take?
```

---

## 2. Retrieval

The question is sent to the Snowflake retrieval layer.

The retriever searches the available knowledge base and returns relevant document chunks.

A retrieved chunk contains information such as:

- Chunk ID
- Document ID
- Document name
- Category
- Chunk index
- Chunk text
- Retrieval score
- Page number
- Source type
- Section heading

---

## 3. Relevance Scoring

Retrieved chunks are evaluated by the RAG service.

The scoring layer considers:

- Word overlap
- FAQ question similarity
- Section relevance
- Category relevance
- Intent compatibility
- Distinctive terms
- Retrieval score

This prevents generic keyword overlap from being treated as sufficient evidence.

---

## 4. Intent Filtering

The system determines the intent of the user's question.

For example:

```text
How long does delivery take?
        ↓
duration
```

While:

```text
Where is my package?
        ↓
tracking
```

And:

```text
Who pays for return shipping?
        ↓
cost
```

The retrieved chunks are then checked against the detected intent.

---

## 5. Variant Filtering

SupportAI also protects against conflicting delivery variants.

For example:

```text
Standard delivery
Express delivery
International delivery
Overnight delivery
```

If the user explicitly asks about express delivery, standard-delivery evidence should not be selected simply because it contains similar words.

---

## 6. Evidence Selection

Only chunks that pass the relevance and intent gates are accepted as evidence.

If no suitable evidence remains, SupportAI returns a controlled refusal.

```text
Question
   ↓
Retrieved Chunks
   ↓
Relevance Scoring
   ↓
Intent Filtering
   ↓
Variant Filtering
   ↓
Valid Evidence
```

---

## 7. Grounded Generation

The selected evidence is inserted into a grounded prompt.

The generator is instructed to answer using the available evidence instead of relying on unsupported information.

---

## 8. Final Response

The final response contains the generated answer and associated evidence.

Example:

```text
Question:
How long does delivery take?

Answer:
Standard delivery normally takes 3 to 5 business days after
the order is shipped.
```

---

# Document Processing

SupportAI allows users to upload knowledge documents.

The processing flow is:

```text
Upload Document
      ↓
Validate File
      ↓
Extract Content
      ↓
Calculate Metadata
      ↓
Create Chunks
      ↓
Store Metadata
      ↓
Index Content
      ↓
Mark as INDEXED
      ↓
Available for Retrieval
```

---

## Supported Files

### Markdown

```text
.md
```

Markdown files are processed as text and divided into searchable chunks.

### PDF

```text
.pdf
```

PDF files are processed page-by-page and their metadata includes page information where available.

---

# Document Metadata

The document metadata table contains information such as:

| Column | Description |
| --- | --- |
| `DOCUMENT_ID` | Unique document identifier |
| `ORIGINAL_FILENAME` | Original uploaded filename |
| `SANITIZED_FILENAME` | Safe filename used by the system |
| `STAGED_FILE_PATH` | Staged file location |
| `FILE_TYPE` | Document type |
| `FILE_SIZE` | File size |
| `CONTENT_HASH` | Content hash used for identification |
| `CATEGORY` | Document category |
| `DESCRIPTION` | Document description |
| `TAGS` | Document tags |
| `UPLOADED_BY` | User who uploaded the document |
| `PROCESSING_STATUS` | Current processing state |
| `PAGE_COUNT` | Number of pages where applicable |
| `CHARACTER_COUNT` | Number of extracted characters |
| `CHUNK_COUNT` | Number of generated chunks |
| `ERROR_MESSAGE` | Processing error information |
| `CREATED_AT` | Creation timestamp |
| `UPDATED_AT` | Last update timestamp |
| `ACTIVE` | Whether the document is active |

---

# Document Status

The system uses processing status to track document state.

Common statuses include:

```text
UPLOADED
PROCESSING
INDEXED
DELETED
```

An active document is expected to have:

```text
PROCESSING_STATUS = INDEXED
ACTIVE = TRUE
```

Deleted documents are retained as historical records but are not intended to participate in active retrieval.

---

# Project Structure

A typical SupportAI project structure is:

```text
SupportAI/
│
├── app/
│   ├── __init__.py
│   ├── controller.py
│   ├── demo_main.py
│   ├── demo_providers.py
│   ├── main.py
│   └── ui.py
│
├── data/
│   ├── evaluation_questions.csv
│   ├── payments_faq.md
│   ├── refunds_faq.md
│   ├── returns_faq.md
│   ├── shipping_faq.md
│   └── warranty_faq.md
│
├── providers/
│   └── snowflake/
│       ├── __init__.py
│       ├── connection.py
│       ├── document_store.py
│       ├── generator.py
│       └── retriever.py
│
├── rag/
│   ├── __init__.py
│   ├── contracts.py
│   ├── prompts.py
│   └── service.py
│
├── scripts/
│   └── generate_seed.py
│
├── sql/
│   ├── 02_seed.sql
│   ├── chunking.sql
│   ├── documents.sql
│   ├── foundation.sql
│   ├── search.sql
│   └── validation.sql
│
├── tests/
│   ├── test_contracts.py
│   ├── test_document_store.py
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
├── LICENSE
├── pytest.ini
├── requirements.txt
└── README.md
```

---

# Core Components

## Streamlit Application

The Streamlit application provides the user interface for:

- Asking questions
- Uploading documents
- Viewing document information
- Managing documents
- Viewing responses and evidence

---

## Snowflake Connection

The Snowflake connection layer is responsible for establishing the application's Snowflake session.

The application uses a dedicated connection factory rather than creating connections throughout the application.

---

## Snowflake Retriever

The retriever is responsible for:

- Receiving the user query
- Searching the knowledge base
- Retrieving relevant chunks
- Returning structured retrieval results

---

## Snowflake Generator

The generator is responsible for sending the grounded prompt to the configured language-generation service and returning the generated answer.

---

## RAG Service

The RAG service coordinates the complete question-answering workflow.

Conceptually:

```text
retrieved = retriever.retrieve(question)

evidence = filter_evidence(question, retrieved)

answer = generator.generate(
    grounded_prompt(question, evidence)
)
```

The service also handles refusal behavior when suitable evidence is unavailable.

---

# Grounding and Refusal Behavior

A key design principle of SupportAI is:

> **Do not answer beyond the available knowledge base.**

If relevant evidence cannot be identified, the system returns:

```text
I couldn't find a policy that answers that question.
```

This behavior is preferable to generating an unsupported answer.

---

# Example Questions

The system can handle questions such as:

### Shipping

```text
How long does standard delivery take?
```

Expected knowledge:

```text
Standard delivery normally takes 3 to 5 business days
after the order is shipped.
```

### Express Shipping

```text
How long does express delivery take?
```

Expected knowledge:

```text
Express delivery is normally completed within 1–3
business days after dispatch.
```

### Refunds

```text
How long does a refund take?
```

### Returns

```text
How long do I have to return a product?
```

### Payments

```text
Why was my card declined?
```

### Tracking

```text
What should I do if tracking has not updated?
```

---

# Installation

## Prerequisites

Before running SupportAI, make sure the following are installed:

- Python 3.x
- Snowflake account
- Required Snowflake database/schema objects
- Required Python packages

---

## Clone the Repository

```bash
git clone <repository-url>
cd SupportAI
```

---

## Create a Virtual Environment

### Windows

```powershell
python -m venv .venv
```

Activate it:

```powershell
.venv\Scripts\activate
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

---

# Install Dependencies

Install the required packages:

```bash
pip install -r requirements.txt
```

---

# Environment Configuration

SupportAI requires configuration values for connecting to Snowflake and the configured AI services.

Create a `.env` file in the project root.

Example:

```text
SNOWFLAKE_ACCOUNT=<your-account>
SNOWFLAKE_USER=<your-user>
SNOWFLAKE_PASSWORD=<your-password>
SNOWFLAKE_WAREHOUSE=<your-warehouse>
SNOWFLAKE_DATABASE=SUPPORTAI_DB
SNOWFLAKE_SCHEMA=SUPPORTAI
SNOWFLAKE_ROLE=<your-role>
```

Add any additional model or service configuration required by the current generator implementation.

### Important

Never commit credentials or secrets to source control.

Make sure `.env` is included in `.gitignore`.

Example:

```text
.env
.venv/
__pycache__/
*.pyc
```

---

# Snowflake Setup

SupportAI uses Snowflake as the backend knowledge platform.

The application expects the required database and schema to exist.

Example:

```text
Database:
SUPPORTAI_DB

Schema:
SUPPORTAI
```

The document metadata table is:

```text
SUPPORTAI_DB.SUPPORTAI.DOCUMENTS
```

The exact names can be changed according to the deployment environment and application configuration.

---

# Running the Application

From the project root:

```bash
streamlit run app/main.py
```

After starting the application, Streamlit will provide a local URL.

Open the URL in your browser.

---

# Basic Usage

## Ask a Question

Enter a support question into the chat interface.

Example:

```text
How long does delivery take?
```

SupportAI retrieves relevant knowledge and generates a grounded response.

---

## Upload a Document

Use the document upload functionality to add a new support document.

Supported formats:

```text
PDF
Markdown
```

After successful processing, the document should become available for retrieval.

---

## Verify Document Status

An uploaded document should eventually show:

```text
INDEXED
```

and:

```text
ACTIVE = TRUE
```

---

## Delete a Document

When a document is deleted, the system marks it inactive.

Expected state:

```text
PROCESSING_STATUS = DELETED
ACTIVE = FALSE
```

The document should no longer be used as active retrieval evidence.

---

# Testing

SupportAI has been tested across multiple areas of the application.

## Backend Validation

The following areas can be validated independently:

- Snowflake connection
- Document metadata
- Document processing
- Chunk generation
- Retrieval
- Evidence filtering
- Answer generation

---

## Retrieval Testing

Example:

```text
How long does delivery take?
```

The system should prioritize the relevant shipping FAQ.

---

## Variant Testing

Example:

```text
How long does express delivery take?
```

The system should prioritize express-delivery evidence rather than standard-delivery evidence.

---

## Refusal Testing

For questions that are not covered by the knowledge base, the system should not invent an answer.

Expected behavior:

```text
I couldn't find a policy that answers that question.
```

---

## Document Lifecycle Testing

The following lifecycle should work correctly:

```text
Upload
  ↓
Process
  ↓
Index
  ↓
Retrieve
  ↓
Answer
  ↓
Delete
  ↓
Inactive
```

---

# Error Handling

SupportAI includes controlled handling for situations such as:

- Empty questions
- Missing retrieval results
- Invalid evidence
- Unsupported answers
- Document processing failures
- Snowflake-related failures
- Generation failures

The RAG service validates the retrieved evidence before allowing generation to proceed.

---

# Design Principles

SupportAI follows several core principles.

## 1. Grounded Responses

Answers should be based on retrieved knowledge.

---

## 2. Evidence Before Generation

Generation should happen only after relevant evidence has been identified.

---

## 3. Conservative Retrieval

The system should prefer rejecting weak evidence over using clearly unrelated evidence.

---

## 4. Intent Awareness

Different support questions can have different answer types.

For example:

```text
Duration ≠ Tracking ≠ Refund ≠ Cost
```

The retrieval layer accounts for these distinctions.

---

## 5. Document Lifecycle Awareness

Deleted documents should not remain active in the retrieval pipeline.

---

## 6. Maintainability

The application separates major responsibilities into dedicated components such as:

```text
Application
     ↓
RAG Service
     ↓
Retriever
     ↓
Snowflake
     ↓
Generator
```

This makes individual components easier to test and maintain.

---

# Security Considerations

SupportAI should be deployed using secure configuration practices.

### Never expose:

- Snowflake passwords
- API keys
- Access tokens
- Private credentials
- Production secrets

in source code or public repositories.

Use environment variables or a secure secrets-management solution.

---

# Troubleshooting

## Snowflake Connection Error

Verify:

- Account identifier
- Username
- Password/authentication configuration
- Warehouse
- Database
- Schema
- Role
- Network access

---

## ModuleNotFoundError

Make sure the application is being run from the project root and that the virtual environment is activated.

Example:

```powershell
cd <SupportAI-project-folder>
.venv\Scripts\activate
```

Then run:

```bash
streamlit run app/main.py
```

---

## No Answer Returned

Check:

1. Whether the document is indexed.
2. Whether the document is active.
3. Whether retrieval returns chunks.
4. Whether evidence filtering accepts the chunks.
5. Whether the generator is configured correctly.

---

## Uploaded Document Not Retrieved

Verify that the document has:

```text
PROCESSING_STATUS = INDEXED
ACTIVE = TRUE
```

Also verify that the document contains information relevant to the user's question.

---

# Current Knowledge Base Examples

The current SupportAI knowledge base can contain support content covering areas such as:

- Payments
- Shipping
- Returns
- Refunds
- Warranty
- Delivery policies

Additional categories can be added through document upload and processing.

---

# Future Enhancements

Potential future improvements include:

- Improved conversational memory
- More advanced metadata filtering
- Hybrid keyword + vector retrieval
- Improved document version management
- Automated document re-indexing
- Authentication and role-based access
- Usage analytics
- Feedback collection
- Response evaluation
- Admin dashboard
- Monitoring and observability
- Automated evaluation datasets
- Multi-language support
- Production deployment automation

---

# Project Goals

The primary goals of SupportAI are:

```text
Accurate Retrieval
       +
Grounded Generation
       +
Reliable Document Management
       +
Snowflake Integration
       +
Simple User Experience
```

Together, these provide a practical AI-powered support assistant that can answer questions using an organization's controlled knowledge base.

---

# Conclusion

SupportAI demonstrates how Retrieval-Augmented Generation can be combined with Snowflake and a Streamlit interface to build a practical AI-powered FAQ assistant.

The system goes beyond basic semantic search by introducing:

- Evidence filtering
- Intent detection
- Variant protection
- Grounded generation
- Controlled refusal behavior
- Document lifecycle management
- Active/inactive document handling

This architecture provides a foundation for building a reliable and maintainable enterprise support assistant.

---

## Author

**Khader Gouse**

AI / ML Project Intern

---

## Project

**SupportAI — AI-Powered FAQ Assistant**

Built using:

- Python
- Streamlit
- Snowflake
- RAG
- LLM-based Generation
- Document Processing
