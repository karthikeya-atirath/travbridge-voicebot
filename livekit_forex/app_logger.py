import logging
from logging.handlers import RotatingFileHandler
import os


def _build_logger() -> logging.Logger:
    """
    Configure a shared logger that logs to both stdout and a rotating file.
    """
    # Use a per-package logger name to avoid handler collisions across apps.
    logger_name = f"voice_bot.{os.path.basename(os.path.dirname(__file__))}"
    logger = logging.getLogger(logger_name)

    if logger.handlers:
        # Logger already configured (module re-import).
        return logger

    log_level = os.environ.get("APP_LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    # Default log location: alongside this module (not current working directory).
    default_log_file = os.path.join(os.path.dirname(__file__), "app_voice.log")
    log_file = os.environ.get("APP_LOG_FILE", default_log_file)
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
                    
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    return logger


applog = _build_logger()
