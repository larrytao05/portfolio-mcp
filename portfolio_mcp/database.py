from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from sqlalchemy.engine import create_engine
from sqlalchemy.orm import sessionmaker

from alembic import command
from portfolio_mcp.submission_locks import SubmissionLocks


def upgrade_database(database_url: str) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


class Database:
    def __init__(
        self,
        database_url: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.submission_locks = SubmissionLocks.from_database_url(database_url)
        upgrade_database(database_url)
        self.engine = create_engine(database_url)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.clock = clock or (lambda: datetime.now(UTC))

    def now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("Clock must return a timezone-aware timestamp")
        return value.astimezone(UTC)
