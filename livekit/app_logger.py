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
    logger.propagate = False  # Prevent LiveKit root logger from duplicating these logs

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

    # The TTS/STT/LLM plugins and the LiveKit pipeline itself (agent_activity,
    # perform_tts_inference, etc.) log through their own logger tree
    # ("livekit.agents", "livekit.plugins.sarvam", ...), completely separate
    # from this "voice_bot.*" logger. Those are where TTS synthesis errors,
    # websocket drops, and API failures actually get logged (e.g. Sarvam's
    # tts.py "Sarvam TTS API error", "WebSocket connection failed") — without
    # this, a silently-failing TTS call leaves zero trace in app_voice.log,
    # since our own code only ever logs that a reply was *scheduled*, not
    # that it was actually spoken.
    sdk_log_level = os.environ.get("SDK_LOG_LEVEL", "WARNING").upper()
    for sdk_logger_name in ("livekit.agents", "livekit.plugins.sarvam"):
        sdk_logger = logging.getLogger(sdk_logger_name)
        if file_handler in sdk_logger.handlers:
            continue
        sdk_logger.addHandler(file_handler)
        sdk_logger.addHandler(stream_handler)
        sdk_logger.setLevel(getattr(logging, sdk_log_level, logging.WARNING))

    return logger


applog = _build_logger()
