# SupportAI

<<<<<<< HEAD
> **Enterprise-style policy-grounded customer-support assistant with Snowflake Cortex Search, multi-provider RAG, Cortex Analyst, and Cortex Agent orchestration.**

SupportAI is a Streamlit-based customer-support intelligence application designed to answer retail policy questions from approved knowledge sources while also answering structured support-ticket questions and combining both sources in a single response.
=======
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
>>>>>>> 3bef7bb8fbdf9e40dacc94b1e422ebaafe4604cc

The platform separates **retrieval, evidence selection, generation, structured analytics, and agent orchestration** so that each responsibility remains independently testable and maintainable.

<<<<<<< HEAD
---

## 1. Executive Overview

SupportAI provides three user-facing modes:

| Mode | Purpose | Primary Snowflake capability |
|---|---|---|
| **Policy RAG** | Answer policy and FAQ questions using grounded evidence | Cortex Search + selected generator |
| **Support Analytics** | Answer natural-language questions over support-ticket data and expose generated SQL | Cortex Analyst + `SUPPORT_ANALYTICS` |
| **Cortex Agent** | Automatically combine policy and structured analytics tools when required | Cortex Agent + Cortex Search + Cortex Analyst |

The Policy RAG mode supports two generation providers:

- **Snowflake Cortex**
- **Groq** using the OpenAI-compatible API

Both providers use the same Snowflake Cortex Search retrieval path and the same final evidence context for fair comparison.

---

## 2. Key Capabilities

### Policy RAG
- Policy-grounded customer-support responses
- Retrieval-Augmented Generation (RAG)
- Snowflake Cortex Search as the shared retrieval layer
- Evidence scoring and filtering before generation
- Controlled refusal when sufficient policy evidence is unavailable
- Runtime provider selection between Snowflake Cortex and Groq
- Supporting source, section, chunk, and page metadata where available

### Knowledge-Base Management
- PDF, DOCX, TXT, and Markdown uploads
- Document metadata and lifecycle tracking
- Duplicate-document handling
- Retry failed processing
- Re-index documents
- Deactivate/delete documents
- Refresh searchable content
- Upload processing progress
- Temporary one-question retrieval preference for newly uploaded documents

### Structured Support Analytics
- 500 synthetic support-ticket records
- Business-friendly Snowflake Semantic View: `SUPPORT_ANALYTICS`
- Natural-language analytics questions
- Generated SQL visible in the UI
- SQL execution and result handling through Cortex Analyst
- Validation against direct SQL for 10 structured evaluation questions

### Cortex Agent
- One Snowflake Cortex Agent
- Existing policy Cortex Search tool
- `SUPPORT_ANALYTICS` Cortex Analyst tool
- Automatic routing for policy-only, analytics-only, and combined questions
- Agent activity display in the UI
- Combined answers that distinguish policy guidance from operational metrics

### Engineering and Operations
- Provider-neutral `Generator` contract
- Centralized configuration
- Externalized credentials
- Structured application logging
- User-friendly error handling
- Automated tests
- Repeatable evaluation workflows
- Streamlit fragment-based UI updates where supported
- Retrieval and generation latency visibility

---

## 3. Architecture

SupportAI keeps retrieval independent from generation and keeps structured analytics separate from the external provider abstraction.

```text
                                 USER
                                   |
                                   v
                        +----------------------+
                        |     Streamlit UI     |
                        |                      |
                        | Mode Selector        |
                        | Question Input       |
                        | Document Management  |
                        +----------+-----------+
                                   |
                     +-------------+-------------+
                     |             |             |
                     v             v             v
               Policy RAG   Support Analytics   Cortex Agent
                     |             |             |
                     v             v             v
              Cortex Search   Cortex Analyst  Cortex Agent
                     |        SUPPORT_ANALYTICS    |
                     v             |         +-------+-------+
              Evidence Filter     |         |               |
                     |             |         v               v
                     v             v   PolicySearch   SupportAnalytics
                Final Context   Generated SQL   |       |
                     |             |             |       |
              +------+-----+       |             +---+---+
              |            |       |                 |
              v            v       v                 v
       Snowflake Cortex   Groq   SQL Results     Combined Context
          Generator      Generator                    |
              |            |                           |
              +------+-----+                           |
                     |                                 |
                     v                                 v
                Policy Answer                    Agent Answer
```

