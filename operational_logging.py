import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


DEFAULT_LOG_PATH = Path(__file__).parent / "logs" / "worker" / "worker.log"
MAX_LOG_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5


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

    console = logging.StreamHandler()
    console.setLevel(level)
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
