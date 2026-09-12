# SupportAI — RetailAssist FAQ Assistant v2

SupportAI is an AI-powered customer-support assistant built with **Python, Streamlit, Snowflake Cortex, RAG, Cortex Analyst, and Cortex Agent**.

The application combines:

- Grounded policy/FAQ question answering
- Multi-provider Policy RAG generation
- Structured support-ticket analytics
- Snowflake Cortex Agent orchestration
- Document upload and lifecycle management
- User authentication and administrator controls
- Operational logging and evaluation workflows

The central design principle is:

> **Cortex Search retrieves candidates, the RAG service selects valid evidence, and the selected generator produces the grounded answer.**

---

## 1. What SupportAI Does

SupportAI provides three main application modes.

| Mode | Purpose | Main Snowflake capability |
|---|---|---|
| **Policy RAG** | Answer questions from approved policies and FAQs | Cortex Search + selected generator |
| **Support Analytics** | Answer natural-language questions about support tickets | Cortex Analyst + Semantic View |
| **Cortex Agent** | Automatically combine policy retrieval and structured analytics | Cortex Agent |

The application also provides:

- User login and sign-up
- Role-based access
- Admin user management
- Document upload and management
- Provider selection for Policy RAG
- Supporting evidence for RAG responses
- Generated SQL visibility for Support Analytics
- Agent tool activity visibility
- Centralized terminal/file logging

---

# 2. High-Level Architecture

```text
                              USER
                                |
                                v
                     +----------------------+
                     |     Streamlit UI     |
                     |                      |
                     | Login / Sign Up      |
                     | Mode Selection       |
                     | Questions            |
                     | Documents            |
                     +----------+-----------+
                                |
                                v
                     +----------------------+
                     | RetailAssistController|
                     +----------+-----------+
                                |
             +------------------+------------------+
             |                  |                  |
             v                  v                  v
        Policy RAG       Support Analytics    Cortex Agent
             |                  |                  |
             v                  v                  v
      RAGService          Cortex Analyst      Cortex Agent
             |                  |                  |
             v                  v                  |
      Cortex Search       Semantic View         |
             |                  |                  |
             v                  v                  |
      Evidence Filter    Generated SQL           |
             |                  |                  |
             v                  v                  |
      +------+------+        SQL Results          |
      |             |                              |
      v             v                              |
 Snowflake       Groq                            |
 Cortex        Generation                        |
      |             |                              |
      +------+------+                              |
             |                                     |
             v                                     v
       Policy Answer                         Agent Answer
```

### Agent orchestration

```text
                         Cortex Agent
                              |
                 +------------+------------+
                 |                         |
                 v                         v
           PolicySearch             SupportAnalytics
                 |                         |
                 v                         v
        Cortex Search              Cortex Analyst
                 |                         |
                 v                         v
        Policy Evidence              SQL Results
                 |                         |
                 +------------+------------+
                              |
                              v
                       Combined Answer
```

---

# 3. Technology Stack

| Layer | Technology |
|---|---|
| Language | Python |
| UI | Streamlit |
| Data Platform | Snowflake |
| Policy Retrieval | Snowflake Cortex Search |
| Policy Generation | Snowflake Cortex / Groq |
| Structured Analytics | Snowflake Cortex Analyst |
| Semantic Layer | Snowflake Semantic View |
| Orchestration | Snowflake Cortex Agent |
| Document Processing | PDF / DOCX / TXT / Markdown |
| Authentication | Snowflake-backed user table + PBKDF2 password hashing |
| Configuration | `.env` + `config/settings.py` |
| Logging | Python logging + `logs/supportai.log` |
| Testing | Pytest |
| Data | CSV / Snowflake tables |

---

# 4. Application Modes

## 4.1 Policy RAG

Policy RAG answers questions using approved policy and FAQ content.

Flow:

