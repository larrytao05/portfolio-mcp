from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from portfolio_mcp.config import ExecutionSettings, SchwabSettings
from portfolio_mcp.database import (
    AccountNotFoundError,
    PortfolioRepository,
    SchwabAccountMappingConflictError,
)
from portfolio_mcp.models import is_schwab_account_eligible
from portfolio_mcp.provider import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
)
from portfolio_mcp.schema import AccountRecord, SchwabAccountMappingRecord
from portfolio_mcp.schwab_readiness import (
    SchwabReadinessService,
    SchwabReadinessState,
)
from portfolio_mcp.schwab_transport import SchwabOAuthTransport


class FakeHttpClient:
    def __init__(self, responses: list[tuple[int, object] | Exception]) -> None:
        self._responses = list(responses)
        self.invocations: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, object]:
        self.invocations.append((method, url))
        if not self._responses:
            raise RuntimeError(f"No response configured for {method} {url}")
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


class RoutingFakeHttpClient:
    def __init__(self, routes: dict[str, tuple[int, object]]) -> None:
        self.routes = routes
        self.invocations: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, object]:
        self.invocations.append((method, url))
        for pattern, response in self.routes.items():
            if pattern in url:
                return response
        raise RuntimeError(f"No route for {method} {url}")


def _make_repo(tmp_path: Path) -> PortfolioRepository:
    db_file = tmp_path / "test.db"
    return PortfolioRepository(f"sqlite:///{db_file}")


def _seed_accounts(repo: PortfolioRepository) -> None:
    now = datetime.now(UTC)
    with repo._sessions() as s:
        s.add(
            AccountRecord(
                id="schwab-taxable-1",
                provider="schwab",
                label="Schwab Individual Brokerage ••••1234",
                account_type="Taxable brokerage",
                currency="USD",
                refreshed_at=now,
                is_stale=False,
            )
        )
        s.add(
            AccountRecord(
                id="schwab-roth-2",
                provider="schwab",
                label="Schwab Roth IRA ••••5678",
                account_type="Roth IRA",
                currency="USD",
                refreshed_at=now,
                is_stale=False,
            )
        )
        s.commit()


def test_execution_settings_defaults() -> None:
    settings = ExecutionSettings.from_environment({})
    assert settings.provider == "fixture"
    assert settings.schwab_execution_enabled is False
    assert settings.permits_schwab_execution is False


def test_execution_settings_opt_in() -> None:
    settings = ExecutionSettings.from_environment(
        {"EXECUTION_PROVIDER": "schwab", "SCHWAB_EXECUTION_ENABLED": "true"}
    )
    assert settings.provider == "schwab"
    assert settings.schwab_execution_enabled is True
    assert settings.permits_schwab_execution is True


def test_execution_settings_rejects_invalid_provider() -> None:
    with pytest.raises(ProviderConfigurationError, match="EXECUTION_PROVIDER"):
        ExecutionSettings.from_environment({"EXECUTION_PROVIDER": "unsupported"})


def test_schwab_settings_missing_fields_raises() -> None:
    with pytest.raises(ProviderConfigurationError, match="SCHWAB_CLIENT_ID"):
        SchwabSettings.from_environment({})


@pytest.mark.asyncio
async def test_transport_error_mapping() -> None:
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    client = FakeHttpClient(
        [
            (400, {"error": "invalid_grant"}),
        ]
    )
    transport = SchwabOAuthTransport(settings, http_client=client)
    with pytest.raises(ProviderAuthenticationError, match="Refresh token was rejected"):
        await transport.access_token()


@pytest.mark.asyncio
async def test_schwab_transport_token_ttl_and_caching() -> None:
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    client = FakeHttpClient(
        [
            (200, {"access_token": "token-1", "expires_in": 1800}),
            (200, {"access_token": "token-2", "expires_in": 1800}),
        ]
    )
    transport = SchwabOAuthTransport(settings, http_client=client)

    t1 = await transport.access_token()
    assert t1 == "token-1"
    assert len(client.invocations) == 1

    t2 = await transport.access_token()
    assert t2 == "token-1"
    assert len(client.invocations) == 1

    transport._token_expires_at = 0.0
    t3 = await transport.access_token()
    assert t3 == "token-2"
    assert len(client.invocations) == 2