### Policy RAG request flow

```text
User Question
     |
     v
Snowflake Cortex Search
     |
     v
Candidate Retrieval
     |
     v
Question / Chunk Scoring
     |
     v
Intent + Variant Filtering
     |
     v
Final Evidence
     |
     v
Shared Grounded Prompt
     |
     +------------------------+
     |                        |
     v                        v
Snowflake Cortex            Groq
     |                        |
     +-----------+------------+
                 |
                 v
            Final Answer
```

### Important design principle

**Cortex Search retrieves candidates, the RAG service determines valid evidence, and the selected generator produces the grounded policy answer.**

For provider comparison, both generators receive the same final evidence context so the comparison focuses on generation behavior rather than different retrieval results.

---

## 4. Technology Stack

| Layer | Technology |
|---|---|
| UI | Streamlit |
| Application layer | Python |
| Retrieval | Snowflake Cortex Search |
| Policy generation | Snowflake Cortex / Groq |
| Structured analytics | Snowflake Cortex Analyst |
| Agent orchestration | Snowflake Cortex Agent |
| Semantic layer | Snowflake Semantic View |
| Document processing | PDF / DOCX / TXT / Markdown pipeline |
| Configuration | Environment variables + `config/settings.py` |
| Testing | Pytest |
| Data format | CSV |
| Database / warehouse | Snowflake |

---

## 5. Policy RAG

### Retrieval architecture

Cortex Search is the shared retrieval layer for Policy RAG.

The system does not treat the top Search result as automatically valid evidence. Candidate results are passed through an evidence-selection layer that considers signals including:

- lexical overlap
- FAQ and section-heading relevance
- question intent
- candidate intent
- distinctive terms
- policy-variant compatibility
- Cortex ranking score

This helps prevent incorrect cross-policy matches, such as applying standard-shipping evidence to an express-shipping question.

### Grounding behavior

The generator is instructed to answer only from supplied evidence.

When sufficient evidence is unavailable, the configured refusal is:

```text
I couldn't find a policy that answers that question.
```

The application should not fabricate company policy from general knowledge.

---

## 6. Generation Providers

### Snowflake Cortex

Snowflake Cortex provides the native generation path used by the application.

### Groq

Groq is used through its OpenAI-compatible API.

The current provider adapter is located at:

```text
providers/openai/generator.py
```

The directory name is retained as an internal implementation path for compatibility with the existing provider factory and contracts; the active external generation service is **Groq**.

Current model configuration:

```text
openai/gpt-oss-20b
```

### Provider selection

The **AI Provider** selector is available in **Policy RAG** mode.

Available generation providers:

```text
Snowflake Cortex
Groq
```

The provider applies to the next Policy RAG generation request.

The provider selector is not used by Support Analytics or Cortex Agent mode because those modes use Snowflake-native orchestration.

---

## 7. Support Analytics

Support Analytics provides structured natural-language querying over support tickets.

### Dataset

The project contains a synthetic support-ticket dataset with **500 records** covering:

- Refunds
- Shipping
- Returns
- Payments
- Warranty

The dataset includes fields required for operational analysis such as:

- created date
- category
- status
- priority
- channel
- region
- product family
- resolution time
- customer rating
- escalation state
- refund amount

### Semantic View

The required business-semantics layer is:

```text
RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_ANALYTICS
```

Key dimensions include:

```text
created_date
category
status
priority
channel
region
product_family
```

Core measures and metrics include:

```text
ticket_count
open_ticket_count
closed_ticket_count
average_resolution_hours
average_customer_rating
escalation_rate
total_refund_amount
```

### User experience

The application returns:

1. A natural-language answer
2. The generated SQL in an expandable **Generated SQL** section
3. A request identifier where available

The semantic-layer requirements call for generated SQL to remain visible for learning and verification.

---

## 8. Cortex Agent

Cortex Agent is the Snowflake-native orchestration mode.

### Configured tools

#### `PolicySearch`
Purpose:

```text
Retrieve unstructured policy and FAQ evidence
```

Backed by the existing Cortex Search service:

```text
RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH
```

#### `SupportAnalytics`
Purpose:

```text
Generate and execute SQL over structured support-ticket data
```

Backed by:

```text
RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_ANALYTICS
```

