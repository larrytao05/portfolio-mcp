from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import threading
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.engine import make_url


class SubmissionLockError(RuntimeError):
    pass


@dataclass
class _LocalLock:
    lock: threading.Lock = field(default_factory=threading.Lock)


_LOCAL_LOCKS: weakref.WeakValueDictionary[tuple[str, str], _LocalLock] = (
    weakref.WeakValueDictionary()
)
_LOCAL_LOCKS_GUARD = threading.Lock()


class SubmissionLocks:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path.resolve()
        self._lock_directory = self._database_path.parent / (
            f".{self._database_path.name}.submission-locks"
        )

    @classmethod
    def from_database_url(cls, database_url: str) -> SubmissionLocks:
        url = make_url(database_url)
        if url.drivername.split("+", 1)[0] != "sqlite":
            raise SubmissionLockError("Submission locks require local SQLite")
        database = url.database
        if (
            not database
            or database == ":memory:"
            or database.startswith("file:")
            or url.query.get("mode") == "memory"
        ):
            raise SubmissionLockError("Submission locks require a SQLite file")
        return cls(Path(database).expanduser())

    @contextmanager
    def claim(self, draft_id: str) -> Iterator[bool]:
        key = (str(self._database_path), draft_id)
        with _LOCAL_LOCKS_GUARD:
            local = _LOCAL_LOCKS.get(key)
            if local is None:
                local = _LocalLock()
                _LOCAL_LOCKS[key] = local

        if not local.lock.acquire(blocking=False):
            yield False
            return

        descriptor: int | None = None
        keep_local_lock = True
        try:
            self._lock_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            mode = self._lock_directory.stat().st_mode & 0o777
            if mode & 0o077:
                raise SubmissionLockError("Submission lock directory is not private")
            lock_name = f"{hashlib.sha256(draft_id.encode()).hexdigest()}.lock"
            descriptor = os.open(
                self._lock_directory / lock_name,
                os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise SubmissionLockError(
                        "Could not acquire submission lock"
                    ) from error
                os.close(descriptor)
                descriptor = None
                local.lock.release()
                keep_local_lock = False
                yield False
                return
            yield True
        except SubmissionLockError:
            raise
        except OSError as error:
            raise SubmissionLockError("Could not acquire submission lock") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if keep_local_lock:
                local.lock.release()