```text
User Question
     |
     v
Cortex Search
     |
     v
Candidate Documents / Chunks
     |
     v
RAG Evidence Selection
     |
     v
Relevant Evidence
     |
     v
Grounded Prompt
     |
     +----------------------+
     |                      |
     v                      v
Snowflake Cortex          Groq
     |                      |
     +----------+-----------+
                |
                v
          Final Answer
```

### Important behavior

The top Search result is **not automatically treated as valid evidence**.

The RAG service evaluates retrieved candidates using signals such as:

- lexical overlap
- FAQ/question relevance
- section-heading relevance
- category relevance
- question intent
- candidate intent
- distinctive terms
- policy variant compatibility
- retrieval score

This helps avoid incorrect matches such as using standard-shipping information for an express-shipping question.

### Grounded refusal

If suitable evidence cannot be found, the application uses a controlled refusal rather than inventing company policy:

```text
I couldn't find a policy that answers that question.
```

---

# 5. Generation Providers

Policy RAG supports two generation paths.

## Snowflake Cortex

Snowflake Cortex provides the native Snowflake generation path.

## Groq

Groq is accessed through its OpenAI-compatible API.

The provider implementation is located at:

```text
providers/openai/generator.py
```

The directory name is retained for compatibility with the existing provider factory and contracts.

The configured Groq model is controlled through:

```text
GROQ_MODEL
```

The current project configuration uses:

```text
openai/gpt-oss-20b
```

### Provider selection

The **AI Provider** selector applies to **Policy RAG** only.

```text
Policy RAG
   |
   +-- Snowflake Cortex
   |
   +-- Groq
```

Support Analytics and Cortex Agent use Snowflake-native services and do not use the Policy RAG provider selector.

---

# 6. Support Analytics

Support Analytics provides natural-language analysis over support-ticket data.

## Dataset

The project contains a synthetic dataset of **500 support tickets**.

Categories include:

```text
Refunds
Shipping
Returns
Payments
Warranty
```

The dataset contains operational fields including:

```text
TICKET_ID
CREATED_DATE
CATEGORY
STATUS
PRIORITY
RESOLUTION_TIME_HOURS
CUSTOMER_RATING
CHANNEL
REGION
PRODUCT_FAMILY
REFUND_AMOUNT
IS_ESCALATED
```

## Semantic View

The semantic layer is:

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
resolution_time_hours
customer_rating
refund_amount
is_escalated
```

Core metrics include:

```text
ticket_count
open_ticket_count
closed_ticket_count
average_resolution_hours
average_customer_rating
escalation_rate
total_refund_amount
```

## User experience

A Support Analytics request provides:

1. Natural-language answer
2. Generated SQL in the **Generated SQL** section
3. Request ID where available

This makes the analytics result easier to inspect and validate.

---

# 7. Cortex Agent

Cortex Agent is the Snowflake-native orchestration mode.

The configured Agent is:

```text
RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_AGENT
```

It has two application tools.

## PolicySearch

```text
Purpose:
Retrieve approved policy and FAQ evidence.

Backend:
RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH
```

## SupportAnalytics

```text
Purpose:
Answer structured support-ticket questions.

Backend:
RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_ANALYTICS
```

## Automatic routing

### Policy-only

Example:

```text
Can I return a damaged product after 20 days?
```

Expected tool:

```text
PolicySearch
```

### Analytics-only

Example:

```text
How many support tickets are currently open?
```

Expected tool:

```text
SupportAnalytics
```

### Combined

Example:

```text
What does the refund policy promise, and how long are refund
tickets actually taking?
```

Expected tools:

```text
PolicySearch
+
SupportAnalytics
```

The Agent is instructed to return only the customer-facing answer and not expose internal routing, SQL, orchestration, or implementation details.

---

# 8. Authentication and Authorization

SupportAI uses a Snowflake-backed account table:

```text
RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_USERS
```

The table stores:

```text
USER_ID
NAME
EMAIL
PASSWORD_HASH
ROLE
STATUS
CREATED_AT
LAST_LOGIN
```

## Roles

```text
USER
ADMIN
```

Normal sign-up creates a:

```text
USER
```

Admin accounts are provisioned separately.

## Account status

```text
ACTIVE
INACTIVE
```

Inactive users cannot log in.

## Password security

Passwords are never stored as plain text.

The authentication service uses:

```text
PBKDF2-HMAC-SHA256
```

with a per-password salt and a high iteration count.

Passwords and password hashes are not written to application logs.

## User flow

```text
Login / Sign Up
       |
       +--------------------+
       |                    |
       v                    v
     USER                 ADMIN
       |                    |
       v                    v
