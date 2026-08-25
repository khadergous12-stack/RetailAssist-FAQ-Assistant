-- ============================================================
-- RetailAssist
-- Phase 4: Dynamic Document Upload Foundation
-- ============================================================

USE DATABASE RETAIL_ASSIST_DB;
USE SCHEMA RETAIL_ASSIST;


-- ------------------------------------------------------------
-- 1. Internal stage for uploaded documents
-- ------------------------------------------------------------

CREATE STAGE IF NOT EXISTS DOCUMENT_UPLOAD_STAGE
    ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');


-- ------------------------------------------------------------
-- 2. Document metadata table
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS DOCUMENTS (

    DOCUMENT_ID        VARCHAR(100) NOT NULL,
    ORIGINAL_FILENAME  VARCHAR(255) NOT NULL,
    SANITIZED_FILENAME VARCHAR(255) NOT NULL,

    STAGED_FILE_PATH   VARCHAR(1000),

    FILE_TYPE          VARCHAR(50) NOT NULL,
    FILE_SIZE          NUMBER NOT NULL,

    CONTENT_HASH       VARCHAR(64) NOT NULL,

    CATEGORY           VARCHAR(100),
    DESCRIPTION        VARCHAR(1000),
    TAGS               VARCHAR(1000),

    UPLOADED_BY        VARCHAR(255),

    PROCESSING_STATUS  VARCHAR(30) NOT NULL
                       DEFAULT 'UPLOADED',

    PAGE_COUNT         INTEGER,
    CHARACTER_COUNT    INTEGER,
    CHUNK_COUNT        INTEGER,

    ERROR_MESSAGE      VARCHAR(2000),

    CREATED_AT         TIMESTAMP_NTZ
                       DEFAULT CURRENT_TIMESTAMP(),

    UPDATED_AT         TIMESTAMP_NTZ
                       DEFAULT CURRENT_TIMESTAMP(),

    ACTIVE             BOOLEAN
                       DEFAULT TRUE,

    CONSTRAINT PK_DOCUMENTS
        PRIMARY KEY (DOCUMENT_ID),

    CONSTRAINT UQ_DOCUMENT_HASH
        UNIQUE (CONTENT_HASH)
);


-- ------------------------------------------------------------
-- 3. Verify objects
-- ------------------------------------------------------------

SHOW STAGES;

SHOW TABLES;

SELECT
    DOCUMENT_ID,
    ORIGINAL_FILENAME,
    FILE_TYPE,
    FILE_SIZE,
    PROCESSING_STATUS,
    ACTIVE,
    CREATED_AT
FROM DOCUMENTS
ORDER BY CREATED_AT DESC;