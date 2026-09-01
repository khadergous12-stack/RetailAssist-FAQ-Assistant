from __future__ import annotations

import logging
import os
from pathlib import Path


def setup_logging() -> None:
    """
    Configure application-wide logging.

    Logs are written both to the console and to a file so that
    runtime issues can be diagnosed during development and demo runs.
    """

    log_level_name = os.environ.get(
        "RETAIL_ASSIST_LOG_LEVEL",
        "INFO",
    ).upper()

    log_level = getattr(logging, log_level_name, logging.INFO)

    log_directory = Path("logs")
    log_directory.mkdir(parents=True, exist_ok=True)

    log_file = log_directory / "supportai.log"

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(
        log_file,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Do not remove handlers installed by Streamlit or third-party libraries.
    # Only add our handlers if they are not already present.
    has_console_handler = any(
        getattr(handler, "_supportai_console_handler", False)
        for handler in root_logger.handlers
    )
    has_file_handler = any(
        getattr(handler, "_supportai_file_handler", False)
        for handler in root_logger.handlers
    )

    if not has_console_handler:
        console_handler._supportai_console_handler = True
        root_logger.addHandler(console_handler)

    if not has_file_handler:
        file_handler._supportai_file_handler = True
        root_logger.addHandler(file_handler)

    logging.getLogger(__name__).info(
        "Logging initialized | level=%s | file=%s",
        log_level_name,
        log_file,
    )