### Automatic routing

The Agent selects tools based on the question.

#### Policy-only

Example:

```text
Can I return a damaged product after 20 days?
```

Expected routing:

```text
PolicySearch
```

#### Analytics-only

Example:

```text
What is the average refund ticket resolution time?
```

Expected routing:

```text
SupportAnalytics
```

#### Combined

Example:

```text
What does the refund policy promise, and how long are refund tickets actually taking?
```

Expected routing:

```text
PolicySearch + SupportAnalytics
```

For combined responses, the application distinguishes policy statements from operational support-ticket metrics.

---

## 9. Mandatory Demonstration Question

The final integrated demonstration question is:

```text
Customers are complaining about refunds. What does our refund policy say,
how many refund tickets did we receive last month, and how does their
average resolution time compare with other categories?
```

This question demonstrates the complete architecture:

```text
Policy retrieval
        +
Structured analytics
        +
Combined grounded response
```

The Agent should use both configured Snowflake tools for this cross-source request.

---

## 10. Document Management

SupportAI includes a dedicated Document Management experience.

Supported formats:

| Format | Extension | Supported |
|---|---|---|
| Markdown | `.md` | Yes |
| PDF | `.pdf` | Yes |
| Microsoft Word | `.docx` | Yes |
| Plain text | `.txt` | Yes |

### Upload lifecycle

```text
Select File
   |
   v
Validate Extension / Size / Empty File
   |
   v
Create Document Metadata
   |
   v
Upload to Snowflake Stage
   |
   v
Parse Content
   |
   v
Store Parsed Content
   |
   v
Create Searchable Chunks
   |
   v
Store Chunk Metadata
   |
   v
Refresh Cortex Search
   |
   v
Mark Indexed / Ready
   |
   v
Available as RAG Evidence
```

### Document lifecycle states

```text
UPLOADING
PARSING
INDEXING
INDEXED
FAILED
DELETED
```

Only active, indexed uploaded documents should participate in normal retrieval.

### Newly uploaded documents

A newly uploaded document can receive a temporary one-question retrieval preference:

```text
New Upload
    |
    v
Next Question Prioritizes New Upload
    |
    v
Scope Consumed
    |
    v
Normal Active Knowledge Base Retrieval
```

The uploaded document remains active after that question.

---

## 11. Snowflake Integration

Snowflake provides the persistent data and intelligence layer for the application.

The platform uses Snowflake for:

- application/session connectivity
- document metadata
- extracted document content
- policy chunks
- uploaded document chunks
- Cortex Search
- support-ticket storage
- Semantic View
- Cortex Analyst
- Cortex Agent
- Snowflake Cortex generation

### Existing Policy Search Service

```text
RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH
```

### Support Analytics Semantic View

```text
RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_ANALYTICS
```

### Cortex Agent

```text
RETAIL_ASSIST_AGENT
```

The Agent uses Snowflake-native orchestration with:

```text
PolicySearch
SupportAnalytics
```

---

## 12. Configuration

Configuration is centralized in:

```text
config/settings.py
```

Create a local environment file:

```powershell
Copy-Item .env.example .env
```

### Safe `.env.example`

Do not commit credentials or tokens.

```env
SNOWFLAKE_ACCOUNT=
SNOWFLAKE_USER=
SNOWFLAKE_PAT=
SNOWFLAKE_WAREHOUSE=
SNOWFLAKE_DATABASE=RETAIL_ASSIST_DB
SNOWFLAKE_SCHEMA=RETAIL_ASSIST
SNOWFLAKE_ROLE=

GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-20b
GROQ_MAX_TOKENS=256
GROQ_TIMEOUT=30

DEFAULT_AI_PROVIDER=snowflake
RETAIL_ASSIST_MODE=SNOWFLAKE
RETAIL_ASSIST_LOG_LEVEL=INFO
```

The local `.env` file should contain the real credentials for the developer environment and must remain outside source control.

### Secret handling

Never commit:

- Snowflake credentials
- PATs
- Groq API keys
- access tokens
- production secrets

---

## 13. Installation

Recommended Python setup:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

The project uses `python-dotenv` for environment configuration.

---

## 14. Run the Application

From the project root:

```powershell
$env:PYTHONPATH = "."
streamlit run app/main.py
```

The main application provides:

- Policy RAG
- Support Analytics
- Cortex Agent
- runtime provider selection in Policy RAG
- supporting evidence
- generated SQL visibility
- Agent activity visibility
- document upload
- document management
- processing indicators
- latency information

---

## 15. Local Demo Mode

For UI development without a live Snowflake dependency:

```powershell
$env:PYTHONPATH = "."
streamlit run app/demo_main.py
```

The demo environment is intended for local UI and component development rather than production validation of Snowflake services.

---

## 16. Testing

Run the complete automated test suite:

```powershell
$env:PYTHONPATH = "."
python -m pytest -q
```

Latest validated result:

```text
27 passed
0 failed
```

### Multi-provider tests cover

- provider factory behavior
- unsupported provider handling
- missing configuration
- Generator interface behavior
- Groq generation behavior
- provider API failure handling
- RAG-service orchestration

External provider calls are mocked in unit tests so the suite does not require paid/live generation requests.

---

## 17. Evaluation Strategy

SupportAI uses three complementary evaluation layers.

### 17.1 Policy RAG Evaluation

The original Policy RAG dataset contains **20 questions**.

Run:

```powershell
python scripts/evaluate_providers.py
```

Results:

```text
data/provider_evaluation_results.csv
```

The evaluation retrieves and filters evidence once and supplies the same final evidence context to:

```text
Snowflake Cortex
Groq
```

### Current Policy RAG evaluation snapshot

| Metric | Result |
|---|---:|
| Questions evaluated | 20/20 |
| Successful Snowflake responses | 20/20 |
| Successful Groq responses | 20/20 |
| Unsupported questions handled | 2/2 |
| Answerable questions | 18 |
| Exact expected-source matches | 13/18 |
| Exact expected-source rate | 72.2% |

The source-match metric is a retrieval/evidence metric. The supplied evaluation dataset does not contain reference answers, so an objective answer-correctness or groundedness percentage is not claimed from this CSV alone.

The evaluation intentionally retains difficult retrieval cases for future tuning rather than removing them to improve the reported metric.

### 17.2 Structured Analytics Evaluation

The project includes at least **10 structured natural-language questions** with known expected values.

Validation is performed against direct SQL queries.

Validated examples include:

| ID | Validation |
|---|---|
| AN-01 | Current open-ticket count |
| AN-02 | Highest average resolution time |
| AN-03 | Monthly ticket volume |
| AN-04 | Lowest average customer rating |
| AN-05 | High-priority tickets in the previous month |
| AN-06 | Escalation rate by category |
| AN-07 | Refunds vs Shipping average resolution time |
| AN-08 | Regions with the largest number of open tickets |
| AN-09 | Monthly refund amount |
| AN-10 | Lowest-rated product family |

Validated structured evaluation status:

```text
10/10 passed
```

### 17.3 Cortex Agent Evaluation

The Agent evaluation contains **12 questions**:

```text
4 policy-only
4 analytics-only
4 combined
```

The evaluation checks:

- expected source/tool type
- answer grounding
- combined-source behavior
- numeric results for analytics questions
- routing behavior

---

## 18. Sample Questions

### Policy RAG

```text
What is the return window for an unused product?
```

```text
What refund processing time does the policy promise?
```

```text
What payment methods does the company accept?
```

### Support Analytics

```text
How many support tickets are currently open?
```

```text
Which category has the highest average resolution time?
```

```text
What is the escalation rate for each support ticket category?
```

### Cortex Agent

```text
What does the refund policy promise, and how long are refund tickets actually taking?
```

```text
Which policy applies to late shipments and how many Shipping tickets were escalated last month?
```

```text
Customers are complaining about refunds. What does our refund policy say,
how many refund tickets did we receive last month, and how does their
average resolution time compare with other categories?
```

---