@pytest.mark.asyncio
async def test_schwab_transport_401_retry_invalidation() -> None:
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    client = FakeHttpClient(
        [
            (200, {"access_token": "expired-token", "expires_in": 1800}),
            (401, {"error": "unauthorized"}),
            (200, {"access_token": "fresh-token", "expires_in": 1800}),
            (200, {"status": "ok"}),
        ]
    )
    transport = SchwabOAuthTransport(settings, http_client=client)

    status, body = await transport.request(
        "GET", "https://api.schwabapi.com/trader/v1/accounts"
    )
    assert status == 200
    assert body == {"status": "ok"}
    assert len(client.invocations) == 4


@pytest.mark.asyncio
async def test_schwab_readiness_redacts_full_account_numbers(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)

    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    raw_secret_number = "123456789012"
    client = FakeHttpClient(
        [
            (200, {"access_token": "valid-token"}),
            (
                200,
                [
                    {
                        "accountNumber": raw_secret_number,
                        "hashValue": "hash-redact-test",
                    }
                ],
            ),
        ]
    )
    transport = SchwabOAuthTransport(settings, http_client=client)
    service = SchwabReadinessService(
        repo, transport=transport, schwab_settings=settings
    )

    candidates = await service.list_candidates_for_account("schwab-taxable-1")
    assert len(candidates) == 1
    cand = candidates[0]

    assert cand.masked_account_number == "*9012"
    assert cand.schwab_account_hash == "hash-redact-test"
    assert raw_secret_number not in str(cand.to_dict())
    assert raw_secret_number not in repr(cand)


def test_repository_schwab_mapping_crud_and_uniqueness(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)

    mapping = repo.save_schwab_account_mapping("schwab-taxable-1", "hash-1", "*1234")
    assert mapping.account_id == "schwab-taxable-1"
    assert mapping.schwab_account_hash == "hash-1"
    assert mapping.masked_account_number == "*1234"

    with pytest.raises(SchwabAccountMappingConflictError, match="already mapped"):
        repo.save_schwab_account_mapping("schwab-roth-2", "hash-1", "*1234")

    with pytest.raises(AccountNotFoundError, match="does not exist"):
        repo.save_schwab_account_mapping("nonexistent-account", "hash-unique", "*1234")

    updated = repo.save_schwab_account_mapping("schwab-taxable-1", "hash-new", "*9999")
    assert updated.schwab_account_hash == "hash-new"
    assert updated.masked_account_number == "*9999"

    found = repo.get_schwab_account_mapping("schwab-taxable-1")
    assert found is not None
    assert found.schwab_account_hash == "hash-new"

    found_by_hash = repo.get_schwab_account_mapping_by_hash("hash-new")
    assert found_by_hash is not None
    assert found_by_hash.account_id == "schwab-taxable-1"

    all_mappings = repo.list_schwab_account_mappings()
    assert len(all_mappings) == 1

    assert repo.delete_schwab_account_mapping("schwab-taxable-1") is True
    assert repo.get_schwab_account_mapping("schwab-taxable-1") is None
    assert repo.delete_schwab_account_mapping("schwab-taxable-1") is False


@pytest.mark.parametrize(
    "masked", ["1234", "x1234", "*123", "*１２３４", "*12345", "Unavailable"]
)
def test_repository_rejects_noncanonical_schwab_account_masks(
    tmp_path: Path, masked: str
) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)

    with pytest.raises(ValueError, match="masked account number"):
        repo.save_schwab_account_mapping("schwab-taxable-1", "hash-1", masked)


