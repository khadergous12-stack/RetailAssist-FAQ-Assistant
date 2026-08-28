-- ============================================================
-- RetailAssist FAQ Assistant
-- Phase 2: Snowflake Foundation
-- ============================================================

-- 1. Create the project database
CREATE DATABASE IF NOT EXISTS RETAIL_ASSIST_DB;

-- 2. Create the project schema
CREATE SCHEMA IF NOT EXISTS RETAIL_ASSIST_DB.RETAIL_ASSIST;

-- 3. Use the project database and schema
USE DATABASE RETAIL_ASSIST_DB;
USE SCHEMA RETAIL_ASSIST;

-- 4. Create the complete policy source table
CREATE TABLE IF NOT EXISTS POLICY_SOURCES (
    DOCUMENT_ID     VARCHAR(50) NOT NULL,
    DOCUMENT_NAME   VARCHAR(255) NOT NULL,
    CATEGORY        VARCHAR(100) NOT NULL,
    CONTENT         TEXT NOT NULL,
    CREATED_AT      TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    
    CONSTRAINT PK_POLICY_SOURCES
        PRIMARY KEY (DOCUMENT_ID)
);

-- 5. Verify the table
SHOW TABLES;