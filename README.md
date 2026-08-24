# RetailAssist FAQ Assistant

An industry-style Retrieval-Augmented Generation (RAG) customer-support assistant built with **Snowflake Cortex Search**, **Snowflake Cortex Complete**, **Snowpark**, and **Streamlit**.

RetailAssist answers customer FAQ questions from a controlled retail-policy knowledge base. The application retrieves relevant policy chunks from Snowflake Cortex Search and passes only the accepted evidence to a Cortex language model for grounded response generation.

---

## 1. Project Overview

RetailAssist is designed to demonstrate a production-oriented RAG workflow:

```text
Customer Question
       |
       v
Streamlit UI
       |
       v
RAG Service
       |
       +--------------------+
       |                    |
       v                    v
Cortex Search         Grounded Prompt
       |                    |
       v                    v
Policy Chunks ------> Cortex Complete
       |                    |
       +---------+----------+
                 |
                 v
        Answer + Sources
```

The system is intentionally designed so that the generated answer is based on retrieved policy evidence rather than general model knowledge.

---

## 2. Key Features

- Snowflake Cortex Search for semantic policy retrieval
- Snowflake Cortex Complete for grounded answer generation
- Chunk-based policy knowledge base
- Category-aware retrieval for:
  - Returns
  - Refunds
  - Shipping
  - Warranty
  - Payments
- Evidence filtering before generation
- Unsupported-question refusal behavior
- Source/chunk visibility in the Streamlit UI
- Provider-neutral RAG architecture
- Separate Demo and Snowflake provider modes
- Environment-variable based Snowflake configuration
- No credentials hard-coded in application source code
- Temperature set to `0` for deterministic generation behavior
- Concise customer-support responses

---

## 3. Technology Stack

| Layer | Technology |
|---|---|
| UI | Streamlit |
| Language | Python 3.11+ |
| RAG | Custom Python RAG service |
| Retrieval | Snowflake Cortex Search |
| Generation | Snowflake Cortex Complete |
| Database | Snowflake |
| Snowflake API | Snowpark Python |
| Configuration | `.env` |
| Architecture | Provider-neutral RAG |

---

## 4. Project Structure

```text
RetailAssist FAQ Assistant/
│
├── app/
│   ├── main.py
│   ├── demo_main.py
│   ├── controller.py
│   ├── ui.py
│   └── demo_providers.py
│
├── rag/
│   ├── contracts.py
│   ├── prompts.py
│   └── service.py
│
├── providers/
│   └── snowflake/
│       ├── connection.py
│       ├── retriever.py
│       └── generator.py
│
├── sql/
│   └── search.sql
│
├── data/
│   └── policy files / source documents
│
├── .env
├── .gitignore
└── README.md
```

File names may vary slightly depending on the final project directory.

---

## 5. RAG Architecture

### Step 1 — User Question

The customer enters a question in the Streamlit interface.

Example:

```text
My device was dropped and the screen is cracked. Is that covered?
```

### Step 2 — Category Detection

The Snowflake retriever identifies an obvious policy category from the question.

Supported categories include:

```text
returns
refunds
shipping
warranty
payments
```

### Step 3 — Cortex Search

The question is sent to the configured Cortex Search Service.

The service returns candidate chunks containing:

- Chunk ID
- Document ID
- Document name
- Category
- Chunk index
- Chunk text
- Relevance score

### Step 4 — Evidence Filtering

The RAG service checks the retrieved chunks for meaningful overlap with the actual subject of the question.

Irrelevant chunks are discarded.

This prevents a response about one policy area from being grounded using unrelated FAQ content.

### Step 5 — Grounded Prompt

Only accepted evidence is placed into the generation prompt.

The model receives:

```text
POLICY EVIDENCE
+
CUSTOMER QUESTION
+
STRICT GROUNDING INSTRUCTIONS
```

### Step 6 — Cortex Generation

Snowflake Cortex Complete generates the customer-facing answer.

The generation configuration uses:

```text
temperature = 0
max_tokens = 250
```

### Step 7 — Answer and Sources

The UI displays:

- Final answer
- Retrieved source documents
- Categories
- Chunk IDs
- Chunk indexes
- Evidence text