@pytest.mark.parametrize(
    "details",
    [
        {
            "type": "MARGIN",
            "currency": "USD",
            "isClosingOnlyRestricted": None,
            "isDayTrader": False,
        },
        {
            "type": "MARGIN",
            "currency": "USD",
            "isClosingOnlyRestricted": "false",
            "isDayTrader": False,
        },
        {"type": "MARGIN", "isClosingOnlyRestricted": False, "isDayTrader": False},
        {
            "type": "MARGIN",
            "currency": 1,
            "isClosingOnlyRestricted": False,
            "isDayTrader": False,
        },
        {
            "type": "MARGIN",
            "currency": "USD",
            "isClosingOnlyRestricted": False,
            "isDayTrader": "false",
        },
        {
            "type": 1,
            "currency": "USD",
            "isClosingOnlyRestricted": False,
            "isDayTrader": False,
        },
    ],
)
@pytest.mark.asyncio
async def test_readiness_fails_closed_on_malformed_broker_details(
    tmp_path: Path, details: dict[str, object]
) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    repo.save_schwab_account_mapping("schwab-taxable-1", "hash-1234", "*1234")
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    client = FakeHttpClient(
        [
            (200, {"access_token": "token"}),
            (200, [{"accountNumber": "12345678", "hashValue": "hash-1234"}]),
            (200, {"securitiesAccount": details}),
        ]
    )
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=client),
        execution_settings=ExecutionSettings(
            provider="schwab", schwab_execution_enabled=True
        ),
        schwab_settings=settings,
    )

    result = await service.check_account_readiness("schwab-taxable-1")

    assert result.state == SchwabReadinessState.UNSUPPORTED_ACCOUNT
    assert result.ready is False


@pytest.mark.asyncio
async def test_candidates_skip_malformed_account_numbers(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    client = FakeHttpClient(
        [
            (200, {"access_token": "token"}),
            (
                200,
                [
                    {"accountNumber": "1234", "hashValue": "short"},
                    {"accountNumber": "ABCDEFGH", "hashValue": "letters"},
                    {"accountNumber": 12345678, "hashValue": "number"},
                    {"accountNumber": "１２３４５６７８", "hashValue": "unicode"},
                    {"accountNumber": "87654321", "hashValue": 1234},
                    {"accountNumber": "87654321", "hashValue": "   "},
                    {"accountNumber": "12345678", "hashValue": "valid"},
                ],
            ),
        ]
    )
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=client),
        schwab_settings=settings,
    )

    candidates = await service.list_candidates_for_account("schwab-taxable-1")

    assert [candidate.masked_account_number for candidate in candidates] == ["*5678"]


@pytest.mark.parametrize(
    "failure_stage", ["token", "account_numbers", "account_detail"]
)
@pytest.mark.asyncio
async def test_readiness_does_not_expose_provider_exception_text(
    tmp_path: Path, failure_stage: str
) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    repo.save_schwab_account_mapping("schwab-taxable-1", "hash-1234", "*1234")
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    provider_secret = "private-provider-payload-marker"
    responses: list[tuple[int, object] | Exception] = []
    responses.append(
        RuntimeError(provider_secret)
        if failure_stage == "token"
        else (200, {"access_token": "token"})
    )
    if failure_stage != "token":
        responses.append(
            RuntimeError(provider_secret)
            if failure_stage == "account_numbers"
            else (200, [{"accountNumber": "12345678", "hashValue": "hash-1234"}])
        )
    if failure_stage == "account_detail":
        responses.append(RuntimeError(provider_secret))
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=FakeHttpClient(responses)),
        execution_settings=ExecutionSettings(
            provider="schwab", schwab_execution_enabled=True
        ),
        schwab_settings=settings,
    )

    result = await service.check_account_readiness("schwab-taxable-1")

    assert provider_secret not in str(result.to_dict())
    assert result.ready is False


def test_legacy_schwab_mapping_mask_is_unavailable_and_not_ready(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    repo.save_schwab_account_mapping("schwab-taxable-1", "hash-1234", "*1234")
    with repo._sessions() as session:
        record = session.scalar(
            select(SchwabAccountMappingRecord).where(
                SchwabAccountMappingRecord.account_id == "schwab-taxable-1"
            )
        )
        assert record is not None
        record.masked_account_number = "123456789012"
        session.commit()

    mapping = repo.get_schwab_account_mapping("schwab-taxable-1")
    assert mapping is not None
    assert mapping.masked_account_number == "Unavailable"


@pytest.mark.asyncio
async def test_legacy_schwab_mapping_requires_owner_reverification(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    repo.save_schwab_account_mapping("schwab-taxable-1", "hash-1234", "*1234")
    with repo._sessions() as session:
        record = session.scalar(
            select(SchwabAccountMappingRecord).where(
                SchwabAccountMappingRecord.account_id == "schwab-taxable-1"
            )
        )
        assert record is not None
        record.masked_account_number = "123456789012"
        session.commit()
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=FakeHttpClient([])),
        execution_settings=ExecutionSettings(
            provider="schwab", schwab_execution_enabled=True
        ),
        schwab_settings=settings,
    )

    result = await service.check_account_readiness("schwab-taxable-1")

    assert result.state == SchwabReadinessState.UNMAPPED
    assert result.ready is False


