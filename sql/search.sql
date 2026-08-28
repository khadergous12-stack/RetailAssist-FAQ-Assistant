-- ============================================================
-- SupportAI FAQ Assistant
-- Cortex Search - authoritative active-policy retrieval
-- ============================================================

USE ROLE CAT07_LEARNER_RL;
USE WAREHOUSE CAT07_WH;
USE DATABASE RETAIL_ASSIST_DB;
USE SCHEMA RETAIL_ASSIST;

-- ============================================================
-- 1. Recreate Cortex Search Service
--
-- IMPORTANT:
-- ACTIVE is included as an attribute and the source query excludes
-- inactive chunks. This prevents deleted document chunks from being
-- indexed again after the service refreshes.
--
-- DOCUMENT_ID remains the stable identity used by the Python retriever.
-- The Python retriever also checks DOCUMENTS so that a document that is
-- still present in an older search index cannot be used after deletion.
-- ============================================================

CREATE OR REPLACE CORTEX SEARCH SERVICE RETAIL_ASSIST_SEARCH
    ON CHUNK_TEXT
    PRIMARY KEY (CHUNK_ID)
    ATTRIBUTES
        DOCUMENT_ID,
        DOCUMENT_NAME,
        CATEGORY,
        CHUNK_INDEX,
        PAGE_NUMBER,
        SECTION_HEADING,
        SOURCE_TYPE,
        ACTIVE
    WAREHOUSE = CAT07_WH
    TARGET_LAG = '1 hour'
    AS
    SELECT
        CHUNK_ID,
        DOCUMENT_ID,
        DOCUMENT_NAME,
        CATEGORY,
        CHUNK_INDEX,
        CHUNK_TEXT,
        PAGE_INDEX,
        PAGE_NUMBER,
        SECTION_HEADING,
        SOURCE_TYPE,
        ACTIVE
    FROM POLICY_CHUNKS
    WHERE COALESCE(ACTIVE, TRUE) = TRUE;

-- ============================================================
-- 2. Verify the service
-- ============================================================

SHOW CORTEX SEARCH SERVICES;

-- ============================================================
-- 3. Retrieval smoke tests
-- ============================================================

-- Test 1: Uploaded policy should win over an older FAQ when it strongly
-- answers the same question.
SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        'RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH',
        '{
            "query": "How long does standard shipping normally take after dispatch?",
            "columns": [
                "CHUNK_ID",
                "DOCUMENT_ID",
                "DOCUMENT_NAME",
                "CATEGORY",
                "CHUNK_INDEX",
                "CHUNK_TEXT",
                "PAGE_INDEX",
                "PAGE_NUMBER",
                "SECTION_HEADING",
                "SOURCE_TYPE",
                "ACTIVE"
            ],
            "limit": 10
        }'
    )
)['results'] AS RESULTS;

-- Test 2: Express delivery.
SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        'RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH',
        '{
            "query": "How long does express delivery normally take after dispatch?",
            "columns": [
                "CHUNK_ID",
                "DOCUMENT_ID",
                "DOCUMENT_NAME",
                "CATEGORY",
                "CHUNK_INDEX",
                "CHUNK_TEXT",
                "PAGE_INDEX",
                "PAGE_NUMBER",
                "SECTION_HEADING",
                "SOURCE_TYPE",
                "ACTIVE"
            ],
            "limit": 10
        }'
    )
)['results'] AS RESULTS;

-- Test 3: Delivered but missing package.
SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        'RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH',
        '{
            "query": "What should I do if my tracking says delivered but I cannot find my package?",
            "columns": [
                "CHUNK_ID",
                "DOCUMENT_ID",
                "DOCUMENT_NAME",
                "CATEGORY",
                "CHUNK_INDEX",
                "CHUNK_TEXT",
                "PAGE_INDEX",
                "PAGE_NUMBER",
                "SECTION_HEADING",
                "SOURCE_TYPE",
                "ACTIVE"
            ],
            "limit": 10
        }'
    )
)['results'] AS RESULTS;

-- Test 4: Damaged product. This should normally resolve to the returns
-- policy if the returns FAQ is the strongest matching policy.
SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        'RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH',
        '{
            "query": "What should I do if my package arrives damaged?",
            "columns": [
                "CHUNK_ID",
                "DOCUMENT_ID",
                "DOCUMENT_NAME",
                "CATEGORY",
                "CHUNK_INDEX",
                "CHUNK_TEXT",
                "PAGE_INDEX",
                "PAGE_NUMBER",
                "SECTION_HEADING",
                "SOURCE_TYPE",
                "ACTIVE"
            ],
            "limit": 10
        }'
    )
)['results'] AS RESULTS;

-- Test 5: Refunds.
SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        'RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH',
        '{
            "query": "How long does a refund take?",
            "columns": [
                "CHUNK_ID",
                "DOCUMENT_ID",
                "DOCUMENT_NAME",
                "CATEGORY",
                "CHUNK_INDEX",
                "CHUNK_TEXT",
                "PAGE_INDEX",
                "PAGE_NUMBER",
                "SECTION_HEADING",
                "SOURCE_TYPE",
                "ACTIVE"
            ],
            "limit": 10
        }'
    )
)['results'] AS RESULTS;

-- ============================================================
-- 4. Optional direct check: no inactive chunks should be searchable
-- from the source table.
-- ============================================================

SELECT
    DOCUMENT_ID,
    DOCUMENT_NAME,
    ACTIVE,
    COUNT(*) AS CHUNK_COUNT
FROM POLICY_CHUNKS
GROUP BY DOCUMENT_ID, DOCUMENT_NAME, ACTIVE
ORDER BY DOCUMENT_NAME, ACTIVE DESC;
