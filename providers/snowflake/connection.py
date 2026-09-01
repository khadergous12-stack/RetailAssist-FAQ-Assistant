import logging
import os
import time

from dotenv import load_dotenv
from snowflake.snowpark import Session


logger = logging.getLogger(__name__)
# Load variables from .env
load_dotenv()


def create_snowflake_session() -> Session:
    """
    Create a Snowpark session using a Snowflake
    Programmatic Access Token (PAT).

    The PAT must never be hard-coded in source code.
    """
    start_time = time.perf_counter()

    logger.info("Snowflake session creation started.")

    required_variables = [
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PAT",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_SCHEMA",
    ]

    missing_variables = [
        variable for variable in required_variables if not os.environ.get(variable)
    ]

    if missing_variables:
        logger.error(
            "Snowflake session creation blocked by missing configuration | variables=%s",
            ", ".join(missing_variables),
        )
        raise ValueError(
            "Missing Snowflake environment variables: " + ", ".join(missing_variables)
        )

    connection_parameters = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
        "authenticator": "PROGRAMMATIC_ACCESS_TOKEN",
        "token": os.environ["SNOWFLAKE_PAT"],
        "warehouse": os.environ["SNOWFLAKE_WAREHOUSE"],
        "database": os.environ["SNOWFLAKE_DATABASE"],
        "schema": os.environ["SNOWFLAKE_SCHEMA"],
    }

    role = os.environ.get("SNOWFLAKE_ROLE")

    if role:
        connection_parameters["role"] = role

    try:
        session = Session.builder.configs(connection_parameters).create()

        elapsed = time.perf_counter() - start_time

        if elapsed >= 10.0:
            logger.warning(
                "Slow Snowflake session creation detected | duration=%.3fs",
                elapsed,
            )
        else:
            logger.info(
                "Snowflake session created successfully | duration=%.3fs",
                elapsed,
            )

        return session

    except Exception:
        logger.exception(
            "Snowflake session creation failed | duration=%.3fs",
            time.perf_counter() - start_time,
        )
        raise