def test_migration_0015_invalidates_existing_schwab_account_masks(
    tmp_path: Path,
) -> None:
    import sqlite3

    from alembic.config import Config

    from alembic import command

    database_path = tmp_path / "schwab-migration.db"
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "20260926_0014")
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO accounts "
            "(id, provider, label, account_type, currency, refreshed_at, is_stale) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("schwab-taxable-1", "schwab", "Taxable", "Brokerage", "USD", now, 0),
        )
        connection.execute(
            "INSERT INTO accounts "
            "(id, provider, label, account_type, currency, refreshed_at, is_stale) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("schwab-roth-2", "schwab", "Roth", "Brokerage", "USD", now, 0),
        )
        connection.executemany(
            "INSERT INTO schwab_account_mappings "
            "(id, account_id, schwab_account_hash, masked_account_number, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("mapping-1", "schwab-taxable-1", "opaque-hash-1", "*1234", now, now),
                (
                    "mapping-2",
                    "schwab-roth-2",
                    "opaque-hash-2",
                    "123456789012",
                    now,
                    now,
                ),
            ],
        )
    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT schwab_account_hash, masked_account_number "
            "FROM schwab_account_mappings ORDER BY schwab_account_hash"
        ).fetchall()

    assert rows == [
        ("opaque-hash-1", "Unavailable"),
        ("opaque-hash-2", "Unavailable"),
    ]

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO accounts "
            "(id, provider, label, account_type, currency, refreshed_at, is_stale) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("schwab-new-3", "schwab", "New", "Brokerage", "USD", now, 0),
        )
        connection.execute(
            "INSERT INTO schwab_account_mappings "
            "(id, account_id, schwab_account_hash, masked_account_number, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("mapping-3", "schwab-new-3", "opaque-hash-3", "*9999", now, now),
        )

    command.downgrade(config, "20260926_0014")
    with sqlite3.connect(database_path) as connection:
        downgraded_rows = connection.execute(
            "SELECT schwab_account_hash, masked_account_number "
            "FROM schwab_account_mappings ORDER BY schwab_account_hash"
        ).fetchall()
    assert downgraded_rows == [
        ("opaque-hash-1", "Unavailable"),
        ("opaque-hash-2", "Unavailable"),
        ("opaque-hash-3", "*9999"),
    ]


@pytest.mark.asyncio
async def test_candidates_suggestion_logic(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)

    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    client = FakeHttpClient(
        [
            (200, {"access_token": "valid-token"}),
            (
                200,
                [
                    {"accountNumber": "999991234", "hashValue": "hash-1234"},
                    {"accountNumber": "888889999", "hashValue": "hash-9999"},
                ],
            ),
        ]
    )
    transport = SchwabOAuthTransport(settings, http_client=client)
    service = SchwabReadinessService(
        repo, transport=transport, schwab_settings=settings
    )

    candidates = await service.list_candidates_for_account("schwab-taxable-1")
    assert len(candidates) == 2

    c1 = next(c for c in candidates if c.schwab_account_hash == "hash-1234")
    assert c1.suggested is True
    assert c1.is_mapped is False

    c2 = next(c for c in candidates if c.schwab_account_hash == "hash-9999")
    assert c2.suggested is False


@pytest.mark.asyncio
async def test_readiness_not_configured(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    service = SchwabReadinessService(repo, schwab_settings=None)
    result = await service.check_account_readiness("schwab-taxable-1")
    assert result.state == SchwabReadinessState.NOT_CONFIGURED
    assert result.ready is False


@pytest.mark.asyncio
async def test_readiness_not_opted_in(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    exec_settings = ExecutionSettings(
        provider="fixture", schwab_execution_enabled=False
    )
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=FakeHttpClient([])),
        execution_settings=exec_settings,
        schwab_settings=settings,
    )
    result = await service.check_account_readiness("schwab-taxable-1")
    assert result.state == SchwabReadinessState.NOT_OPTED_IN
    assert result.ready is False