## 19. Project Structure
=======
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
>>>>>>> 3bef7bb8fbdf9e40dacc94b1e422ebaafe4604cc

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
├── config/
│   ├── __init__.py
│   └── settings.py
│
├── data/
│   ├── evaluation_questions.csv
│   ├── payments_faq.md
│   ├── refunds_faq.md
│   ├── returns_faq.md
│   ├── shipping_faq.md
<<<<<<< HEAD
│   ├── warranty_faq.md
│   ├── support_tickets.csv
│   ├── evaluation_questions.csv
│   └── provider_evaluation_results.csv
│
├── evaluation/
│   ├── analyst_questions.csv
│   └── agent_questions.csv
=======
│   └── warranty_faq.md
>>>>>>> 3bef7bb8fbdf9e40dacc94b1e422ebaafe4604cc
│
├── providers/
│   ├── factory.py
│   ├── openai/
│   │   └── generator.py
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
├── services/
│   ├── cortex_analyst.py
│   └── cortex_agent.py
│
├── sql/
<<<<<<< HEAD
│   ├── 01_setup.sql
│   ├── 02_load_support_tickets.sql
│   ├── 03_semantic_view.sql
│   ├── 04_agent.sql
│   └── 05_validation.sql
│
├── scripts/
│   ├── evaluate_providers.py
│   └── evaluation_summary.py
=======
│   ├── 02_seed.sql
│   ├── chunking.sql
│   ├── documents.sql
│   ├── foundation.sql
│   ├── search.sql
│   └── validation.sql
>>>>>>> 3bef7bb8fbdf9e40dacc94b1e422ebaafe4604cc
│
├── tests/
│   ├── test_contracts.py
│   ├── test_document_store.py
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
<<<<<<< HEAD
├── README.md
└── requirements.txt
=======
├── LICENSE
├── pytest.ini
├── requirements.txt
└── README.md
>>>>>>> 3bef7bb8fbdf9e40dacc94b1e422ebaafe4604cc
```

---

<<<<<<< HEAD
## 20. Error Handling

The application handles:

- unsupported provider values
- missing provider credentials
- missing provider model configuration
- provider authentication failures
- provider connection failures
- provider timeouts
- provider API failures
- empty provider responses
- retrieval failures
- insufficient evidence
- document validation failures
- document parsing failures
- chunking failures
- indexing failures

User-facing errors are intentionally separated from detailed diagnostics.

The application does not expose API keys or raw stack traces through the normal UI.

The selected Policy RAG provider remains available after a failed request so the user can retry or switch providers.

---

## 21. Logging and Observability

The logging layer is intended for operational troubleshooting without logging secrets or document contents.

Typical document logs include:

```text
Document ID
Sanitized filename
File type / size
Processing stage
Parsing start / completion
Page count
Chunk count
Search refresh request
Indexing status
Operation duration
Failure details
```

Typical request logs include:

```text
Provider
Question length
Evidence count
Response duration
Retrieval failures
Generation failures
```

The application does not intentionally log:

- API keys
- credentials
- raw extracted document contents
- full raw prompts
- stage file contents

---

## 22. Troubleshooting

### `ModuleNotFoundError`

Run from the project root:
=======
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
>>>>>>> 3bef7bb8fbdf9e40dacc94b1e422ebaafe4604cc

---

## Verify Document Status

An uploaded document should eventually show:

```text
INDEXED
```

<<<<<<< HEAD
Activate the virtual environment:

```powershell
.venv\Scripts\Activate.ps1
```

Then reinstall dependencies:

```powershell
pip install -r requirements.txt
```

### Groq configuration error

Check:

```text
GROQ_API_KEY
GROQ_MODEL
GROQ_MAX_TOKENS
GROQ_TIMEOUT
```

The current configured model is:

```text
openai/gpt-oss-20b
```

### Groq API failure

Check:

- `GROQ_API_KEY`
- model availability
- timeout configuration
- network connectivity
- provider-side rate limits
- application logs

A Groq generation error does not imply a retrieval failure because Policy RAG retrieval remains on Snowflake Cortex Search.

### Uploaded document not retrieved

Debug the pipeline in this order:
=======
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
>>>>>>> 3bef7bb8fbdf9e40dacc94b1e422ebaafe4604cc

```text
Upload
  ↓
<<<<<<< HEAD
Validation
  ↓
Stage Upload
  ↓
Content Extraction
  ↓
Document Metadata
  ↓
Chunk Creation
  ↓
Cortex Search Indexing
  ↓
Retrieval
  ↓
Evidence Filtering
  ↓
