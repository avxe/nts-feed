import json
import os
import tempfile
import fcntl
from contextlib import contextmanager
from typing import Any, Callable, Optional

from ..runtime_paths import episodes_dir, shows_path


def _ensure_parent_dir(file_path: str) -> None:
    parent = os.path.dirname(os.path.abspath(file_path))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def _fsync_directory(file_path: str) -> None:
    """Fsync the parent directory to persist the rename metadata."""
    parent_dir = os.path.dirname(os.path.abspath(file_path)) or "."
    try:
        fd = os.open(parent_dir, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        # Best-effort on platforms/filesystems that do not support this
        pass


class JsonDocumentStore:
    """JSON document store with per-document lockfile and atomic writes.

    - Readers acquire a shared lock on <path>.lock before reading.
    - Writers acquire an exclusive lock on <path>.lock, write to a temp file,
      fsync, then atomic rename into place, followed by directory fsync.
    """

    def __init__(self, path: str, default_factory: Optional[Callable[[], Any]] = None, indent: int = 4):
        self.path = path
        self.lock_path = f"{path}.lock"
        self.default_factory = default_factory
        self.indent = indent

    @contextmanager
    def _acquire_lock(self, exclusive: bool):
        _ensure_parent_dir(self.lock_path)
        # Open lock file in append mode to ensure it exists
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield fd
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def read(self) -> Any:
        with self._acquire_lock(exclusive=False):
            if not os.path.exists(self.path):
                return self.default_factory() if self.default_factory else None
            with open(self.path, "r") as f:
                return json.load(f)

    def write(self, data: Any) -> None:
        _ensure_parent_dir(self.path)
        # Perform atomic write under an exclusive lock
        with self._acquire_lock(exclusive=True):
            dir_name = os.path.dirname(self.path) or "."
            prefix = ".tmp-" + os.path.basename(self.path)
            tmp_fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=prefix)
            try:
                with os.fdopen(tmp_fd, "w") as f:
                    json.dump(data, f, indent=self.indent)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.path)
                _fsync_directory(self.path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                raise

    @contextmanager
    def read_lock(self):
        with self._acquire_lock(exclusive=False):
            yield

    @contextmanager
    def write_lock(self):
        with self._acquire_lock(exclusive=True):
            yield


# Typed helpers
def get_shows_store() -> JsonDocumentStore:
    return JsonDocumentStore(
        path=str(shows_path()),
        default_factory=lambda: {}
    )


def get_episodes_store(slug: str) -> JsonDocumentStore:
    return JsonDocumentStore(
        path=str(episodes_dir() / f"{slug}.json"),
        default_factory=lambda: {"episodes": []}
    )