User Dashboard       Admin Dashboard
       |                    |
       +-- Policy RAG       +-- User Management
       +-- Analytics        +-- Activate User
       +-- Cortex Agent     +-- Deactivate User
                            +-- Overview
```

---

# 9. Document Management

SupportAI supports knowledge-document processing for:

```text
Markdown  .md
PDF       .pdf
DOCX      .docx
TXT       .txt
```

## Upload lifecycle

```text
Select File
    |
    v
Validate File
    |
    v
Create Metadata
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
INDEXED / Ready
```

Typical processing states include:

```text
UPLOADING
PARSING
INDEXING
INDEXED
FAILED
DELETED
```

Only active, indexed documents should participate in normal retrieval.

## Newly uploaded documents

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
Normal Active Knowledge Base
```

The document remains active after the temporary preference is consumed.

---

# 10. Snowflake Objects

The application uses the following major Snowflake objects.

## Database

```text
RETAIL_ASSIST_DB
```

## Schema

```text
RETAIL_ASSIST
```

## Support Tickets

```text
RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_TICKETS
```

## User Accounts

```text
RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_USERS
```

## Semantic View

```text
RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_ANALYTICS
```

## Cortex Search

```text
RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH
```

## Cortex Agent

```text
RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_AGENT
```

---

# 11. SQL Setup

SQL scripts are located in:

```text
sql/
```

Important setup files include:

```text
setup.sql
documents.sql
chunking.sql
search.sql
load_support_tickets.sql
semantic_view.sql
agent.sql
auth.sql
password_reset.sql
validation.sql
05_validation.sql
```

A typical setup sequence is:

```text
1. Foundation / database setup
2. Document objects
3. Chunking objects
4. Cortex Search
5. Support ticket table + data
6. SUPPORT_ANALYTICS Semantic View
7. SUPPORT_USERS authentication table
8. Cortex Agent
9. Validation
```

Use the SQL files supplied with the project and execute them in the intended Snowflake environment.

---

# 12. Configuration

Configuration is centralized through:

```text
config/settings.py
```

Create a local environment file from the example:

### Windows PowerShell

```powershell
Copy-Item .env.example .env
```

Then populate the local `.env` with the appropriate credentials.

Example:

```env
SNOWFLAKE_ACCOUNT=<your-account>
SNOWFLAKE_USER=<your-user>
SNOWFLAKE_PAT=<your-programmatic-access-token>
SNOWFLAKE_WAREHOUSE=<your-warehouse>
SNOWFLAKE_DATABASE=RETAIL_ASSIST_DB
SNOWFLAKE_SCHEMA=RETAIL_ASSIST
SNOWFLAKE_ROLE=<your-role>

GROQ_API_KEY=<your-groq-api-key>
GROQ_MODEL=openai/gpt-oss-20b
GROQ_MAX_TOKENS=256
GROQ_TIMEOUT=30

DEFAULT_AI_PROVIDER=snowflake
RETAIL_ASSIST_MODE=SNOWFLAKE
RETAIL_ASSIST_LOG_LEVEL=INFO
```

## Security

Never commit:

```text
.env
Snowflake PATs
Groq API keys
Passwords
Access tokens
Production secrets
```

The repository should contain only safe placeholders in:

```text
.env.example
```

---

# 13. Installation

## Prerequisites

Install:

- Python 3.x
- Snowflake account/access
- Git
- Required Python packages

## Create virtual environment

### Windows

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

---

# 14. Run SupportAI

From the project root:

