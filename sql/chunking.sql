-- ============================================================
-- RetailAssist FAQ Assistant
-- Phase 3: Recursive Markdown-Aware Chunking
-- ============================================================

USE DATABASE RETAIL_ASSIST_DB;
USE SCHEMA RETAIL_ASSIST;


-- ------------------------------------------------------------
-- 1. Create the chunk table
-- ------------------------------------------------------------

CREATE OR REPLACE TABLE POLICY_CHUNKS (
    CHUNK_ID        VARCHAR(100) NOT NULL,
    DOCUMENT_ID     VARCHAR(50) NOT NULL,
    DOCUMENT_NAME   VARCHAR(255) NOT NULL,
    CATEGORY        VARCHAR(100) NOT NULL,
    CHUNK_INDEX     INTEGER NOT NULL,
    CHUNK_TEXT      TEXT NOT NULL,
    CHUNK_LENGTH    INTEGER NOT NULL,
    CREATED_AT      TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT PK_POLICY_CHUNKS
        PRIMARY KEY (CHUNK_ID)
);


-- ------------------------------------------------------------
-- 2. Split each complete Markdown document recursively
-- ------------------------------------------------------------

INSERT INTO POLICY_CHUNKS (
    CHUNK_ID,
    DOCUMENT_ID,
    DOCUMENT_NAME,
    CATEGORY,
    CHUNK_INDEX,
    CHUNK_TEXT,
    CHUNK_LENGTH
)
SELECT
    DOCUMENT_ID || '_CHUNK_' || LPAD(
        ROW_NUMBER() OVER (
            PARTITION BY DOCUMENT_ID
            ORDER BY INDEX
        )::VARCHAR,
        4,
        '0'
    ) AS CHUNK_ID,

    DOCUMENT_ID,

    DOCUMENT_NAME,

    CATEGORY,

    ROW_NUMBER() OVER (
        PARTITION BY DOCUMENT_ID
        ORDER BY INDEX
    ) - 1 AS CHUNK_INDEX,

    VALUE::VARCHAR AS CHUNK_TEXT,

    LENGTH(VALUE::VARCHAR) AS CHUNK_LENGTH

FROM POLICY_SOURCES,

LATERAL FLATTEN(
    INPUT => SNOWFLAKE.CORTEX.SPLIT_TEXT_RECURSIVE_CHARACTER(
        CONTENT,
        'markdown',
        1200,
        200
    )
);


-- ------------------------------------------------------------
-- 3. Inspect generated chunks
-- ------------------------------------------------------------

SELECT
    DOCUMENT_ID,
    DOCUMENT_NAME,
    CATEGORY,
    CHUNK_INDEX,
    CHUNK_LENGTH,
    CHUNK_TEXT
FROM POLICY_CHUNKS
ORDER BY
    DOCUMENT_ID,
    CHUNK_INDEX;