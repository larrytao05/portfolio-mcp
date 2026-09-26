import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from portfolio_mcp.database import (
    McpAuthorizationRecord,
    OrderDraftRecord,
    PortfolioRepository,
)
from portfolio_mcp.trading_service import (
    CreatedMcpAuthorization,
    McpAuthorizationError,
    McpAuthorizationService,
    StoredMcpAuthorization,
)


def _seed_draft(
    repository: PortfolioRepository,
    draft_id: str = "draft-123",
    account_id: str = "schwab-taxable-demo",
    fingerprint: str = "fp-test-123",
    expires_at: datetime | None = None,
    created_at: datetime | None = None,
) -> OrderDraftRecord:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    draft = OrderDraftRecord(
        id=draft_id,
        account_id=account_id,
        account_label="Schwab Taxable ••••4821",
        provider="Schwab",
        instrument_id="us-etf:VTI",
        symbol="VTI",
        instrument_name="Vanguard Total Stock Market ETF",
        asset_class="equity_etf",
        side="buy",
        order_type="limit",
        quantity=Decimal("5"),
        limit_price=Decimal("220.00"),
        quote_observed_at=now,
        quote_last_price=Decimal("220.00"),
        quote_bid_price=Decimal("219.95"),
        quote_ask_price=Decimal("220.05"),
        quote_source="fixture",
        warnings="[]",
        fingerprint=fingerprint,
        created_at=created_at or now,
        expires_at=expires_at or (now + timedelta(minutes=10)),
    )
    with repository._sessions.begin() as session:
        session.add(draft)
    return draft


def test_create_mcp_authorization_generates_and_stores_hashed_code(tmp_path) -> None:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'auth.db'}", clock=lambda: now)
    _seed_draft(repo, draft_id="draft-1")
    service = McpAuthorizationService(repo, clock=lambda: now, scrypt_n=1024)

    created = service.create_authorization(action="submit", target_draft_id="draft-1")

    assert isinstance(created, CreatedMcpAuthorization)
    assert len(created.plaintext_code) == 8
    assert created.plaintext_code.isdigit()
    assert created.expires_at == now + timedelta(minutes=5)

    with repo._sessions() as session:
        records = list(
            session.scalars(
                select(McpAuthorizationRecord).where(
                    McpAuthorizationRecord.target_draft_id == "draft-1"
                )
            )
        )
        assert len(records) == 1
        rec = records[0]
        # Plaintext code is never stored in DB
        assert created.plaintext_code not in rec.digest
        assert created.plaintext_code not in rec.salt

        # Hash matches scrypt derivation
        expected_digest = hashlib.scrypt(
            created.plaintext_code.encode("utf-8"),
            salt=bytes.fromhex(rec.salt),
            n=1024,
            r=8,
            p=1,
        ).hex()
        assert rec.digest == expected_digest
        assert rec.failed_attempts == 0
        assert rec.consumed_at is None
        assert rec.invalidation_reason is None


def test_create_authorization_supersedes_previous_active_code_for_same_draft(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'auth.db'}", clock=lambda: now)
    _seed_draft(repo, draft_id="draft-1")
    service = McpAuthorizationService(
        repo,
        clock=lambda: now,
        scrypt_n=1024,
        code_generator=iter(["11111111", "22222222"]).__next__,
    )

    first = service.create_authorization(action="submit", target_draft_id="draft-1")
    second = service.create_authorization(action="submit", target_draft_id="draft-1")

    assert first.plaintext_code == "11111111"
    assert second.plaintext_code == "22222222"

    with repo._sessions() as session:
        first_rec = session.get(McpAuthorizationRecord, first.id)
        assert first_rec is not None
        assert first_rec.invalidation_reason == "superseded"

    # Attempting to consume first code fails
    with pytest.raises(McpAuthorizationError) as exc_info:
        service.consume_authorization(
            action="submit",
            target_draft_id="draft-1",
            expected_fingerprint="fp-test-123",
            account_id="schwab-taxable-demo",
            candidate_code="11111111",
        )
    assert exc_info.value.code == "invalid_or_expired_code"


