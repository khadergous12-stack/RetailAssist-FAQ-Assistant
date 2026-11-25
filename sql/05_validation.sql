-- RetailAssist Support Analytics validation queries

-- AN-01
SELECT COUNT(*) AS open_ticket_count
FROM RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_TICKETS
WHERE status = 'Open';

-- AN-02
SELECT category,
       AVG(resolution_time_hours) AS average_resolution_hours
FROM RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_TICKETS
WHERE resolution_time_hours IS NOT NULL
GROUP BY category
ORDER BY average_resolution_hours DESC NULLS LAST
LIMIT 1;

-- AN-03
SELECT DATE_TRUNC('MONTH', created_date) AS month,
       COUNT(*) AS ticket_count
FROM RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_TICKETS
WHERE created_date >= '2026-03-01'
  AND created_date < '2026-09-01'
GROUP BY 1
ORDER BY 1;

-- AN-04
SELECT category,
       AVG(customer_rating) AS average_customer_rating
FROM RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_TICKETS
WHERE customer_rating IS NOT NULL
GROUP BY category
ORDER BY average_customer_rating ASC NULLS LAST
LIMIT 1;

-- AN-05
SELECT COUNT(*) AS high_priority_ticket_count
FROM RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_TICKETS
WHERE priority = 'High'
  AND created_date >= '2026-08-01'
  AND created_date < '2026-09-01';

-- AN-06
SELECT category,
       ROUND(100.0 * AVG(IFF(is_escalated, 1, 0)), 2) AS escalation_rate_percent
FROM RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_TICKETS
GROUP BY category
ORDER BY escalation_rate_percent DESC;

-- AN-07
SELECT category,
       AVG(resolution_time_hours) AS average_resolution_hours
FROM RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_TICKETS
WHERE category IN ('Refunds', 'Shipping')
  AND resolution_time_hours IS NOT NULL
GROUP BY category
ORDER BY category;

-- AN-08
SELECT region,
       COUNT(*) AS open_ticket_count
FROM RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_TICKETS
WHERE status = 'Open'
GROUP BY region
ORDER BY open_ticket_count DESC;

-- AN-09
SELECT DATE_TRUNC('MONTH', created_date) AS month,
       SUM(refund_amount) AS total_refund_amount
FROM RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_TICKETS
GROUP BY 1
ORDER BY 1;

-- AN-10
SELECT product_family,
       AVG(customer_rating) AS average_customer_rating
FROM RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_TICKETS
WHERE customer_rating IS NOT NULL
GROUP BY product_family
ORDER BY average_customer_rating ASC NULLS LAST
LIMIT 1;