@pytest.mark.asyncio
async def test_readiness_unmapped(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    exec_settings = ExecutionSettings(provider="schwab", schwab_execution_enabled=True)
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=FakeHttpClient([])),
        execution_settings=exec_settings,
        schwab_settings=settings,
    )
    result = await service.check_account_readiness("schwab-taxable-1")
    assert result.state == SchwabReadinessState.UNMAPPED
    assert result.ready is False


@pytest.mark.asyncio
async def test_readiness_auth_failed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    repo.save_schwab_account_mapping("schwab-taxable-1", "hash-1234", "*1234")

    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    exec_settings = ExecutionSettings(provider="schwab", schwab_execution_enabled=True)
    client = FakeHttpClient([(400, {"error": "invalid_grant"})])
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=client),
        execution_settings=exec_settings,
        schwab_settings=settings,
    )
    result = await service.check_account_readiness("schwab-taxable-1")
    assert result.state == SchwabReadinessState.AUTH_FAILED
    assert result.ready is False


@pytest.mark.asyncio
async def test_readiness_not_entitled(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    repo.save_schwab_account_mapping("schwab-taxable-1", "hash-1234", "*1234")

    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    exec_settings = ExecutionSettings(provider="schwab", schwab_execution_enabled=True)
    client = FakeHttpClient(
        [
            (200, {"access_token": "token"}),
            (403, {"error": "forbidden"}),
        ]
    )
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=client),
        execution_settings=exec_settings,
        schwab_settings=settings,
    )
    result = await service.check_account_readiness("schwab-taxable-1")
    assert result.state == SchwabReadinessState.NOT_ENTITLED
    assert result.ready is False


@pytest.mark.asyncio
async def test_readiness_account_unavailable_when_hash_not_in_list(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    repo.save_schwab_account_mapping("schwab-taxable-1", "hash-different", "*1234")

    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    exec_settings = ExecutionSettings(provider="schwab", schwab_execution_enabled=True)
    client = FakeHttpClient(
        [
            (200, {"access_token": "token"}),
            (200, [{"accountNumber": "12345678", "hashValue": "hash-other"}]),
        ]
    )
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=client),
        execution_settings=exec_settings,
        schwab_settings=settings,
    )
    result = await service.check_account_readiness("schwab-taxable-1")
    assert result.state == SchwabReadinessState.ACCOUNT_UNAVAILABLE
    assert result.ready is False


@pytest.mark.asyncio
async def test_readiness_unsupported_account_restricted(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    repo.save_schwab_account_mapping("schwab-taxable-1", "hash-1234", "*1234")

    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    exec_settings = ExecutionSettings(provider="schwab", schwab_execution_enabled=True)
    client = FakeHttpClient(
        [
            (200, {"access_token": "token"}),
            (200, [{"accountNumber": "12345678", "hashValue": "hash-1234"}]),
            (
                200,
                {
                    "securitiesAccount": {
                        "type": "MARGIN",
                        "isClosingOnlyRestricted": True,
                    }
                },
            ),
        ]
    )
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=client),
        execution_settings=exec_settings,
        schwab_settings=settings,
    )
    result = await service.check_account_readiness("schwab-taxable-1")
    assert result.state == SchwabReadinessState.UNSUPPORTED_ACCOUNT
    assert result.ready is False
    assert "closing" in result.message


@pytest.mark.asyncio
async def test_readiness_ready_when_all_valid(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    repo.save_schwab_account_mapping("schwab-taxable-1", "hash-1234", "*1234")

    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    exec_settings = ExecutionSettings(provider="schwab", schwab_execution_enabled=True)
    client = FakeHttpClient(
        [
            (200, {"access_token": "token"}),
            (200, [{"accountNumber": "12345678", "hashValue": "hash-1234"}]),
            (
                200,
                {
                    "securitiesAccount": {
                        "type": "MARGIN",
                        "currency": "USD",
                        "isClosingOnlyRestricted": False,
                        "isDayTrader": True,
                    }
                },
            ),
        ]
    )
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=client),
        execution_settings=exec_settings,
        schwab_settings=settings,
    )
    result = await service.check_account_readiness("schwab-taxable-1")
    assert result.state == SchwabReadinessState.READY
    assert result.ready is True
    assert result.masked_account_number == "*1234"
    assert result.details == {"account_type": "MARGIN", "is_day_trader": True}
    assert "schwab_account_hash" not in result.to_dict()


