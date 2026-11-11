from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command


def test_both_prior_migration_heads_upgrade_without_losing_cancellation(tmp_path):
    for revision in (None, "20260930_0016", "20261110_0011"):
        url = f"sqlite:///{tmp_path / (revision or 'fresh')}.sqlite"
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        if revision:
            command.upgrade(config, revision)
        engine = create_engine(url)
        if revision == "20261110_0011":
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO cancellation_attempts VALUES "
                        "('account','order','UNKNOWN',"
                        "'2026-01-01T00:00:00+00:00',"
                        "'2026-01-01T00:00:00+00:00')"
                    )
                )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
                == "20261111_0018"
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM imported_cancellation_events")
                ).scalar()
                == 0
            )
            expected = 1 if revision == "20261110_0011" else 0
            assert (
                connection.execute(
                    text("SELECT count(*) FROM cancellation_attempts")
                ).scalar()
                == expected
            )
            if expected:
                assert (
                    connection.execute(
                        text("SELECT state FROM cancellation_attempts")
                    ).scalar()
                    == "UNKNOWN"
                )
        engine.dispose()