```powershell
$env:PYTHONPATH = "."
streamlit run app/main.py
```

The application opens in the Streamlit browser interface.

The production-style application provides:

- Authentication
- User dashboard
- Admin dashboard
- Policy RAG
- Support Analytics
- Cortex Agent
- Policy RAG provider selection
- Supporting evidence
- Generated SQL
- Agent tool activity
- Document upload and management

---

# 15. Demo Mode

For UI development without a live Snowflake dependency:

```powershell
$env:PYTHONPATH = "."
streamlit run app/demo_main.py
```

Demo mode is intended for UI/component development and does not replace validation against the live Snowflake services.

---

# 16. Logging and Observability

SupportAI uses centralized Python logging.

Logs are written to:

```text
logs/supportai.log
```

and to the terminal.

Logging is configured through:

```text
config/logging_config.py
```

The log level can be controlled with:

```env
RETAIL_ASSIST_LOG_LEVEL=INFO
```

## Logged operational events

The backend logs operational events across:

```text
Authentication
Admin operations
Application startup
Controller
Policy RAG
Retriever
Generators
Support Analytics
Cortex Analyst
Cortex Agent
Document processing
```

Examples include:

```text
request started
request completed
request rejected
request failed
duration
result counts
evidence counts
tool usage
provider selection
document processing stages
slow operations
```

The logging design intentionally avoids writing sensitive values such as:

```text
passwords
password hashes
API keys
PATs
raw credentials
full user prompts
raw document contents
```

---

# 17. Testing

Run the automated test suite from the project root:

```powershell
$env:PYTHONPATH = "."
python -m pytest -q
```

The project includes tests covering areas such as:

- authentication
- admin service
- contracts
- provider behavior
- RAG behavior

The validated project test run used during development completed with:

```text
27 passed
0 failed
```

---

# 18. Policy RAG Evaluation

The project retains the original **20-question** Policy RAG evaluation.

Evaluation script:

```powershell
python scripts/evaluate_providers.py
```

Output:

```text
data/provider_evaluation_results.csv
```

The evaluation compares the configured Policy RAG generation providers using the same retrieved/evidence-selected context.

Current evaluation snapshot:

| Metric | Result |
|---|---:|
| Questions evaluated | 20/20 |
| Successful Snowflake responses | 20/20 |
| Successful Groq responses | 20/20 |
| Unsupported questions handled | 2/2 |
| Answerable questions | 18 |
| Exact expected-source matches | 13/18 |
| Exact expected-source rate | 72.2% |

The source-match metric evaluates retrieval/evidence alignment. The supplied evaluation dataset does not provide reference answers, so an answer-level correctness percentage should not be inferred from this metric.

---

# 19. Structured Analytics Evaluation

The project contains an analytics evaluation set with at least 10 structured questions.

Examples include:

```text
How many support tickets are currently open?

Which category has the highest average resolution time?

What is the escalation rate by category?

How many high-priority tickets were created in the previous month?

Which region has the most open tickets?

What is the monthly refund amount?
```

The structured questions are validated against expected values/direct SQL validation.

Development validation recorded:

```text
10/10 passed
```

---

# 20. Cortex Agent Evaluation

The Agent evaluation contains:

```text
4 policy-only questions
4 analytics-only questions
4 combined questions
```

Total:

```text
12 questions
```

The evaluation checks:

- PolicySearch routing
- SupportAnalytics routing
- Combined tool usage
- Policy grounding
- Analytics results
- Cross-source behavior

---

# 21. Sample Demonstration Questions

## Policy RAG

```text
What is the return window for an unused product?
```

```text
What refund processing time does the policy promise?
```

```text
What payment methods does the company accept?
```

## Support Analytics

```text
How many support tickets are currently open?
```

```text
Which category has the highest average resolution time?
```

```text
What is the escalation rate for each support ticket category?
```

## Cortex Agent

```text
What does the refund policy promise, and how long are refund tickets actually taking?
```

```text
Which policy applies to late shipments and how many Shipping tickets were escalated last month?
```

