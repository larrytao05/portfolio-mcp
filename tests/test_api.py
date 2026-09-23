from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from portfolio_mcp.api import create_app
from portfolio_mcp.database import PortfolioRepository
from portfolio_mcp.fixtures import FixtureMarketDataProvider, FixturePortfolioProvider
from portfolio_mcp.models import Account, HoldingsSnapshot, Instrument, Position
from portfolio_mcp.provider import ProviderAuthenticationError, ProviderUnavailableError


class FailingFixtureProvider(FixturePortfolioProvider):
    async def list_accounts(self) -> list[Account]:
        raise ProviderUnavailableError("Fixture provider is unavailable")


class AuthenticationFailingFixtureProvider(FixturePortfolioProvider):
    async def list_accounts(self) -> list[Account]:
        raise ProviderAuthenticationError("Fixture credentials expired")


class PartiallyFailingFixtureProvider(FixturePortfolioProvider):
    async def get_holdings(self, account_id: str) -> HoldingsSnapshot:
        if account_id == "schwab-taxable-demo":
            raise ProviderUnavailableError("Schwab fixture data is unavailable")
        return await super().get_holdings(account_id)


class IncompleteCapabilityFixtureProvider(FixturePortfolioProvider):
    async def get_account_capabilities(self, account_ids: list[str]):
        capabilities = await super().get_account_capabilities(account_ids)
        return [
            capability
            for capability in capabilities
            if capability.account_id != "schwab-taxable-demo"
        ]


class RemovedAccountFixtureProvider(FixturePortfolioProvider):
    async def list_accounts(self) -> list[Account]:
        return [
            account
            for account in await super().list_accounts()
            if account.provider == "Fidelity"
        ]


class FailingMarketDataProvider(FixtureMarketDataProvider):
    async def search_instruments(self, query: str) -> list[Instrument]:
        raise ProviderUnavailableError("Market data provider is unavailable")


def create_client(tmp_path, clock=None) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    return TestClient(
        create_app(
            FixturePortfolioProvider(),
            database_url=database_url,
            clock=clock,
        )
    )


