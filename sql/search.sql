-- ============================================================
-- RetailAssist FAQ Assistant
-- Phase 4: Cortex Search
-- ============================================================

USE DATABASE RETAIL_ASSIST_DB;
USE SCHEMA RETAIL_ASSIST;

-- ============================================================
-- Create Cortex Search Service
-- ============================================================

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

-- ============================================================
-- Cortex Search Retrieval Tests
-- ============================================================

-- Test 1: Warranty / accidental damage
SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        'RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH',
        '{
            "query": "My device was dropped and the screen is cracked. Is that covered?",
            "columns": [
                "CHUNK_ID",
                "DOCUMENT_ID",
                "DOCUMENT_NAME",
                "CATEGORY",
                "CHUNK_INDEX",
                "CHUNK_TEXT"
            ],
            "limit": 5
        }'
    )
)['results'] AS RESULTS;

SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        'RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH',
        '{
            "query": "My device was dropped and the screen is cracked. Is that covered?",
            "columns": [
                "CHUNK_ID",
                "DOCUMENT_ID",
                "DOCUMENT_NAME",
                "CATEGORY",
                "CHUNK_INDEX",
                "CHUNK_TEXT"
            ],
            "limit": 5
        }'
    )
)['results'] AS RESULTS;

SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        'RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH',
        '{
            "query": "The product arrived broken. Can I send it back?",
            "columns": [
                "CHUNK_ID",
                "DOCUMENT_ID",
                "DOCUMENT_NAME",
                "CATEGORY",
                "CHUNK_INDEX",
                "CHUNK_TEXT"
            ],
            "limit": 5
        }'
    )
)['results'] AS RESULTS;

SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        'RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH',
        '{
            "query": "How long will normal delivery take?",
            "columns": [
                "CHUNK_ID",
                "DOCUMENT_ID",
                "DOCUMENT_NAME",
                "CATEGORY",
                "CHUNK_INDEX",
                "CHUNK_TEXT"
            ],
            "limit": 5
        }'
    )
)['results'] AS RESULTS;

SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        'RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH',
        '{
            "query": "My refund has been pending for several business days. What should I do?",
            "columns": [
                "CHUNK_ID",
                "DOCUMENT_ID",
                "DOCUMENT_NAME",
                "CATEGORY",
                "CHUNK_INDEX",
                "CHUNK_TEXT"
            ],
            "limit": 5
        }'
    )
)['results'] AS RESULTS;

SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        'RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH',
        '{
            "query": "Why do I see a pending card charge even though my checkout failed?",
            "columns": [
                "CHUNK_ID",
                "DOCUMENT_ID",
                "DOCUMENT_NAME",
                "CATEGORY",
                "CHUNK_INDEX",
                "CHUNK_TEXT"
            ],
            "limit": 5
        }'
    )
)['results'] AS RESULTS;