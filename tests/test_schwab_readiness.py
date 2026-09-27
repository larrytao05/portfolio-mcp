from datetime import UTC, datetime
from pathlib import Path

import pytest

from portfolio_mcp.config import ExecutionSettings, SchwabSettings
from portfolio_mcp.database import (
    AccountNotFoundError,
    AccountRecord,
    PortfolioRepository,
    SchwabAccountMappingConflictError,
    SchwabAccountMappingRecord,
)
from portfolio_mcp.models import is_schwab_account_eligible
from portfolio_mcp.provider import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
)
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


# --- 1. Config Tests ---


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


# --- 2. Shared Transport & Account Number Redaction Invariant ---


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

    # First fetch fetches token-1
    t1 = await transport.access_token()
    assert t1 == "token-1"
    assert len(client.invocations) == 1

    # Second fetch within TTL reuses cached token without HTTP request
    t2 = await transport.access_token()
    assert t2 == "token-1"
    assert len(client.invocations) == 1

    # Advancing expiry past monotonic time triggers a fresh fetch
    transport._token_expires_at = 0.0
    t3 = await transport.access_token()
    assert t3 == "token-2"
    assert len(client.invocations) == 2


@pytest.mark.asyncio
async def test_schwab_transport_401_retry_invalidation() -> None:
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    client = FakeHttpClient(
        [
            # Initial token fetch
            (200, {"access_token": "expired-token", "expires_in": 1800}),
            # First request call returns 401 Unauthorized
            (401, {"error": "unauthorized"}),
            # Token refresh call after 401 invalidation
            (200, {"access_token": "fresh-token", "expires_in": 1800}),
            # Retried request succeeds
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
    # Return access token, then accountNumbers payload containing full numbers
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

    # Full secret number must NOT be present anywhere in candidate
    # representation or dict
    assert cand.masked_account_number == "*9012"
    assert cand.schwab_account_hash == "hash-redact-test"
    assert raw_secret_number not in str(cand.to_dict())
    assert raw_secret_number not in repr(cand)


# --- 3. Repository Mapping 1-to-1 Tests ---


def test_repository_schwab_mapping_crud_and_uniqueness(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _seed_accounts(repo)

    # Save mapping
    mapping = repo.save_schwab_account_mapping("schwab-taxable-1", "hash-1", "*1234")
    assert mapping.account_id == "schwab-taxable-1"
    assert mapping.schwab_account_hash == "hash-1"
    assert mapping.masked_account_number == "*1234"

    # Enforce 1-to-1: Duplicate hash to different account fails
    with pytest.raises(SchwabAccountMappingConflictError, match="already mapped"):
        repo.save_schwab_account_mapping("schwab-roth-2", "hash-1", "*1234")

    # Non-existent account fails with AccountNotFoundError
    with pytest.raises(AccountNotFoundError, match="does not exist"):
        repo.save_schwab_account_mapping("nonexistent-account", "hash-unique", "*1234")

    # Updating existing account mapping to new hash succeeds
    updated = repo.save_schwab_account_mapping("schwab-taxable-1", "hash-new", "*9999")
    assert updated.schwab_account_hash == "hash-new"
    assert updated.masked_account_number == "*9999"

    # Lookup
    found = repo.get_schwab_account_mapping("schwab-taxable-1")
    assert found is not None
    assert found.schwab_account_hash == "hash-new"

    found_by_hash = repo.get_schwab_account_mapping_by_hash("hash-new")
    assert found_by_hash is not None
    assert found_by_hash.account_id == "schwab-taxable-1"

    all_mappings = repo.list_schwab_account_mappings()
    assert len(all_mappings) == 1

    # Revocation / deletion
    assert repo.delete_schwab_account_mapping("schwab-taxable-1") is True
    assert repo.get_schwab_account_mapping("schwab-taxable-1") is None
    assert repo.delete_schwab_account_mapping("schwab-taxable-1") is False


# --- 4. Candidate Listing & Suggestions Tests ---


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

    # schwab-taxable-1 label is "... ••••1234", matching *1234
    candidates = await service.list_candidates_for_account("schwab-taxable-1")
    assert len(candidates) == 2

    c1 = next(c for c in candidates if c.schwab_account_hash == "hash-1234")
    assert c1.suggested is True
    assert c1.is_mapped is False

    c2 = next(c for c in candidates if c.schwab_account_hash == "hash-9999")
    assert c2.suggested is False


# --- 5. Readiness State Machine Tests ---


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
    # access token ok, but accountNumbers endpoint returns 403
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
                        "isClosingOnlyRestricted": False,
                    }
                },
            ),
            "accounts/hash-2": (
                200,
                {
                    "securitiesAccount": {
                        "type": "MARGIN",
                        "isClosingOnlyRestricted": False,
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

    # Pre-existing mapping in DB remains saved, but readiness must be false
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
    # Pre-existing mapping remains in database
    mapping = repo.get_schwab_account_mapping("schwab-eur-1")
    assert mapping is not None
    assert mapping.schwab_account_hash == "hash-eur"

    # Attempting to save mapping for ineligible account is rejected
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
                        "currency": "EUR",  # contradictory currency
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
    assert "currency 'EUR' is not supported" in result.message


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
                        "type": "FUTURES",  # unsupported type
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
    assert "FUTURES" in result.message


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

    # Repository mapping remains untouched
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

    # Process 1
    service1 = SchwabReadinessService(
        repo,
        transport=transport,
        schwab_settings=settings,
        hmac_key=b"process-1-key-32-bytes-long-1234",
    )
    candidates = await service1.list_candidates_for_account("schwab-taxable-1")
    cand_id_p1 = candidates[0].candidate_id

    # Process 2 (simulated restart with fresh HMAC secret)
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
