-- ============================================================
-- RetailAssist / SupportAI
-- Extension Step 3: SUPPORT_ANALYTICS Semantic View
-- ============================================================

USE DATABASE RETAIL_ASSIST_DB;
USE SCHEMA RETAIL_ASSIST;

-- ============================================================
-- CREATE / UPDATE SEMANTIC VIEW
-- ============================================================

CREATE OR ALTER SEMANTIC VIEW SUPPORT_ANALYTICS

TABLES (
    support AS SUPPORT_TICKETS
        PRIMARY KEY (TICKET_ID)
        WITH SYNONYMS ('support tickets', 'tickets', 'support cases')
        COMMENT = 'One row per customer support ticket.'
)

DIMENSIONS (

    support.created_date
        AS CREATED_DATE
        WITH SYNONYMS ('created date', 'ticket date', 'date created')
        COMMENT = 'Date when the support ticket was created.'

    ,support.category
        AS CATEGORY
        WITH SYNONYMS ('issue type', 'issue category', 'ticket category')
        COMMENT = 'Type of support issue.'
        SAMPLE_VALUES ('Refunds', 'Shipping', 'Returns', 'Payments', 'Warranty')
        IS_ENUM

    ,support.status
        AS STATUS
        WITH SYNONYMS ('ticket status', 'case status')
        COMMENT = 'Current lifecycle status of the support ticket.'
        SAMPLE_VALUES ('Closed', 'In Progress', 'Open')
        IS_ENUM

    ,support.priority
        AS PRIORITY
        WITH SYNONYMS ('ticket priority', 'case priority')
        COMMENT = 'Priority assigned to the support ticket.'
        SAMPLE_VALUES ('Low', 'Medium', 'High', 'Critical')
        IS_ENUM

    ,support.channel
        AS CHANNEL
        WITH SYNONYMS ('contact channel', 'support channel')
        COMMENT = 'Customer channel used to create the support ticket.'
        SAMPLE_VALUES ('Email', 'Chat', 'Phone', 'Web')
        IS_ENUM

    ,support.region
        AS REGION
        WITH SYNONYMS ('geography', 'area')
        COMMENT = 'Geographic region associated with the support ticket.'
        SAMPLE_VALUES ('North', 'South', 'East', 'West')
        IS_ENUM

    ,support.product_family
        AS PRODUCT_FAMILY
        WITH SYNONYMS ('product category', 'product type')
        COMMENT = 'Product family associated with the support ticket.'
        SAMPLE_VALUES ('Electronics', 'Home', 'Fashion', 'Beauty', 'Sports')
        IS_ENUM

    ,support.resolution_time_hours
        AS RESOLUTION_TIME_HOURS
        WITH SYNONYMS ('resolution time', 'resolution hours', 'time to resolve')
        COMMENT = 'Elapsed time in hours required to resolve a closed ticket. NULL for unresolved tickets.'

    ,support.customer_rating
        AS CUSTOMER_RATING
        WITH SYNONYMS ('CSAT', 'rating', 'customer satisfaction', 'satisfaction rating')
        COMMENT = 'Customer rating from 1 to 5. NULL for unresolved or unrated tickets.'

    ,support.refund_amount
        AS REFUND_AMOUNT
        WITH SYNONYMS ('refund value', 'refund amount')
        COMMENT = 'Refund amount associated with the ticket, primarily populated for Refunds and Returns.'

    ,support.is_escalated
        AS IS_ESCALATED
        WITH SYNONYMS ('escalated', 'escalation', 'ticket escalated')
        COMMENT = 'Whether the support ticket required escalation.'
)

METRICS (

    support.ticket_count
        AS COUNT(*)
        WITH SYNONYMS ('ticket volume', 'number of tickets', 'ticket total', 'case count')
        COMMENT = 'Total number of support tickets.'

    ,support.open_ticket_count
        AS COUNT_IF(support.status = 'Open')
        WITH SYNONYMS ('open tickets', 'currently open tickets', 'open ticket volume')
        COMMENT = 'Number of currently open support tickets.'

    ,support.closed_ticket_count
        AS COUNT_IF(support.status = 'Closed')
        WITH SYNONYMS ('closed tickets', 'closed ticket volume')
        COMMENT = 'Number of closed support tickets.'

    ,support.average_resolution_hours
        AS AVG(support.resolution_time_hours)
        WITH SYNONYMS ('average resolution time', 'avg resolution time', 'average resolution hours')
        COMMENT = 'Average resolution time in hours for tickets with a recorded resolution time.'

    ,support.average_customer_rating
        AS AVG(support.customer_rating)
        WITH SYNONYMS ('average rating', 'average CSAT', 'average customer satisfaction')
        COMMENT = 'Average customer rating on a 1 to 5 scale.'

    ,support.escalation_rate
        AS 100.0 * COUNT_IF(support.is_escalated) / NULLIF(COUNT(*), 0)
        WITH SYNONYMS ('escalation percentage', 'percentage escalated', 'percent escalated')
        COMMENT = 'Percentage of support tickets that were escalated.'

    ,support.total_refund_amount
        AS SUM(COALESCE(support.refund_amount, 0))
        WITH SYNONYMS ('total refunds', 'refund total', 'total refund value')
        COMMENT = 'Total refund amount across support tickets.'
)

COMMENT = 'Business semantic layer for SupportAI structured support-ticket analytics.';