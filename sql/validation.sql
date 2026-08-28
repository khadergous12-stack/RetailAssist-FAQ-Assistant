-- ============================================================
-- RetailAssist FAQ Assistant
-- Phase 5: Snowflake Validation
-- ============================================================

USE DATABASE RETAIL_ASSIST_DB;
USE SCHEMA RETAIL_ASSIST;

-- ============================================================
-- 1. Validate source documents
-- ============================================================

SELECT
    COUNT(*) AS TOTAL_DOCUMENTS
FROM POLICY_SOURCES;


-- ============================================================
-- 2. Validate expected FAQ categories
-- ============================================================

SELECT
    CATEGORY,
    COUNT(*) AS DOCUMENT_COUNT
FROM POLICY_SOURCES
GROUP BY CATEGORY
ORDER BY CATEGORY;


-- ============================================================
-- 3. Validate source document completeness
-- ============================================================

SELECT
    DOCUMENT_ID,
    DOCUMENT_NAME,
    CATEGORY,
    LENGTH(CONTENT) AS CONTENT_LENGTH
FROM POLICY_SOURCES
ORDER BY DOCUMENT_ID;


-- ============================================================
-- 4. Validate generated chunks
-- ============================================================

SELECT
    COUNT(*) AS TOTAL_CHUNKS
FROM POLICY_CHUNKS;


-- ============================================================
-- 5. Validate chunks by category
-- ============================================================

SELECT
    CATEGORY,
    COUNT(*) AS CHUNK_COUNT
FROM POLICY_CHUNKS
GROUP BY CATEGORY
ORDER BY CATEGORY;


-- ============================================================
-- 6. Validate chunk integrity
-- ============================================================

SELECT
    COUNT(*) AS INVALID_CHUNKS
FROM POLICY_CHUNKS
WHERE CHUNK_ID IS NULL
   OR DOCUMENT_ID IS NULL
   OR DOCUMENT_NAME IS NULL
   OR CATEGORY IS NULL
   OR CHUNK_INDEX IS NULL
   OR CHUNK_TEXT IS NULL
   OR CHUNK_LENGTH <= 0;


-- ============================================================
-- 7. Inspect sample chunks
-- ============================================================

SELECT
    CHUNK_ID,
    DOCUMENT_ID,
    DOCUMENT_NAME,
    CATEGORY,
    CHUNK_INDEX,
    CHUNK_LENGTH,
    CHUNK_TEXT
FROM POLICY_CHUNKS
ORDER BY DOCUMENT_ID, CHUNK_INDEX
LIMIT 10;


-- ============================================================
-- 8. Check for duplicate chunk IDs
-- ============================================================

SELECT
    CHUNK_ID,
    COUNT(*) AS DUPLICATE_COUNT
FROM POLICY_CHUNKS
GROUP BY CHUNK_ID
HAVING COUNT(*) > 1;


-- ============================================================
-- 9. Verify Cortex Search Service exists
-- ============================================================

SHOW CORTEX SEARCH SERVICES;


-- ============================================================
-- 10. Basic Cortex Search validation
-- ============================================================

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


-- ============================================================
-- 11. Returns retrieval validation
-- ============================================================

SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        'RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH',
        '{
            "query": "The product arrived broken. Can I return it?",
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


-- ============================================================
-- 12. Shipping retrieval validation
-- ============================================================

SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        'RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH',
        '{
            "query": "How long will standard delivery take?",
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


-- ============================================================
-- 13. Refund retrieval validation
-- ============================================================

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
                "CHUNK_TEXT"
            ],
            "limit": 5
        }'
    )
)['results'] AS RESULTS;


-- ============================================================
-- 14. Payments retrieval validation
-- ============================================================

SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        'RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH',
        '{
            "query": "Why was my card declined?",
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


-- ============================================================
-- 15. Unsupported question validation
-- ============================================================

SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        'RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH',
        '{
            "query": "Do you provide helicopter delivery to Mars?",
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