def test_consume_mcp_authorization_success(tmp_path) -> None:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'auth.db'}", clock=lambda: now)
    _seed_draft(repo, draft_id="draft-1", fingerprint="fp-abc", account_id="acc-1")
    service = McpAuthorizationService(
        repo,
        clock=lambda: now,
        scrypt_n=1024,
        code_generator=lambda: "12345678",
    )

    created = service.create_authorization(action="submit", target_draft_id="draft-1")

    consume_time = now + timedelta(seconds=30)
    service_at_consume = McpAuthorizationService(
        repo,
        clock=lambda: consume_time,
        scrypt_n=1024,
    )

    consumed = service_at_consume.consume_authorization(
        action="submit",
        target_draft_id="draft-1",
        expected_fingerprint="fp-abc",
        account_id="acc-1",
        candidate_code="12345678",
    )

    assert isinstance(consumed, StoredMcpAuthorization)
    assert consumed.id == created.id
    assert consumed.consumed_at == consume_time
    # Digest and salt not in StoredMcpAuthorization
    assert not hasattr(consumed, "salt")
    assert not hasattr(consumed, "digest")

    with repo._sessions() as session:
        rec = session.get(McpAuthorizationRecord, created.id)
        assert rec is not None
        assert rec.consumed_at == consume_time
        assert rec.failed_attempts == 0


def test_consume_mcp_authorization_cannot_be_replayed(tmp_path) -> None:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'auth.db'}", clock=lambda: now)
    _seed_draft(repo, draft_id="draft-1", fingerprint="fp-abc", account_id="acc-1")
    service = McpAuthorizationService(
        repo,
        clock=lambda: now,
        scrypt_n=1024,
        code_generator=lambda: "12345678",
    )

    service.create_authorization(action="submit", target_draft_id="draft-1")

    service.consume_authorization(
        action="submit",
        target_draft_id="draft-1",
        expected_fingerprint="fp-abc",
        account_id="acc-1",
        candidate_code="12345678",
    )

    # Replay attempt fails
    with pytest.raises(McpAuthorizationError) as exc_info:
        service.consume_authorization(
            action="submit",
            target_draft_id="draft-1",
            expected_fingerprint="fp-abc",
            account_id="acc-1",
            candidate_code="12345678",
        )
    assert exc_info.value.code == "invalid_or_expired_code"


def test_consume_fails_with_generic_error_and_increments_attempts(tmp_path) -> None:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'auth.db'}", clock=lambda: now)
    _seed_draft(repo, draft_id="draft-1", fingerprint="fp-abc", account_id="acc-1")
    service = McpAuthorizationService(
        repo,
        clock=lambda: now,
        scrypt_n=1024,
        code_generator=lambda: "12345678",
    )

    created = service.create_authorization(action="submit", target_draft_id="draft-1")

    with pytest.raises(McpAuthorizationError) as exc_info:
        service.consume_authorization(
            action="submit",
            target_draft_id="draft-1",
            expected_fingerprint="fp-abc",
            account_id="acc-1",
            candidate_code="00000000",
        )
    assert exc_info.value.code == "invalid_or_expired_code"

    with repo._sessions() as session:
        rec = session.get(McpAuthorizationRecord, created.id)
        assert rec is not None
        assert rec.failed_attempts == 1
        assert rec.consumed_at is None


def test_consume_locks_out_after_five_failed_attempts(tmp_path) -> None:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'auth.db'}", clock=lambda: now)
    _seed_draft(repo, draft_id="draft-1", fingerprint="fp-abc", account_id="acc-1")
    service = McpAuthorizationService(
        repo,
        clock=lambda: now,
        scrypt_n=1024,
        code_generator=lambda: "12345678",
    )

    created = service.create_authorization(action="submit", target_draft_id="draft-1")

    for i in range(5):
        with pytest.raises(McpAuthorizationError) as exc_info:
            service.consume_authorization(
                action="submit",
                target_draft_id="draft-1",
                expected_fingerprint="fp-abc",
                account_id="acc-1",
                candidate_code=f"0000000{i}",
            )
        assert exc_info.value.code == "invalid_or_expired_code"

    with repo._sessions() as session:
        rec = session.get(McpAuthorizationRecord, created.id)
        assert rec is not None
        assert rec.failed_attempts == 5
        assert rec.invalidation_reason == "max_attempts_exceeded"

    # 6th attempt with the CORRECT code now fails because attempts are exhausted
    with pytest.raises(McpAuthorizationError) as exc_info:
        service.consume_authorization(
            action="submit",
            target_draft_id="draft-1",
            expected_fingerprint="fp-abc",
            account_id="acc-1",
            candidate_code="12345678",
        )
    assert exc_info.value.code == "invalid_or_expired_code"


