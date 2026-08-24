import os

from app.main import create_controller
from app.ui import run_app


def main():
    os.environ["RETAIL_ASSIST_MODE"] = "SNOWFLAKE"

    controller = create_controller()

    run_app(controller)


if __name__ == "__main__":
    main()
