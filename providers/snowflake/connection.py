import os

from dotenv import load_dotenv
from snowflake.snowpark import Session


# Load variables from .env
load_dotenv()


def create_snowflake_session() -> Session:
    """
    Create a Snowpark session from environment variables.

    Real credentials must never be hard-coded in source code.
    """

    required_variables = [
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_SCHEMA",
    ]

    missing_variables = [
        variable for variable in required_variables if not os.environ.get(variable)
    ]

    if missing_variables:
        raise ValueError(
            "Missing Snowflake environment variables: " + ", ".join(missing_variables)
        )

    connection_parameters = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
        "password": os.environ["SNOWFLAKE_PASSWORD"],
        "warehouse": os.environ["SNOWFLAKE_WAREHOUSE"],
        "database": os.environ["SNOWFLAKE_DATABASE"],
        "schema": os.environ["SNOWFLAKE_SCHEMA"],
    }

    role = os.environ.get("SNOWFLAKE_ROLE")

    if role:
        connection_parameters["role"] = role

    return Session.builder.configs(connection_parameters).create()