def test_consume_fails_on_expired_code(tmp_path) -> None:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'auth.db'}", clock=lambda: now)
    _seed_draft(repo, draft_id="draft-1", fingerprint="fp-abc", account_id="acc-1")
    service = McpAuthorizationService(
        repo,
        clock=lambda: now,
        scrypt_n=1024,
        code_generator=lambda: "12345678",
    )

    created = service.create_authorization(action="submit", target_draft_id="draft-1")

    expired_time = now + timedelta(minutes=5, seconds=1)
    service_after_expiry = McpAuthorizationService(
        repo,
        clock=lambda: expired_time,
        scrypt_n=1024,
    )

    with pytest.raises(McpAuthorizationError) as exc_info:
        service_after_expiry.consume_authorization(
            action="submit",
            target_draft_id="draft-1",
            expected_fingerprint="fp-abc",
            account_id="acc-1",
            candidate_code="12345678",
        )
    assert exc_info.value.code == "invalid_or_expired_code"

    with repo._sessions() as session:
        rec = session.get(McpAuthorizationRecord, created.id)
        assert rec is not None
        assert rec.invalidation_reason == "expired"
        assert rec.consumed_at is None


def test_consume_fails_on_mismatched_target_or_fingerprint_or_account(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'auth.db'}", clock=lambda: now)
    _seed_draft(repo, draft_id="draft-1", fingerprint="fp-abc", account_id="acc-1")
    service = McpAuthorizationService(
        repo,
        clock=lambda: now,
        scrypt_n=1024,
        code_generator=lambda: "12345678",
    )

    service.create_authorization(action="submit", target_draft_id="draft-1")

    # Wrong draft ID
    with pytest.raises(McpAuthorizationError) as exc_info:
        service.consume_authorization(
            action="submit",
            target_draft_id="draft-wrong",
            expected_fingerprint="fp-abc",
            account_id="acc-1",
            candidate_code="12345678",
        )
    assert exc_info.value.code == "invalid_or_expired_code"

    # Wrong action
    with pytest.raises(McpAuthorizationError) as exc_info:
        service.consume_authorization(
            action="cancel",
            target_draft_id="draft-1",
            expected_fingerprint="fp-abc",
            account_id="acc-1",
            candidate_code="12345678",
        )
    assert exc_info.value.code == "invalid_or_expired_code"

    # Wrong account ID
    with pytest.raises(McpAuthorizationError) as exc_info:
        service.consume_authorization(
            action="submit",
            target_draft_id="draft-1",
            expected_fingerprint="fp-abc",
            account_id="acc-wrong",
            candidate_code="12345678",
        )
    assert exc_info.value.code == "invalid_or_expired_code"

    # Wrong fingerprint
    with pytest.raises(McpAuthorizationError) as exc_info:
        service.consume_authorization(
            action="submit",
            target_draft_id="draft-1",
            expected_fingerprint="fp-wrong",
            account_id="acc-1",
            candidate_code="12345678",
        )
    assert exc_info.value.code == "invalid_or_expired_code"


def test_expiry_caps_at_draft_expiry(tmp_path) -> None:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'auth.db'}", clock=lambda: now)
    # Draft expires in only 2 minutes
    _seed_draft(repo, draft_id="draft-1", expires_at=now + timedelta(minutes=2))
    service = McpAuthorizationService(repo, clock=lambda: now, scrypt_n=1024)

    created = service.create_authorization(action="submit", target_draft_id="draft-1")
    assert created.expires_at == now + timedelta(minutes=2)


