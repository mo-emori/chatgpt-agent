"""Process-scoped ownership guard for the Local Agent Worker daemon."""

import json
import os
import socket
from pathlib import Path


DEFAULT_LOCK_PATH = Path(__file__).parent / "logs" / "worker" / "agent_worker.lock"


class SingleInstanceAlreadyRunning(RuntimeError):
    """Raised when another daemon process owns the worker lock."""


class WorkerInstanceGuard:
    """Hold an OS byte-range lock for the lifetime of one daemon instance.

    The file contents are diagnostic owner metadata only. The kernel lock is
    authoritative and is released automatically if the process terminates.
    """

    def __init__(self, path=DEFAULT_LOCK_PATH):
        self.path = Path(path)
        self._file = None

    def acquire(self):
        if self._file is not None:
            return self

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            self._lock(handle)
        except OSError as exc:
            handle.close()
            raise SingleInstanceAlreadyRunning(
                f"SINGLE_INSTANCE_ALREADY_RUNNING: lock={self.path}"
            ) from exc

        self._file = handle
        metadata = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
        }
        handle.seek(0)
        handle.write(json.dumps(metadata, sort_keys=True).encode("utf-8") + b"\n")
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
        return self

    @staticmethod
    def _lock(handle):
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(handle):
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def release(self):
        handle, self._file = self._file, None
        if handle is None:
            return
        try:
            self._unlock(handle)
        finally:
            handle.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
