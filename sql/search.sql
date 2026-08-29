USE ROLE CAT07_LEARNER_RL;

USE WAREHOUSE CAT07_WH;

USE DATABASE RETAIL_ASSIST_DB;

USE SCHEMA RETAIL_ASSIST;


-- ============================================================
-- RETAILASSIST
-- Unified Cortex Search
--
-- Sources:
--   1. POLICY_CHUNKS     = built-in FAQ/policies
--   2. DOCUMENT_CHUNKS   = user-uploaded PDF/DOCX/MD/TXT
--
-- Uploaded documents are controlled by DOCUMENTS.ACTIVE and
-- DOCUMENTS.PROCESSING_STATUS.
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

    WHERE COALESCE(ACTIVE, TRUE) = TRUE


    UNION ALL


    SELECT
        dc.CHUNK_ID,
        dc.DOCUMENT_ID,
        dc.DOCUMENT_NAME,
        dc.CATEGORY,
        dc.CHUNK_INDEX,
        dc.CHUNK_TEXT,
        dc.SOURCE_PAGE_INDEX AS PAGE_INDEX,
        dc.SOURCE_PAGE_NUMBER AS PAGE_NUMBER,
        dc.SECTION AS SECTION_HEADING,
        'USER_UPLOAD' AS SOURCE_TYPE,
        TRUE AS ACTIVE

    FROM DOCUMENT_CHUNKS dc

    INNER JOIN DOCUMENTS d
        ON d.DOCUMENT_ID = dc.DOCUMENT_ID

    WHERE COALESCE(dc.ACTIVE, TRUE) = TRUE
      AND COALESCE(d.ACTIVE, TRUE) = TRUE
      AND UPPER(d.PROCESSING_STATUS) = 'INDEXED';


-- ============================================================
-- VERIFY
-- ============================================================

SHOW CORTEX SEARCH SERVICES;


-- ============================================================
-- VERIFY SOURCE COUNTS
-- ============================================================

SELECT
    'POLICY_CHUNKS' AS SOURCE,
    COUNT(*) AS CHUNKS
FROM POLICY_CHUNKS
WHERE COALESCE(ACTIVE, TRUE) = TRUE

UNION ALL

SELECT
    'DOCUMENT_CHUNKS' AS SOURCE,
    COUNT(*) AS CHUNKS
FROM DOCUMENT_CHUNKS dc
INNER JOIN DOCUMENTS d
    ON d.DOCUMENT_ID = dc.DOCUMENT_ID
WHERE COALESCE(dc.ACTIVE, TRUE) = TRUE
  AND COALESCE(d.ACTIVE, TRUE) = TRUE
  AND UPPER(d.PROCESSING_STATUS) = 'INDEXED';


-- ============================================================
-- VERIFY UPLOADED DOCUMENTS
-- ============================================================

SELECT
    d.DOCUMENT_ID,
    d.ORIGINAL_FILENAME,
    d.FILE_TYPE,
    d.ACTIVE,
    d.PROCESSING_STATUS,
    d.PAGE_COUNT,
    d.CHUNK_COUNT,
    COUNT(dc.CHUNK_ID) AS ACTUAL_CHUNKS
FROM DOCUMENTS d
LEFT JOIN DOCUMENT_CHUNKS dc
    ON d.DOCUMENT_ID = dc.DOCUMENT_ID
WHERE d.ACTIVE = TRUE
GROUP BY
    d.DOCUMENT_ID,
    d.ORIGINAL_FILENAME,
    d.FILE_TYPE,
    d.ACTIVE,
    d.PROCESSING_STATUS,
    d.PAGE_COUNT,
    d.CHUNK_COUNT
ORDER BY d.CREATED_AT DESC;