def test_create_authorization_fails_if_draft_not_found_or_expired(tmp_path) -> None:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'auth.db'}", clock=lambda: now)
    service = McpAuthorizationService(repo, clock=lambda: now, scrypt_n=1024)

    # Draft not found
    with pytest.raises(McpAuthorizationError) as exc_info:
        service.create_authorization(action="submit", target_draft_id="non-existent")
    assert exc_info.value.code == "draft_not_found"

    # Draft expired
    _seed_draft(
        repo,
        draft_id="draft-expired",
        expires_at=now - timedelta(seconds=1),
    )
    with pytest.raises(McpAuthorizationError) as exc_info:
        service.create_authorization(action="submit", target_draft_id="draft-expired")
    assert exc_info.value.code == "draft_expired"


def test_concurrent_consumption_allows_at_most_one_winner(tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'auth.db'}", clock=lambda: now)
    _seed_draft(
        repo, draft_id="draft-race", fingerprint="fp-race", account_id="acc-race"
    )
    service = McpAuthorizationService(
        repo,
        clock=lambda: now,
        scrypt_n=1024,
        code_generator=lambda: "55555555",
    )

    service.create_authorization(action="submit", target_draft_id="draft-race")

    successes: list[StoredMcpAuthorization] = []
    failures: list[Exception] = []

    def attempt_consume() -> None:
        try:
            res = service.consume_authorization(
                action="submit",
                target_draft_id="draft-race",
                expected_fingerprint="fp-race",
                account_id="acc-race",
                candidate_code="55555555",
            )
            successes.append(res)
        except Exception as error:
            failures.append(error)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(attempt_consume) for _ in range(8)]
        for f in futures:
            f.result()

    assert len(successes) == 1
    assert len(failures) == 7
    for failure in failures:
        assert isinstance(failure, McpAuthorizationError)
        assert failure.code == "invalid_or_expired_code"


def test_created_authorization_repr_does_not_leak_plaintext_code(tmp_path) -> None:
    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'auth.db'}", clock=lambda: now)
    _seed_draft(
        repo, draft_id="draft-repr", fingerprint="fp-repr", account_id="acc-repr"
    )
    service = McpAuthorizationService(
        repo,
        clock=lambda: now,
        scrypt_n=1024,
        code_generator=lambda: "88776655",
    )
    created = service.create_authorization(
        action="submit", target_draft_id="draft-repr"
    )
    assert "88776655" not in repr(created)
    active = repo.active_mcp_authorization(
        action="submit", target_draft_id="draft-repr"
    )
    assert active is not None
    assert active.salt not in repr(active)
    assert active.digest not in repr(active)


def test_concurrent_failed_attempts_lock_out_safely(tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    now = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    repo = PortfolioRepository(f"sqlite:///{tmp_path / 'auth.db'}", clock=lambda: now)
    _seed_draft(
        repo, draft_id="draft-fail-race", fingerprint="fp-fail", account_id="acc-fail"
    )
    service = McpAuthorizationService(
        repo,
        clock=lambda: now,
        scrypt_n=1024,
        code_generator=lambda: "12345678",
    )
    service.create_authorization(action="submit", target_draft_id="draft-fail-race")

    def attempt_fail(i: int) -> None:
        try:
            service.consume_authorization(
                action="submit",
                target_draft_id="draft-fail-race",
                expected_fingerprint="fp-fail",
                account_id="acc-fail",
                candidate_code=f"bad{i}",
            )
        except McpAuthorizationError:
            pass

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(attempt_fail, i) for i in range(5)]
        for f in futures:
            f.result()

    assert (
        repo.active_mcp_authorization(
            action="submit", target_draft_id="draft-fail-race"
        )
        is None
    )

    with repo._sessions() as session:
        rec = session.scalars(
            select(McpAuthorizationRecord).where(
                McpAuthorizationRecord.action == "submit",
                McpAuthorizationRecord.target_draft_id == "draft-fail-race",
            )
        ).one()
        assert rec.failed_attempts == 5
        assert rec.invalidation_reason == "max_attempts_exceeded"


def test_migration_0012_upgrade_and_downgrade(tmp_path) -> None:
    from pathlib import Path

    from alembic.config import Config

    from alembic import command

    database_path = tmp_path / "migration-test.db"
    database_url = f"sqlite:///{database_path}"
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)

    # Upgrade to head (includes 0012)
    command.upgrade(config, "head")

    # Downgrade to 0011
    command.downgrade(config, "20260925_0011")

    # Upgrade again to head
    command.upgrade(config, "head")