def test_health(tmp_path) -> None:
    client = create_client(tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_trading_settings_default_to_blocking_and_use_optimistic_concurrency(
    tmp_path,
) -> None:
    client = create_client(tmp_path)

    default = client.get("/api/trading/settings")

    assert default.status_code == 200
    assert default.json()["settings"] == {
        "live_trading_enabled": False,
        "kill_switch_active": True,
        "max_order_shares": None,
        "max_order_notional_usd": None,
        "updated_at": None,
        "version": 0,
        "effective_state": "Trading blocked",
    }

    invalid = client.put(
        "/api/trading/settings",
        json={
            "live_trading_enabled": True,
            "kill_switch_active": False,
            "max_order_shares": None,
            "max_order_notional_usd": None,
            "version": 0,
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"

    saved = client.put(
        "/api/trading/settings",
        json={
            "live_trading_enabled": True,
            "kill_switch_active": False,
            "max_order_shares": "10",
            "max_order_notional_usd": "1000.50",
            "version": 0,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["settings"]["version"] == 1
    assert saved.json()["settings"]["effective_state"] == "Trading enabled"

    conflict = client.put(
        "/api/trading/settings",
        json={
            "live_trading_enabled": False,
            "kill_switch_active": True,
            "max_order_shares": "10",
            "max_order_notional_usd": "1000.50",
            "version": 0,
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "settings_conflict"

    invalid_version = client.put(
        "/api/trading/settings",
        json={
            "live_trading_enabled": False,
            "kill_switch_active": True,
            "max_order_shares": None,
            "max_order_notional_usd": None,
            "version": -1,
        },
    )
    assert invalid_version.status_code == 422
    assert invalid_version.json()["error"]["code"] == "validation_error"

    invalid_boolean = client.put(
        "/api/trading/settings",
        json={
            "live_trading_enabled": "yes",
            "kill_switch_active": True,
            "max_order_shares": None,
            "max_order_notional_usd": None,
            "version": 1,
        },
    )
    assert invalid_boolean.status_code == 422
    assert invalid_boolean.json()["error"]["code"] == "validation_error"


def test_order_draft_api_cannot_bypass_default_trading_safeguards(tmp_path) -> None:
    client = create_client(tmp_path)

    response = client.post(
        "/api/order-drafts",
        json={
            "account_id": "schwab-taxable-demo",
            "instrument_id": "us-etf:VTI",
            "side": "buy",
            "order_type": "limit",
            "quantity": "1",
            "limit_price": "300",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "trading_disabled"


def test_refresh_persists_accounts(tmp_path) -> None:
    client = create_client(tmp_path)

    response = client.post("/api/refresh")

    assert response.status_code == 200
    refresh = response.json()["refresh"]
    assert refresh["status"] == "success"
    assert refresh["accounts_refreshed"] == 2
    assert refresh["positions_refreshed"] == 9
    assert refresh["daily_snapshots_recorded"] == 2
    assert refresh["warnings"] == []
    assert refresh["provider_outcomes"] == [
        {
            "provider": "Fidelity",
            "status": "success",
            "accounts_refreshed": 1,
            "stale_accounts": 0,
            "excluded_accounts": 0,
            "warning": None,
        },
        {
            "provider": "Schwab",
            "status": "success",
            "accounts_refreshed": 1,
            "stale_accounts": 0,
            "excluded_accounts": 0,
            "warning": None,
        },
    ]
    assert refresh["started_at"].endswith("+00:00")

    response = client.get("/api/accounts")

    assert response.status_code == 200
    assert {account["provider"] for account in response.json()["accounts"]} == {
        "Fidelity",
        "Schwab",
    }


def test_trading_status_reads_saved_capabilities_and_stales_after_failure(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    client = TestClient(
        create_app(
            FixturePortfolioProvider(),
            database_url=database_url,
            clock=lambda: datetime(2026, 9, 12, 20, 0, tzinfo=UTC),
        )
    )

    client.post("/api/refresh")

    status = client.get("/api/trading/status")
    account = client.get("/api/accounts/schwab-taxable-demo/capabilities")

    assert status.status_code == 200
    assert [
        (item["provider"], item["state"]) for item in status.json()["providers"]
    ] == [
        ("Fidelity", "healthy"),
        ("Schwab", "healthy"),
    ]
    assert status.json()["accounts"][0]["blocks"][0]["code"] == "monitoring_only"
    assert account.json()["capability"]["is_trade_capable"] is True

    failing = TestClient(
        create_app(FailingFixtureProvider(), database_url=database_url)
    )
    failing.post("/api/refresh")

    stale = failing.get("/api/accounts/schwab-taxable-demo/capabilities")
    assert stale.json()["capability"]["is_stale"] is True
    assert stale.json()["capability"]["is_trade_capable"] is False
    assert failing.get("/api/accounts/missing/capabilities").status_code == 404


def test_old_capability_observation_is_not_trade_capable(tmp_path) -> None:
    client = create_client(tmp_path)

    client.post("/api/refresh")

    capability = client.get("/api/accounts/schwab-taxable-demo/capabilities")
    assert capability.json()["capability"]["is_stale"] is True
    assert capability.json()["capability"]["is_trade_capable"] is False


def test_incomplete_capability_response_stales_previous_observation(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    initial = TestClient(
        create_app(
            FixturePortfolioProvider(),
            database_url=database_url,
            clock=lambda: datetime(2026, 9, 12, 20, 0, tzinfo=UTC),
        )
    )
    initial.post("/api/refresh")

    incomplete = TestClient(
        create_app(
            IncompleteCapabilityFixtureProvider(),
            database_url=database_url,
            clock=lambda: datetime(2026, 9, 12, 20, 0, tzinfo=UTC),
        )
    )
    incomplete.post("/api/refresh")

    capability = incomplete.get("/api/accounts/schwab-taxable-demo/capabilities")
    assert capability.json()["capability"]["is_stale"] is True
    assert capability.json()["capability"]["is_trade_capable"] is False


def test_capability_ages_out_after_its_observation_window(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    refreshed = TestClient(
        create_app(
            FixturePortfolioProvider(),
            database_url=database_url,
            clock=lambda: datetime(2026, 9, 12, 20, 0, tzinfo=UTC),
        )
    )
    refreshed.post("/api/refresh")

    aged = TestClient(
        create_app(
            FixturePortfolioProvider(),
            database_url=database_url,
            clock=lambda: datetime(2026, 9, 14, 20, 0, tzinfo=UTC),
        )
    )

    capability = aged.get("/api/accounts/schwab-taxable-demo/capabilities")
    assert capability.json()["capability"]["is_stale"] is True
    assert capability.json()["capability"]["is_trade_capable"] is False


def test_removed_account_is_excluded_from_current_capability_responses(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    initial = TestClient(
        create_app(
            FixturePortfolioProvider(),
            database_url=database_url,
            clock=lambda: datetime(2026, 9, 12, 20, 0, tzinfo=UTC),
        )
    )
    initial.post("/api/refresh")

    removed = TestClient(
        create_app(
            RemovedAccountFixtureProvider(),
            database_url=database_url,
            clock=lambda: datetime(2026, 9, 12, 21, 0, tzinfo=UTC),
        )
    )
    removed.post("/api/refresh")

    status = removed.get("/api/trading/status").json()
    assert [item["account_id"] for item in status["accounts"]] == ["fidelity-roth-demo"]
    assert (
        removed.get("/api/accounts/schwab-taxable-demo/capabilities").status_code == 404
    )


def test_partial_refresh_stales_existing_capability_and_auth_has_safe_recovery(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    successful = TestClient(
        create_app(FixturePortfolioProvider(), database_url=database_url)
    )
    successful.post("/api/refresh")

    partial = TestClient(
        create_app(PartiallyFailingFixtureProvider(), database_url=database_url)
    )
    partial.post("/api/refresh")
    capability = partial.get("/api/accounts/schwab-taxable-demo/capabilities")

    assert capability.json()["capability"]["is_stale"] is True
    assert capability.json()["capability"]["is_trade_capable"] is False

    auth = TestClient(
        create_app(AuthenticationFailingFixtureProvider(), database_url=database_url)
    )
    auth.post("/api/refresh")
    health = auth.get("/api/trading/status").json()["providers"]

    assert {item["state"] for item in health} == {"authentication_required"}
    assert all(
        item["blocks"][0]["recovery_action"] == "reconnect_provider" for item in health
    )


def test_first_refresh_auth_failure_returns_recovery_status(tmp_path) -> None:
    client = TestClient(
        create_app(
            AuthenticationFailingFixtureProvider(),
            database_url=f"sqlite:///{tmp_path / 'portfolio.db'}",
        )
    )

    client.post("/api/refresh")

    providers = client.get("/api/trading/status").json()["providers"]
    assert providers[0]["provider"] == "Portfolio provider"
    assert providers[0]["state"] == "authentication_required"
    assert providers[0]["observed_at"] is not None
    assert providers[0]["last_success_at"] is None
    assert providers[0]["blocks"] == [
        {
            "code": "authentication_required",
            "message": "Provider authentication is required.",
            "recovery_action": "reconnect_provider",
        }
    ]


def test_refresh_imports_a_reverse_chronological_activity_feed(tmp_path) -> None:
    client = create_client(tmp_path)

    client.post("/api/refresh")

    response = client.get("/api/activity?limit=2")

    assert response.status_code == 200
    body = response.json()
    assert body["pagination"] == {"limit": 2, "offset": 0, "total": 6}
    assert [activity["occurred_on"] for activity in body["activities"]] == [
        "2026-08-20",
        "2026-08-14",
    ]
    activity = body["activities"][0]
    assert activity["account"] == {
        "id": "fidelity-roth-demo",
        "label": "Fidelity Roth IRA ••••9046",
    }
    assert activity["provider"] == "Fidelity"
    assert activity["imported_at"].endswith("+00:00")
    assert "provider_transaction_id" not in activity
    assert "raw" not in activity


def test_activity_imports_are_idempotent_and_filterable(tmp_path) -> None:
    client = create_client(tmp_path)

    client.post("/api/refresh")
    client.post("/api/refresh")

    response = client.get(
        "/api/activity?account_id=schwab-taxable-demo&provider=Schwab"
        "&type=buy&symbol=NVDA&start_date=2026-08-01&end_date=2026-08-01"
    )

    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 1
    assert (
        response.json()["activities"][0]["description"] == "Bought NVIDIA Corporation"
    )


def test_activity_returns_an_empty_page_when_nothing_matches(tmp_path) -> None:
    client = create_client(tmp_path)

    client.post("/api/refresh")

    response = client.get("/api/activity?symbol=NOT-A-SYMBOL")

    assert response.status_code == 200
    assert response.json() == {
        "activities": [],
        "pagination": {"limit": 50, "offset": 0, "total": 0},
    }


def test_persisted_accounts_survive_an_app_restart(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    first_client = TestClient(
        create_app(FixturePortfolioProvider(), database_url=database_url)
    )
    first_client.post("/api/refresh")
    second_client = TestClient(
        create_app(FixturePortfolioProvider(), database_url=database_url)
    )

    response = second_client.get("/api/accounts")

    assert len(response.json()["accounts"]) == 2
    positions = second_client.get("/api/accounts/schwab-taxable-demo/positions")
    assert len(positions.json()["positions"]) == 4
    assert positions.json()["positions"][0]["as_of"] == "2026-08-29"
    daily_values = second_client.get("/api/accounts/schwab-taxable-demo/daily-values")
    assert daily_values.json()["daily_values"][0]["value"] == "4799.97"
    latest_refresh = second_client.get("/api/refreshes/latest")
    assert latest_refresh.json()["refresh"]["status"] == "success"
    activity = second_client.get("/api/activity")
    assert activity.json()["pagination"]["total"] == 6


def test_account_detail_uses_persisted_account_metadata_and_holdings(tmp_path) -> None:
    refreshed_at = datetime(2026, 9, 12, 14, 0, tzinfo=UTC)
    client = create_client(tmp_path, clock=lambda: refreshed_at)
    client.post("/api/refresh")

    response = client.get("/api/accounts/schwab-taxable-demo")

    assert response.status_code == 200
    account = response.json()["account"]
    assert account["label"] == "Schwab Taxable ••••4821"
    assert account["provider"] == "Schwab"
    assert account["account_type"] == "taxable_brokerage"
    assert account["currency"] == "USD"
    assert account["refreshed_at"] == "2026-09-12T14:00:00+00:00"
    assert account["as_of"] == "2026-08-29"
    assert account["balances"] == {
        "market_value": "4799.97",
        "cost_basis": "4383.00",
        "currency": "USD",
    }
    assert account["positions"][0]["gain_loss"] == "60.00"


def test_account_detail_preserves_empty_and_unavailable_values(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    repository = PortfolioRepository(database_url)
    refreshed_at = datetime(2020, 1, 2, tzinfo=UTC)
    empty_account = Account(
        id="empty-account",
        provider="Fixture",
        label="Empty ••••0001",
        account_type="taxable_brokerage",
        currency="USD",
    )
    unavailable_account = Account(
        id="unavailable-account",
        provider="Fixture",
        label="Unavailable ••••0002",
        account_type="roth_ira",
        currency="USD",
    )
    repository.save_refresh(
        [
            HoldingsSnapshot(
                account=empty_account,
                as_of=date(2020, 1, 2),
                positions=(),
            ),
            HoldingsSnapshot(
                account=unavailable_account,
                as_of=date(2020, 1, 2),
                positions=(
                    Position(
                        account_id=unavailable_account.id,
                        symbol="UNKNOWN",
                        name="Unavailable values fund",
                        asset_class="fund",
                        quantity=Decimal("1"),
                        current_price=None,
                        market_value=None,
                        cost_basis=None,
                        currency="USD",
                    ),
                ),
            ),
        ],
        refreshed_at,
        refreshed_at,
        date(2020, 1, 2),
    )
    client = TestClient(
        create_app(FixturePortfolioProvider(), database_url=database_url)
    )

    empty = client.get("/api/accounts/empty-account")
    unavailable = client.get("/api/accounts/unavailable-account")

    assert empty.status_code == 200
    assert empty.json()["account"]["positions"] == []
    assert empty.json()["account"]["balances"]["market_value"] == "0"
    assert unavailable.status_code == 200
    detail = unavailable.json()["account"]
    assert detail["refreshed_at"] == "2020-01-02T00:00:00+00:00"
    assert detail["balances"]["market_value"] is None
    assert detail["balances"]["cost_basis"] is None
    assert detail["positions"][0]["current_price"] is None
    assert detail["positions"][0]["cost_basis"] is None
    assert detail["positions"][0]["gain_loss"] is None


def test_refresh_replaces_the_same_new_york_daily_snapshot(tmp_path) -> None:
    times = iter(
        [
            datetime(2026, 9, 12, 4, 0, tzinfo=UTC),
            datetime(2026, 9, 12, 4, 1, tzinfo=UTC),
            datetime(2026, 9, 12, 20, 0, tzinfo=UTC),
            datetime(2026, 9, 12, 20, 1, tzinfo=UTC),
        ]
    )
    client = create_client(tmp_path, clock=lambda: next(times))

    client.post("/api/refresh")
    client.post("/api/refresh")

    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'portfolio.db'}")
    values = repository.daily_values("schwab-taxable-demo")
    assert values is not None
    assert len(values) == 1
    assert values[0].value == Decimal("4799.97")
    assert values[0].recorded_at == datetime(2026, 9, 12, 20, 1, tzinfo=UTC)


def test_refresh_does_not_fabricate_missing_new_york_daily_snapshots(tmp_path) -> None:
    times = iter(
        [
            datetime(2026, 9, 12, 4, 0, tzinfo=UTC),
            datetime(2026, 9, 12, 4, 1, tzinfo=UTC),
            datetime(2026, 9, 14, 4, 0, tzinfo=UTC),
            datetime(2026, 9, 14, 4, 1, tzinfo=UTC),
        ]
    )
    client = create_client(tmp_path, clock=lambda: next(times))

    client.post("/api/refresh")
    client.post("/api/refresh")

    repository = PortfolioRepository(f"sqlite:///{tmp_path / 'portfolio.db'}")
    values = repository.daily_values("schwab-taxable-demo")

    assert values is not None
    assert [value.snapshot_date for value in values] == [
        date(2026, 9, 12),
        date(2026, 9, 14),
    ]


def test_database_preserves_high_precision_decimals(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    repository = PortfolioRepository(database_url)
    timestamp = datetime(2026, 9, 12, 14, 0, tzinfo=UTC)
    account = Account(
        id="precise-account",
        provider="Fixture",
        label="Fixture ••••1234",
        account_type="taxable_brokerage",
        currency="USD",
    )
    precise_quantity = Decimal("0.123456789123456789")
    repository.save_refresh(
        [
            HoldingsSnapshot(
                account=account,
                as_of=date(2026, 9, 12),
                positions=(
                    Position(
                        account_id=account.id,
                        symbol="VTI",
                        name="Vanguard Total Stock Market ETF",
                        asset_class="equity_etf",
                        quantity=precise_quantity,
                        current_price=Decimal("333.333333333333333333"),
                        market_value=Decimal("41.152263041152262999958847737"),
                        cost_basis=Decimal("40.000000000000000001"),
                        currency="USD",
                    ),
                ),
            )
        ],
        timestamp,
        timestamp,
        date(2026, 9, 12),
    )

    reopened_repository = PortfolioRepository(database_url)
    positions = reopened_repository.list_positions(account.id)

    assert positions is not None
    assert positions[0].position.quantity == precise_quantity
    assert positions[0].position.current_price == Decimal("333.333333333333333333")
    assert positions[0].position.market_value == Decimal(
        "41.152263041152262999958847737"
    )
    assert positions[0].position.cost_basis == Decimal("40.000000000000000001")


def test_failed_refresh_is_persisted_without_replacing_saved_data(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    successful_client = TestClient(
        create_app(FixturePortfolioProvider(), database_url=database_url)
    )
    successful_client.post("/api/refresh")
    failing_client = TestClient(
        create_app(FailingFixtureProvider(), database_url=database_url)
    )

    response = failing_client.post("/api/refresh")

    assert response.status_code == 200
    assert response.json()["refresh"]["status"] == "failed"
    assert len(failing_client.get("/api/accounts").json()["accounts"]) == 2
    latest = failing_client.get("/api/refreshes/latest").json()["refresh"]
    assert latest["status"] == "failed"
    assert latest["error_code"] == "provider_error"
    assert latest["error_message"] == "Fixture provider is unavailable"
    assert latest["warnings"] == [
        "Fidelity data is stale; last successful data is shown.",
        "Schwab data is stale; last successful data is shown.",
    ]
    assert all(
        account["is_stale"]
        for account in failing_client.get("/api/accounts").json()["accounts"]
    )


def test_partial_refresh_keeps_failed_provider_data_stale(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    initial_time = datetime(2026, 9, 12, 14, 0, tzinfo=UTC)
    partial_time = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)
    initial_client = TestClient(
        create_app(
            FixturePortfolioProvider(),
            database_url=database_url,
            clock=lambda: initial_time,
        )
    )
    initial_client.post("/api/refresh")
    partial_client = TestClient(
        create_app(
            PartiallyFailingFixtureProvider(),
            database_url=database_url,
            clock=lambda: partial_time,
        )
    )

    response = partial_client.post("/api/refresh")

    assert response.status_code == 200
    refresh = response.json()["refresh"]
    assert refresh["status"] == "partial"
    assert refresh["accounts_refreshed"] == 1
    assert refresh["positions_refreshed"] == 5
    assert refresh["warnings"] == [
        "Schwab data is stale; last successful data is shown."
    ]
    assert refresh["provider_outcomes"][1] == {
        "provider": "Schwab",
        "status": "failed",
        "accounts_refreshed": 0,
        "stale_accounts": 1,
        "excluded_accounts": 0,
        "warning": "Schwab data is stale; last successful data is shown.",
    }

    accounts = {
        account["id"]: account
        for account in partial_client.get("/api/accounts").json()["accounts"]
    }
    assert accounts["fidelity-roth-demo"]["is_stale"] is False
    assert (
        accounts["fidelity-roth-demo"]["source_refreshed_at"]
        == partial_time.isoformat()
    )
    assert accounts["schwab-taxable-demo"]["is_stale"] is True
    assert (
        accounts["schwab-taxable-demo"]["source_refreshed_at"]
        == initial_time.isoformat()
    )

    positions = partial_client.get(
        "/api/accounts/schwab-taxable-demo/positions"
    ).json()["positions"]
    assert len(positions) == 4
    assert all(position["is_stale"] for position in positions)
    assert all(
        position["source_refreshed_at"] == initial_time.isoformat()
        for position in positions
    )


def test_partial_refresh_discloses_a_provider_without_saved_data(tmp_path) -> None:
    client = TestClient(
        create_app(
            PartiallyFailingFixtureProvider(),
            database_url=f"sqlite:///{tmp_path / 'portfolio.db'}",
        )
    )

    response = client.post("/api/refresh")

    assert response.status_code == 200
    refresh = response.json()["refresh"]
    assert refresh["status"] == "partial"
    assert refresh["provider_outcomes"][1] == {
        "provider": "Schwab",
        "status": "failed",
        "accounts_refreshed": 0,
        "stale_accounts": 0,
        "excluded_accounts": 1,
        "warning": "Schwab data is unavailable and excluded from totals.",
    }
    assert {
        account["provider"]
        for account in client.get("/api/accounts").json()["accounts"]
    } == {"Fidelity"}


def test_search_instruments_returns_an_empty_list_for_no_match(tmp_path) -> None:
    client = create_client(tmp_path)

    response = client.get("/api/instruments/search", params={"query": "not-a-symbol"})

    assert response.status_code == 200
    assert response.json() == {"instruments": []}


def test_quote_preserves_unavailable_market_data_fields(tmp_path) -> None:
    client = create_client(tmp_path)

    response = client.get("/api/instruments/us-fund:FIXTURE_UNAVAILABLE/quote")

    assert response.status_code == 200
    assert response.json() == {
        "quote": {
            "instrument": {
                "id": "us-fund:FIXTURE_UNAVAILABLE",
                "symbol": "FIXTURE_UNAVAILABLE",
                "name": "Fixture Unavailable Price Fund",
                "asset_class": "mutual_fund",
                "exchange": None,
                "currency": "USD",
            },
            "source": "fixture_market_data",
            "observed_at": "2026-09-12T20:00:00+00:00",
            "last_price": None,
            "bid_price": None,
            "ask_price": None,
            "currency": "USD",
        }
    }


def test_market_data_provider_failure_has_a_safe_api_response(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'portfolio.db'}"
    client = TestClient(
        create_app(
            FixturePortfolioProvider(),
            market_data_provider=FailingMarketDataProvider(),
            database_url=database_url,
        )
    )

    response = client.get("/api/instruments/search", params={"query": "VTI"})

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "provider_error",
            "message": "Market data provider is unavailable",
        }
    }