@pytest.mark.asyncio
async def test_check_all_readiness_concurrent(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    repo.save_schwab_account_mapping("schwab-taxable-1", "hash-1", "*1234")
    repo.save_schwab_account_mapping("schwab-roth-2", "hash-2", "*5678")

    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    exec_settings = ExecutionSettings(provider="schwab", schwab_execution_enabled=True)

    client = RoutingFakeHttpClient(
        {
            "oauth/token": (200, {"access_token": "token", "expires_in": 1800}),
            "accounts/accountNumbers": (
                200,
                [
                    {"accountNumber": "11111234", "hashValue": "hash-1"},
                    {"accountNumber": "22225678", "hashValue": "hash-2"},
                ],
            ),
            "accounts/hash-1": (
                200,
                {
                    "securitiesAccount": {
                        "type": "CASH",
                        "currency": "USD",
                        "isClosingOnlyRestricted": False,
                        "isDayTrader": False,
                    }
                },
            ),
            "accounts/hash-2": (
                200,
                {
                    "securitiesAccount": {
                        "type": "MARGIN",
                        "currency": "USD",
                        "isClosingOnlyRestricted": False,
                        "isDayTrader": False,
                    }
                },
            ),
        }
    )
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=client),
        execution_settings=exec_settings,
        schwab_settings=settings,
    )

    all_readiness = await service.check_all_readiness()
    assert len(all_readiness) == 2
    ready_ids = {r.account_id for r in all_readiness if r.ready}
    assert ready_ids == {"schwab-taxable-1", "schwab-roth-2"}


def test_schwab_account_eligibility_predicate() -> None:
    assert is_schwab_account_eligible("schwab", "USD") is True
    assert is_schwab_account_eligible("Charles Schwab", "USD") is True
    assert is_schwab_account_eligible("CHARLES SCHWAB", "usd") is True
    assert is_schwab_account_eligible("schwab", "EUR") is False
    assert is_schwab_account_eligible("schwab", "CAD") is False
    assert is_schwab_account_eligible("fidelity", "USD") is False
    assert is_schwab_account_eligible("vanguard", "USD") is False
    assert is_schwab_account_eligible("", "USD") is False
    assert is_schwab_account_eligible("schwab", "") is False


@pytest.mark.asyncio
async def test_readiness_foreign_eur_account_never_ready(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    now = datetime.now(UTC)
    with repo._sessions() as s:
        s.add(
            AccountRecord(
                id="schwab-eur-1",
                provider="schwab",
                label="Schwab International EUR ••••9999",
                account_type="Brokerage",
                currency="EUR",
                refreshed_at=now,
                is_stale=False,
            )
        )
        s.commit()

    with repo._sessions() as s:
        s.add(
            SchwabAccountMappingRecord(
                id="map-eur-1",
                account_id="schwab-eur-1",
                schwab_account_hash="hash-eur",
                masked_account_number="*9999",
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()

    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=FakeHttpClient([])),
        execution_settings=ExecutionSettings(
            provider="schwab", schwab_execution_enabled=True
        ),
        schwab_settings=settings,
    )

    readiness = await service.check_account_readiness("schwab-eur-1")
    assert readiness.state == SchwabReadinessState.UNSUPPORTED_ACCOUNT
    assert readiness.ready is False
    assert "must be a Schwab USD account" in readiness.message
    mapping = repo.get_schwab_account_mapping("schwab-eur-1")
    assert mapping is not None
    assert mapping.schwab_account_hash == "hash-eur"

    with pytest.raises(ValueError, match="not eligible"):
        await service.save_verified_mapping(
            account_id="schwab-eur-1",
            candidate_id="any-id",
            confirmed=True,
        )


@pytest.mark.asyncio
async def test_readiness_broker_contradictory_currency_rejected(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    repo.save_schwab_account_mapping("schwab-taxable-1", "hash-1234", "*1234")

    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    exec_settings = ExecutionSettings(provider="schwab", schwab_execution_enabled=True)
    client = FakeHttpClient(
        [
            (200, {"access_token": "token"}),
            (200, [{"accountNumber": "12345678", "hashValue": "hash-1234"}]),
            (
                200,
                {
                    "securitiesAccount": {
                        "type": "MARGIN",
                        "currency": "EUR",
                        "isClosingOnlyRestricted": False,
                    }
                },
            ),
        ]
    )
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=client),
        execution_settings=exec_settings,
        schwab_settings=settings,
    )
    result = await service.check_account_readiness("schwab-taxable-1")
    assert result.state == SchwabReadinessState.UNSUPPORTED_ACCOUNT
    assert result.ready is False
    assert "currency is unsupported" in result.message
    assert "EUR" not in result.message