---

## 6. Snowflake Objects

The project uses the following Snowflake environment:

```text
DATABASE:
RETAIL_ASSIST_DB

SCHEMA:
RETAIL_ASSIST

WAREHOUSE:
<configured warehouse>

CORTEX SEARCH SERVICE:
RETAIL_ASSIST_SEARCH

POLICY TABLE:
POLICY_CHUNKS
```

The Cortex Search Service indexes the policy chunk text:

```sql
ON CHUNK_TEXT
```

and exposes metadata attributes such as:

```text
DOCUMENT_ID
DOCUMENT_NAME
CATEGORY
CHUNK_INDEX
```

---

## 7. Cortex Search Service

The search service follows this structure:

```sql
CREATE OR REPLACE CORTEX SEARCH SERVICE RETAIL_ASSIST_SEARCH
    ON CHUNK_TEXT
    ATTRIBUTES
        DOCUMENT_ID,
        DOCUMENT_NAME,
        CATEGORY,
        CHUNK_INDEX
    WAREHOUSE = COMPUTE_WH
    TARGET_LAG = '1 hour'
    AS
    SELECT
        CHUNK_ID,
        DOCUMENT_ID,
        DOCUMENT_NAME,
        CATEGORY,
        CHUNK_INDEX,
        CHUNK_TEXT
    FROM POLICY_CHUNKS;
```

The exact warehouse should match the warehouse configured for the project.

---

## 8. Environment Configuration

Create a `.env` file in the project root.

Example:

```env
SNOWFLAKE_ACCOUNT=<your_account>
SNOWFLAKE_USER=<your_user>
SNOWFLAKE_PASSWORD=<your_password>
SNOWFLAKE_WAREHOUSE=<your_warehouse>
SNOWFLAKE_DATABASE=RETAIL_ASSIST_DB
SNOWFLAKE_SCHEMA=RETAIL_ASSIST
SNOWFLAKE_ROLE=<your_role>

SNOWFLAKE_CORTEX_MODEL=<verified_cortex_model>

RETAIL_ASSIST_MODE=SNOWFLAKE
```

### Important Security Rule

Never commit `.env` to Git.

Add this to `.gitignore`:

```gitignore
.env
.venv/
__pycache__/
*.pyc
.streamlit/secrets.toml
```

Never place passwords, tokens, private keys, or other credentials directly inside Python source files.

---

## 9. Installation

Open PowerShell in the project directory.

Create a virtual environment:

```powershell
python -m venv .venv
```

Activate it:

```powershell
.venv\Scripts\Activate.ps1
```

Install the required packages:

```powershell
pip install streamlit python-dotenv snowflake-snowpark-python snowflake-ml-python
```

If the project already contains a `requirements.txt`, use:

```powershell
pip install -r requirements.txt
```

---

## 10. Run the Application

Make sure the environment is configured for Snowflake:

```powershell
$env:RETAIL_ASSIST_MODE="SNOWFLAKE"
```

Set Python path:

```powershell
$env:PYTHONPATH="."
```

Run Streamlit:

```powershell
streamlit run app/main.py
```

Streamlit will display a local URL in the terminal.

Open the displayed URL in the browser.

---

## 11. Demo Mode

For offline/local demonstration, the project can use the Demo provider.

Set:

```powershell
$env:RETAIL_ASSIST_MODE="DEMO"
```

Then run:

```powershell
streamlit run app/demo_main.py
```

Demo mode does not use Snowflake retrieval or Cortex generation.

For the final Snowflake-integrated demonstration, use:

```powershell
streamlit run app/main.py
```

with:

```text
RETAIL_ASSIST_MODE=SNOWFLAKE
```

---

## 12. Testing

The Cortex Search service can be tested directly using the queries in:

```text
sql/search.sql
```

Recommended test categories:

### Warranty

```text
My device was dropped and the screen is cracked. Is that covered?
```

### Returns

```text
The product arrived broken. Can I send it back?
```

### Shipping

```text
How long will normal delivery take?
```

### Refunds

```text
My refund has been pending for several business days. What should I do?
```

### Payments

