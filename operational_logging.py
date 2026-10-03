import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path


DEFAULT_LOG_PATH = Path(__file__).parent / "logs" / "worker" / "worker.log"
MAX_LOG_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5
LIFECYCLE_LOGGER = "local_agent.lifecycle"
_console_lock = threading.RLock()


class LifecycleConsoleFilter(logging.Filter):
    def filter(self, record):
        return record.name != LIFECYCLE_LOGGER


def _safe_console_write(message, stream=None):
    """Write one console record promptly, tolerating hostile stream encodings."""
    stream = stream or sys.stdout
    with _console_lock:
        try:
            stream.write(message)
        except UnicodeEncodeError:
            encoding = getattr(stream, "encoding", None) or "ascii"
            stream.write(message.encode(encoding, "backslashreplace").decode(encoding))
        stream.flush()


def emit_lifecycle(message):
    """Emit a lifecycle block once to stdout and also to the persistent log."""
    text = message.rstrip("\n")
    _safe_console_write(text + "\n")
    logging.getLogger(LIFECYCLE_LOGGER).info(text)


class ThirdPartyDebugFilter(logging.Filter):
    def filter(self, record):
        if (
            record.name.startswith("slack_bolt")
            or record.name.startswith("slack_sdk")
        ):
            return record.levelno >= logging.INFO

        return True


def configure_logging(log_path=DEFAULT_LOG_PATH, level=logging.INFO):
    """Configure console and persistent worker operational logging."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    for handler in root.handlers[:]:
        if getattr(handler, "_local_agent_handler", False):
            root.removeHandler(handler)
            handler.close()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.addFilter(LifecycleConsoleFilter())
    console.setFormatter(logging.Formatter("%(message)s"))
    console._local_agent_handler = True

    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        path,
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(ThirdPartyDebugFilter())
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
    )
    file_handler._local_agent_handler = True

    root.addHandler(console)
    root.addHandler(file_handler)
    
    return path