@pytest.mark.asyncio
async def test_readiness_broker_unsupported_type_rejected(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    repo.save_schwab_account_mapping("schwab-taxable-1", "hash-1234", "*1234")

    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    exec_settings = ExecutionSettings(provider="schwab", schwab_execution_enabled=True)
    client = FakeHttpClient(
        [
            (200, {"access_token": "token"}),
            (200, [{"accountNumber": "12345678", "hashValue": "hash-1234"}]),
            (
                200,
                {
                    "securitiesAccount": {
                        "type": "FUTURES",
                        "currency": "USD",
                        "isClosingOnlyRestricted": False,
                        "isDayTrader": False,
                    }
                },
            ),
        ]
    )
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=client),
        execution_settings=exec_settings,
        schwab_settings=settings,
    )
    result = await service.check_account_readiness("schwab-taxable-1")
    assert result.state == SchwabReadinessState.UNSUPPORTED_ACCOUNT
    assert result.ready is False
    assert "not supported" in result.message


@pytest.mark.asyncio
async def test_save_verified_mapping_auth_failure_leaves_mapping_untouched(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)
    repo.save_schwab_account_mapping("schwab-taxable-1", "hash-orig", "*1111")

    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    client = FakeHttpClient([(401, {"error": "unauthorized"})])
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=client),
        schwab_settings=settings,
    )

    with pytest.raises(ProviderAuthenticationError):
        await service.save_verified_mapping(
            account_id="schwab-taxable-1",
            candidate_id="any-candidate-id",
            confirmed=True,
        )

    mapping = repo.get_schwab_account_mapping("schwab-taxable-1")
    assert mapping is not None
    assert mapping.schwab_account_hash == "hash-orig"
    assert mapping.masked_account_number == "*1111"


@pytest.mark.asyncio
async def test_save_verified_mapping_unknown_selector_rejected(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)

    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    client = FakeHttpClient(
        [
            (200, {"access_token": "token"}),
            (200, [{"accountNumber": "12345678", "hashValue": "hash-valid"}]),
        ]
    )
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=client),
        schwab_settings=settings,
    )

    with pytest.raises(ValueError, match="not recognized"):
        await service.save_verified_mapping(
            account_id="schwab-taxable-1",
            candidate_id="completely-bogus-selector",
            confirmed=True,
        )
    assert repo.get_schwab_account_mapping("schwab-taxable-1") is None


@pytest.mark.asyncio
async def test_process_restart_invalidates_selector(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)

    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    http_client = RoutingFakeHttpClient(
        {
            "oauth/token": (200, {"access_token": "token", "expires_in": 1800}),
            "accounts/accountNumbers": (
                200,
                [{"accountNumber": "12345678", "hashValue": "hash-val"}],
            ),
        }
    )
    transport = SchwabOAuthTransport(settings, http_client=http_client)

    service1 = SchwabReadinessService(
        repo,
        transport=transport,
        schwab_settings=settings,
        hmac_key=b"process-1-key-32-bytes-long-1234",
    )
    candidates = await service1.list_candidates_for_account("schwab-taxable-1")
    cand_id_p1 = candidates[0].candidate_id

    service2 = SchwabReadinessService(
        repo,
        transport=transport,
        schwab_settings=settings,
        hmac_key=b"process-2-key-32-bytes-diff-5678",
    )
    with pytest.raises(ValueError, match="not recognized"):
        await service2.save_verified_mapping(
            account_id="schwab-taxable-1",
            candidate_id=cand_id_p1,
            confirmed=True,
        )