```text
Why do I see a pending card charge even though my checkout failed?
```

### Unsupported

Ask a question outside the supplied retail policy knowledge base.

Expected behavior:

```text
I couldn't find a policy that answers that question.
```

---

## 13. Grounding Strategy

RetailAssist uses multiple safeguards to reduce unsupported answers.

### Policy-only prompting

The generation prompt explicitly states that the supplied policy evidence is the only source of truth.

### Retrieval filtering

Retrieved chunks are evaluated before being passed to the generator.

### Category filtering

When a question clearly belongs to one policy category, the search request can restrict retrieval to that category.

### Evidence limit

The RAG service keeps only a small number of meaningful evidence chunks.

### Refusal behavior

If relevant evidence cannot be established, the application returns:

```text
I couldn't find a policy that answers that question.
```

This is preferable to generating an unsupported answer.

---

## 14. Source and Chunk Handling

Policy documents are divided into chunks before being indexed.

Each chunk retains metadata:

```text
CHUNK_ID
DOCUMENT_ID
DOCUMENT_NAME
CATEGORY
CHUNK_INDEX
CHUNK_TEXT
```

This allows the UI to show exactly which policy evidence contributed to the response.

Multiple relevant chunks can be returned when the question requires information from more than one section of the policy.

The system does not assume that every retrieved chunk is relevant. Retrieval candidates are filtered before generation.

---

## 15. Provider-Neutral Design

The RAG layer does not directly depend on Snowflake.

The application uses provider contracts:

```python
Retriever
Generator
```

The Snowflake implementation provides:

```text
SnowflakeRetriever
SnowflakeGenerator
```

The Demo implementation provides:

```text
DemoRetriever
DemoGenerator
```

This separation makes the application easier to test, maintain, and extend.

---

## 16. Security Considerations

The project follows these practices:

- Credentials are stored in environment variables.
- `.env` is excluded from version control.
- Snowflake access is controlled through a Snowflake role.
- The application does not expose Snowflake credentials in the UI.
- The generator is explicitly instructed not to use outside knowledge.
- Unsupported questions are refused.
- Retrieved evidence is displayed for transparency.

For production deployment, credentials should preferably be supplied through a managed secret-management mechanism rather than a local `.env` file.

---

## 17. Error Handling

The application validates:

- Missing Snowflake configuration
- Empty questions
- Missing Cortex model configuration
- Empty Cortex responses
- Empty retrieved chunks
- Unsupported questions

Typical configuration error:

```text
SNOWFLAKE_CORTEX_MODEL must be configured with a verified Cortex model.
```

This means the model environment variable has not been configured with a valid model available to the Snowflake account.

---

## 18. Expected User Experience

A typical supported interaction should look like:

```text
Customer:
What is the standard return window?

RetailAssist:
According to the policy, most unused physical products can be
returned within 30 calendar days of delivery.
```

The interface should also expose the supporting policy source/chunk information.

For an unsupported question:

```text
Customer:
What is the weather today?

RetailAssist:
I couldn't find a policy that answers that question.
```

---

## 19. Production-Level Improvements

Potential next-stage improvements include:

- Authentication and authorization
- Centralized secrets management
- Automated evaluation datasets
- Retrieval precision/recall monitoring
- Observability and structured logging
- User feedback collection
- Conversation history
- Citation links to original policy documents
- Automated document ingestion
- CI/CD pipeline
- Unit and integration test suite
- Role-based Snowflake access
- Production deployment behind a secure application gateway

---

## 20. Project Outcome

RetailAssist demonstrates an end-to-end enterprise-style RAG workflow using Snowflake:

```text
Policy Documents
      ↓
Policy Chunking
      ↓
Snowflake Policy Table
      ↓
Cortex Search
      ↓
Relevant Evidence
      ↓
Evidence Filtering
      ↓
Grounded Cortex Generation
      ↓
Customer Answer + Sources
```

The key design principle is:

> **Retrieve first, ground the answer in policy evidence, and refuse when sufficient evidence is unavailable.**

This makes RetailAssist suitable as a practical demonstration of retrieval-augmented customer-support automation with Snowflake Cortex.
