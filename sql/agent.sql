USE ROLE CAT07_LEARNER_RL;
USE WAREHOUSE CAT07_WH;
USE DATABASE RETAIL_ASSIST_DB;
USE SCHEMA RETAIL_ASSIST;

CREATE OR REPLACE AGENT RETAIL_ASSIST_AGENT
COMMENT = 'RetailAssist agent for policy search and support ticket analytics'
PROFILE = '{
  "display_name": "RetailAssist Assistant",
  "color": "blue"
}'
FROM SPECIFICATION
$$
models:
  orchestration: claude-sonnet-5

orchestration:
  budget:
    seconds: 30
    tokens: 16000

instructions:
  response: "Answer only questions within the RetailAssist customer-support domain. The supported domain covers company policies and FAQs, approved policy documents, and support-ticket analytics available through the configured tools. For policy questions, use the policy search tool. For support analytics questions, use the analytics tool. For combined questions, use both tools and clearly distinguish policy information from operational metrics. Do not use general world knowledge to answer unrelated questions. Do not answer travel, geography, personal, entertainment, current-events, or other unrelated questions. For an out-of-domain question, briefly state that you can only help with RetailAssist policies, FAQs, and support-ticket analytics. Return only the final answer and do not ask the user a follow-up question unless it is genuinely required to answer a supported RetailAssist question."
  orchestration: "First determine whether the request is within the RetailAssist customer-support domain. Reject unrelated requests without using tools. Use Cortex Search for company policy and FAQ questions. Use Cortex Analyst for support-ticket metrics and analytical questions. When a question requires both policy information and support metrics, use both tools. Never use a tool for an unrelated request."
  sample_questions:
    - question: "What does our refund policy say?"
    - question: "How many support tickets are currently open?"
    - question: "What does our refund policy say and how many refund tickets did we receive last month?"

tools:
  - tool_spec:
      type: "cortex_analyst_text_to_sql"
      name: "SupportAnalytics"
      description: "Answers questions about support tickets, including ticket counts, categories, status, priority, resolution time, customer ratings, escalations, refunds, regions, channels, and product families."

  - tool_spec:
      type: "cortex_search"
      name: "PolicySearch"
      description: "Searches company policies, FAQs, and uploaded policy documents to answer policy-related questions."

tool_resources:
  SupportAnalytics:
    semantic_view: "RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_ANALYTICS"
    execution_environment:
      type: "warehouse"
      warehouse: "CAT07_WH"

  PolicySearch:
    name: "RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH"
    max_results: "5"
$$;

SHOW AGENTS IN SCHEMA RETAIL_ASSIST_DB.RETAIL_ASSIST;

DESCRIBE AGENT RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_AGENT;