-- ============================================================
-- RetailAssist / SupportAI
-- Extension Step 2: Structured Support Analytics Setup
-- ============================================================

CREATE DATABASE IF NOT EXISTS RETAIL_ASSIST_DB;

CREATE SCHEMA IF NOT EXISTS RETAIL_ASSIST_DB.RETAIL_ASSIST;

USE DATABASE RETAIL_ASSIST_DB;
USE SCHEMA RETAIL_ASSIST;

-- ============================================================
-- 1. SUPPORT TICKETS TABLE
-- ============================================================

CREATE TABLE IF NOT EXISTS SUPPORT_TICKETS (
    TICKET_ID              VARCHAR(50) NOT NULL,
    CREATED_DATE           DATE NOT NULL,
    CATEGORY               VARCHAR(50) NOT NULL,
    STATUS                 VARCHAR(30) NOT NULL,
    PRIORITY               VARCHAR(30) NOT NULL,
    RESOLUTION_TIME_HOURS  NUMBER(10,2),
    CUSTOMER_RATING        NUMBER(1,0),
    CHANNEL                VARCHAR(30) NOT NULL,
    REGION                 VARCHAR(30) NOT NULL,
    PRODUCT_FAMILY         VARCHAR(50) NOT NULL,
    REFUND_AMOUNT          NUMBER(10,2) DEFAULT 0,
    IS_ESCALATED           BOOLEAN NOT NULL
);

-- ============================================================
-- 2. INTERNAL STAGE
-- ============================================================

CREATE STAGE IF NOT EXISTS SUPPORT_TICKETS_STAGE
    ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');

-- ============================================================
-- 3. CSV FILE FORMAT
-- ============================================================

CREATE FILE FORMAT IF NOT EXISTS SUPPORT_TICKETS_CSV_FORMAT
    TYPE = 'CSV'
    SKIP_HEADER = 1
    FIELD_OPTIONALLY_ENCLOSED_BY = '"'
    NULL_IF = ('', 'NULL');

-- ============================================================
-- 4. VERIFY SETUP
-- ============================================================

SHOW TABLES;

SHOW STAGES;

SHOW FILE FORMATS;

SELECT COUNT(*) AS SUPPORT_TICKET_COUNT
FROM SUPPORT_TICKETS;