---

# 22. Recommended Final Demo Question

The strongest integrated demonstration question is:

```text
Customers are complaining about refunds. What does our refund policy say,
how many refund tickets did we receive last month, and how does their
average resolution time compare with other categories?
```

This demonstrates:

```text
PolicySearch
     +
SupportAnalytics
     +
Combined Cortex Agent orchestration
```

The expected behavior is for the Agent to use both configured tools and return a concise customer-facing response.

---

# 23. Project Structure

```text
RetailAssist FAQ Assistant v2/
│
├── app/
│   ├── main.py
│   ├── ui.py
│   ├── controller.py
│   ├── demo_main.py
│   ├── demo_providers.py
│   └── __init__.py
│
├── config/
│   ├── settings.py
│   ├── logging_config.py
│   └── __init__.py
│
├── data/
│   ├── support_tickets.csv
│   ├── generate_support_tickets.py
│   ├── evaluation_questions.csv
│   ├── provider_evaluation_results.csv
│   ├── provider_evaluation_summary.csv
│   ├── payments_faq.md
│   ├── refunds_faq.md
│   ├── returns_faq.md
│   ├── shipping_faq.md
│   └── warranty_faq.md
│
├── evaluation/
│   └── analyst_questions.csv
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
├── services/
│   ├── auth_service.py
│   ├── admin_service.py
│   ├── cortex_analyst.py
│   ├── cortex_agent.py
│   └── activity_service.py
│
├── scripts/
│   ├── evaluate_providers.py
│   ├── evaluation_summary.py
│   └── generate_seed.py
│
├── sql/
│   ├── setup.sql
│   ├── documents.sql
│   ├── chunking.sql
│   ├── search.sql
│   ├── load_support_tickets.sql
│   ├── semantic_view.sql
│   ├── agent.sql
│   ├── auth.sql
│   ├── password_reset.sql
│   └── validation.sql
│
├── tests/
│   ├── create_admin.py
│   ├── test_admin_service.py
│   ├── test_auth_manual.py
│   └── test_contracts.py
│
├── logs/
│   └── supportai.log
│
├── .env.example
├── .gitignore
├── requirements.txt
├── LICENSE
└── README.md
```

---

# 24. Troubleshooting

## Snowflake connection failure

Check:

```text
SNOWFLAKE_ACCOUNT
SNOWFLAKE_USER
SNOWFLAKE_PAT
SNOWFLAKE_WAREHOUSE
SNOWFLAKE_DATABASE
SNOWFLAKE_SCHEMA
SNOWFLAKE_ROLE
```

Also verify that the configured Snowflake objects exist.

## Groq configuration failure

Check:

```text
GROQ_API_KEY
GROQ_MODEL
GROQ_MAX_TOKENS
GROQ_TIMEOUT
```

## Policy question returns a refusal

Check:

1. Document status
2. Document active state
3. Cortex Search availability
4. Retrieved candidates
5. Evidence filtering
6. Generator configuration

A retrieval problem should not automatically be solved by changing the generation prompt.

## Analytics question fails

Check:

```text
SUPPORT_TICKETS
SUPPORT_ANALYTICS
Cortex Analyst access
Snowflake warehouse
```

Also inspect:

```text
logs/supportai.log
```

## Agent question fails

Check:

```text
RETAIL_ASSIST_AGENT
RETAIL_ASSIST_SEARCH
SUPPORT_ANALYTICS
```

and inspect the Agent request logs.

## Uploaded document is not retrieved

Verify that the document reached:

```text
PROCESSING_STATUS = INDEXED
ACTIVE = TRUE
```

Then check the document chunks and Cortex Search refresh.

---

# 25. Security and Source-Control Checklist

Before committing:

```powershell
git status
git ls-files .env
```

`.env` should not be tracked.

Also verify that logs or generated local files containing sensitive information are excluded where appropriate.

Never commit:

```text
.env
API keys
PATs
Passwords
Tokens
Private credentials
```

Use:

```text
.env.example
```