Generation
```

### Wrong policy source selected

Inspect:

- document ID
- active/inactive state
- processing status
- chunk metadata
- Cortex Search candidates
- evidence filtering logic

A retrieval-quality issue should not be solved only by modifying the generation prompt.

---

## 23. Security and Source-Control Practices

Before committing the project:

```powershell
git status
git ls-files .env
```

`.env` should not be tracked.

Only safe placeholders belong in:

```text
.env.example
```

Do not place real PATs, API keys, or access tokens in source files, documentation, screenshots, notebooks, or committed test fixtures.

---

## 24. Final Validation Checklist

### Core application

```text
Policy RAG mode                         ✓
Support Analytics mode                 ✓
Cortex Agent mode                      ✓
Snowflake Cortex generation            ✓
Groq generation                        ✓
Runtime provider selection             ✓
Shared Cortex Search retrieval         ✓
Grounded evidence filtering            ✓
```

### Structured analytics

```text
500 support-ticket records             ✓
SUPPORT_ANALYTICS semantic view        ✓
Cortex Analyst integration             ✓
Generated SQL visible in UI            ✓
10/10 structured evaluation            ✓
Direct SQL validation                  ✓
```

### Cortex Agent

```text
PolicySearch tool                      ✓
SupportAnalytics tool                  ✓
Policy-only routing                    ✓
Analytics-only routing                 ✓
Combined routing                       ✓
12-question Agent evaluation           ✓
Agent tool activity in UI              ✓
```

### Document management

```text
PDF upload                             ✓
DOCX upload                            ✓
TXT upload                             ✓
Markdown upload                        ✓
Processing lifecycle                  ✓
Retry                                  ✓
Re-index                               ✓
Delete / deactivate                    ✓
Refresh                                ✓
```

### Engineering

```text
Automated test suite                   ✓
27 tests passing                       ✓
Provider errors handled                ✓
Structured logging                     ✓
Externalized secrets                  ✓
```

---

## 25. Known Limitations

- The Policy RAG evaluation dataset contains expected sources and answerability but does not contain reference answers. Therefore answer-level correctness and groundedness require explicit human review.
- Retrieval quality can still be improved for difficult or ambiguous policy questions.
- Provider latency varies with runtime environment, model behavior, network conditions, and provider availability.
- Snowflake Search indexing and serving can introduce asynchronous or variable latency.
- External provider rate limits can affect generation availability.
- The current provider adapter retains the historical `providers/openai/` implementation path while using Groq's OpenAI-compatible API.
- The temporary new-upload retrieval preference is intentionally limited to one subsequent question before normal active-document retrieval resumes.

---

## 26. Definition of Done

The implementation satisfies the major extension objectives:

| Acceptance criterion | Status |
|---|---|
| Existing Policy RAG remains functional | ✅ |
| Multi-provider generation with Snowflake Cortex + Groq | ✅ |
| 500+ synthetic support tickets | ✅ |
| Support-ticket semantic layer | ✅ |
| `SUPPORT_ANALYTICS` Semantic View | ✅ |
| 10 structured analytics questions validated | ✅ |
| Generated SQL visible in UI | ✅ |
| Cortex Agent configured with Search + Analyst | ✅ |
| Policy-only Agent routing | ✅ |
| Analytics-only Agent routing | ✅ |
| Combined Agent routing | ✅ |
| Three-mode UI | ✅ |
| 12-question Agent evaluation | ✅ |
| 20-question Policy RAG evaluation | ✅ |
| Automated test suite | ✅ |

---

## 27. Conclusion

SupportAI demonstrates a maintainable support-intelligence architecture in which:

- **Snowflake Cortex Search** remains the authoritative policy retrieval layer.
- **RAGService** controls evidence selection and grounding.
- **Snowflake Cortex and Groq** provide interchangeable Policy RAG generation paths.
- **Cortex Analyst** provides governed structured analytics through the `SUPPORT_ANALYTICS` semantic layer.
- **Cortex Agent** orchestrates policy retrieval and structured analytics for cross-source questions.
- **Document management** allows the knowledge base to evolve without redesigning the retrieval layer.
- **Automated tests and repeatable evaluations** provide engineering confidence.
- **Structured logging and user-friendly error handling** provide operational visibility without exposing secrets.

The resulting platform is designed as an extensible foundation for enterprise customer-support workflows where policy guidance and operational support data need to be answered independently or combined into a single grounded response.
=======
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
>>>>>>> 3bef7bb8fbdf9e40dacc94b1e422ebaafe4604cc
