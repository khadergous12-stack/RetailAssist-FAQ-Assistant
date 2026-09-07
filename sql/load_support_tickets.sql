-- ============================================================
-- RetailAssist / SupportAI
-- Extension Step 2: Load Structured Support Ticket Data
-- ============================================================

USE DATABASE RETAIL_ASSIST_DB;
USE SCHEMA RETAIL_ASSIST;

-- ============================================================
-- 1. CHECK THAT THE CSV EXISTS IN THE STAGE
-- ============================================================

LIST @SUPPORT_TICKETS_STAGE;

-- ============================================================
-- 2. LOAD CSV INTO SUPPORT_TICKETS
-- ============================================================

COPY INTO SUPPORT_TICKETS (
    TICKET_ID,
    CREATED_DATE,
    CATEGORY,
    STATUS,
    PRIORITY,
    RESOLUTION_TIME_HOURS,
    CUSTOMER_RATING,
    CHANNEL,
    REGION,
    PRODUCT_FAMILY,
    REFUND_AMOUNT,
    IS_ESCALATED
)
FROM @SUPPORT_TICKETS_STAGE
FILES = ('support_tickets.csv')
FILE_FORMAT = (
    FORMAT_NAME = 'SUPPORT_TICKETS_CSV_FORMAT'
)
ON_ERROR = 'ABORT_STATEMENT'
FORCE = FALSE;

-- ============================================================
-- 3. TOTAL RECORDS
-- ============================================================

SELECT COUNT(*) AS TOTAL_TICKETS
FROM SUPPORT_TICKETS;

-- ============================================================
-- 4. DATE RANGE
-- ============================================================

SELECT
    MIN(CREATED_DATE) AS MIN_CREATED_DATE,
    MAX(CREATED_DATE) AS MAX_CREATED_DATE
FROM SUPPORT_TICKETS;

-- ============================================================
-- 5. CATEGORY DISTRIBUTION
-- ============================================================

SELECT
    CATEGORY,
    COUNT(*) AS TICKET_COUNT
FROM SUPPORT_TICKETS
GROUP BY CATEGORY
ORDER BY CATEGORY;

-- ============================================================
-- 6. STATUS DISTRIBUTION
-- ============================================================

SELECT
    STATUS,
    COUNT(*) AS TICKET_COUNT
FROM SUPPORT_TICKETS
GROUP BY STATUS
ORDER BY STATUS;

-- ============================================================
-- 7. PRIORITY DISTRIBUTION
-- ============================================================

SELECT
    PRIORITY,
    COUNT(*) AS TICKET_COUNT
FROM SUPPORT_TICKETS
GROUP BY PRIORITY
ORDER BY PRIORITY;

-- ============================================================
-- 8. UNRESOLVED TICKET VALIDATION
-- ============================================================

SELECT
    COUNT(*) AS INVALID_UNRESOLVED_ROWS
FROM SUPPORT_TICKETS
WHERE STATUS <> 'Closed'
  AND (
      RESOLUTION_TIME_HOURS IS NOT NULL
      OR CUSTOMER_RATING IS NOT NULL
  );

-- ============================================================
-- 9. REFUND AMOUNT VALIDATION
-- ============================================================

SELECT
    COUNT(*) AS INVALID_NON_REFUND_ROWS
FROM SUPPORT_TICKETS
WHERE CATEGORY NOT IN ('Refunds', 'Returns')
  AND COALESCE(REFUND_AMOUNT, 0) <> 0;