for safe configuration placeholders.

---

# 26. Engineering Design Principles

## Grounded generation

Policy answers should be based on retrieved evidence.

## Evidence before generation

The system selects valid evidence before passing context to the generator.

## Conservative retrieval

Weak or clearly unrelated evidence should be rejected rather than blindly passed to the LLM.

## Intent awareness

Support questions can have different intents:

```text
Duration
Tracking
Refund
Cost
Procedure
Eligibility
Damage
```

## Variant protection

Different policy variants should not be mixed incorrectly.

```text
Standard Delivery
Express Delivery
International Delivery
Overnight Delivery
```

## Separation of responsibilities

The architecture keeps responsibilities separate:

```text
UI
 ↓
Controller
 ↓
Service
 ↓
Retriever / Analyst / Agent
 ↓
Snowflake
 ↓
Generator / Results
```

This allows individual components to be tested and evolved independently.

---

# 27. Definition of Done

| Requirement | Status |
|---|---|
| Existing Policy RAG preserved | ✅ |
| Snowflake Cortex generation | ✅ |
| Groq generation | ✅ |
| Runtime Policy RAG provider selection | ✅ |
| Shared Cortex Search retrieval | ✅ |
| Evidence filtering and grounding | ✅ |
| 500 synthetic support tickets | ✅ |
| Support Analytics | ✅ |
| `SUPPORT_ANALYTICS` Semantic View | ✅ |
| Generated SQL visibility | ✅ |
| 10 structured analytics questions | ✅ |
| Cortex Agent | ✅ |
| PolicySearch tool | ✅ |
| SupportAnalytics tool | ✅ |
| Policy-only routing | ✅ |
| Analytics-only routing | ✅ |
| Combined routing | ✅ |
| 12-question Agent evaluation | ✅ |
| User authentication | ✅ |
| Admin role and dashboard | ✅ |
| User activation/deactivation | ✅ |
| Document upload lifecycle | ✅ |
| PDF / DOCX / TXT / Markdown support | ✅ |
| Centralized operational logging | ✅ |
| Automated tests | ✅ |

---

# 28. Known Limitations

- Policy RAG evaluation includes expected sources but does not contain reference answers for every question, so source-match results should not be interpreted as complete answer-correctness scores.
- Difficult or ambiguous retrieval cases can still be improved through future retrieval/evidence tuning.
- Provider latency depends on model, network, and provider availability.
- Snowflake Search and other Snowflake services can introduce variable runtime latency.
- External provider rate limits can affect Groq generation.
- The Groq adapter retains the existing `providers/openai/` path for compatibility.
- The temporary new-upload retrieval preference applies only to the subsequent question before normal active-document retrieval resumes.
- Demo mode is intended for UI development and does not replace live Snowflake validation.

---

# 29. Future Enhancements

Potential future improvements include:

- More advanced retrieval evaluation
- Better conversational context handling
- Document versioning
- Automated re-indexing
- Expanded analytics metrics
- More Agent evaluation cases
- Response feedback collection
- Production monitoring
- Multi-language support
- Deployment automation
- Additional support-data sources
- More granular authorization

---

# 30. Conclusion

SupportAI demonstrates an end-to-end enterprise-style customer-support architecture that combines unstructured policy knowledge with structured operational data.

The platform separates the major responsibilities:

```text
Cortex Search
    ↓
Policy Retrieval

RAGService
    ↓
Evidence Selection + Grounding

Snowflake Cortex / Groq
    ↓
Policy Generation

Cortex Analyst
    ↓
Structured Analytics

Cortex Agent
    ↓
Policy + Analytics Orchestration

Streamlit
    ↓
User Experience

Snowflake
    ↓
Persistent Data + AI Services
```

This design allows SupportAI to answer simple policy questions, analyze support operations, and combine both sources in a single Agent-driven workflow while maintaining controlled grounding and operational visibility.

---

## Author

**Khader Gouse**

**Project:** RetailAssist FAQ Assistant v2 / SupportAI
