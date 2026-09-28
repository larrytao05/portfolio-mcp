from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient

from portfolio_mcp.api.app import create_app
from portfolio_mcp.config import ExecutionSettings, SchwabSettings
from portfolio_mcp.database import (
    AccountRecord,
    PortfolioRepository,
)
from portfolio_mcp.fixtures import FixturePortfolioProvider
from portfolio_mcp.provider import ProviderRateLimitError, ProviderResponseError
from portfolio_mcp.schwab_readiness import SchwabReadinessService
from portfolio_mcp.schwab_transport import SchwabOAuthTransport


class RoutingFakeHttpClient:
    def __init__(self, routes: dict[str, tuple[int, object] | Exception]) -> None:
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
                if isinstance(response, Exception):
                    raise response
                return response
        raise RuntimeError(f"No route for {method} {url}")


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


def test_api_schwab_mapping_crud_flow(tmp_path: Path) -> None:
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    exec_settings = ExecutionSettings(provider="schwab", schwab_execution_enabled=True)
    db_file = tmp_path / "api_test.db"
    repo = PortfolioRepository(f"sqlite:///{db_file}")
    _seed_accounts(repo)

    http_client = RoutingFakeHttpClient(
        {
            "oauth/token": (200, {"access_token": "token", "expires_in": 1800}),
            "accounts/accountNumbers": (
                200,
                [
                    {"accountNumber": "999991234", "hashValue": "hash-abc"},
                    {"accountNumber": "888885678", "hashValue": "hash-def"},
                ],
            ),
        }
    )
    transport = SchwabOAuthTransport(settings, http_client=http_client)
    service = SchwabReadinessService(
        repo,
        transport=transport,
        execution_settings=exec_settings,
        schwab_settings=settings,
    )
    app = create_app(
        FixturePortfolioProvider(),
        database_url=f"sqlite:///{db_file}",
        schwab_readiness_service=service,
        schwab_settings=settings,
        execution_settings=exec_settings,
    )
    client = TestClient(app)

    # Initially empty mappings
    resp = client.get("/api/schwab/mapping")
    assert resp.status_code == 200
    assert resp.json() == {"mappings": []}

    # Getting non-existent returns 404
    resp = client.get("/api/schwab/mapping/schwab-taxable-1")
    assert resp.status_code == 404

    # Fetch candidates for schwab-taxable-1
    cands_resp = client.get(
        "/api/schwab/mapping/candidates?account_id=schwab-taxable-1"
    )
    assert cands_resp.status_code == 200
    cands = cands_resp.json()["candidates"]
    assert len(cands) == 2
    cand1 = cands[0]
    cand_id = cand1["candidate_id"]
    assert cand1["masked_account_number"] == "*1234"
    assert "schwab_account_hash" not in cand1

    # Saving mapping without confirmation fails with 400
    resp = client.post(
        "/api/schwab/mapping/schwab-taxable-1",
        json={
            "candidate_id": cand_id,
            "confirmed": False,
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid Schwab mapping request"

    # Saving mapping with confirmation succeeds
    resp = client.post(
        "/api/schwab/mapping/schwab-taxable-1",
        json={
            "candidate_id": cand_id,
            "confirmed": True,
        },
    )
    assert resp.status_code == 200
    created = resp.json()["mapping"]
    assert created["account_id"] == "schwab-taxable-1"
    assert created["masked_account_number"] == "*1234"
    assert "schwab_account_hash" not in created

    # Getting single mapping
    resp = client.get("/api/schwab/mapping/schwab-taxable-1")
    assert resp.status_code == 200
    assert resp.json()["mapping"]["account_id"] == "schwab-taxable-1"
    assert resp.json()["mapping"]["masked_account_number"] == "*1234"
    assert "schwab_account_hash" not in resp.json()["mapping"]

    # 1-to-1 violation: saving same underlying hash to schwab-roth-2
    # fails with 409 Conflict
    roth_cands = client.get(
        "/api/schwab/mapping/candidates?account_id=schwab-roth-2"
    ).json()["candidates"]
    cand_roth_dup = roth_cands[0]  # maps to hash-abc
    resp = client.post(
        "/api/schwab/mapping/schwab-roth-2",
        json={
            "candidate_id": cand_roth_dup["candidate_id"],
            "confirmed": True,
        },
    )
    assert resp.status_code == 409
    assert "already mapped" in resp.json()["detail"]

    # Non-existent account returns 404
    resp = client.post(
        "/api/schwab/mapping/nonexistent-acc",
        json={
            "candidate_id": cand_id,
            "confirmed": True,
        },
    )
    assert resp.status_code == 404

    # Deleting mapping
    resp = client.delete("/api/schwab/mapping/schwab-taxable-1")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "account_id": "schwab-taxable-1"}

    # Second delete returns 404
    resp = client.delete("/api/schwab/mapping/schwab-taxable-1")
    assert resp.status_code == 404


def test_api_schwab_readiness_endpoints(tmp_path: Path) -> None:
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    exec_settings = ExecutionSettings(provider="schwab", schwab_execution_enabled=True)
    db_file = tmp_path / "api_test2.db"
    repo = PortfolioRepository(f"sqlite:///{db_file}")
    _seed_accounts(repo)
    repo.save_schwab_account_mapping("schwab-taxable-1", "hash-1234", "*1234")

    http_client = RoutingFakeHttpClient(
        {
            "oauth/token": (200, {"access_token": "token", "expires_in": 1800}),
            "accounts/accountNumbers": (
                200,
                [{"accountNumber": "12345678", "hashValue": "hash-1234"}],
            ),
            "accounts/hash-1234": (
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
        }
    )
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=http_client),
        execution_settings=exec_settings,
        schwab_settings=settings,
    )
    app = create_app(
        FixturePortfolioProvider(),
        database_url=f"sqlite:///{db_file}",
        schwab_readiness_service=service,
        schwab_settings=settings,
        execution_settings=exec_settings,
    )
    test_client = TestClient(app)

    # Check single account readiness
    resp = test_client.get("/api/schwab/readiness/schwab-taxable-1")
    assert resp.status_code == 200
    readiness = resp.json()["readiness"]
    assert readiness["state"] == "ready"
    assert readiness["ready"] is True
    assert readiness["masked_account_number"] == "*1234"
    assert "schwab_account_hash" not in readiness


def test_api_schwab_mapping_candidate_validation(tmp_path: Path) -> None:
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    exec_settings = ExecutionSettings(provider="schwab", schwab_execution_enabled=True)
    db_file = tmp_path / "cand_val.db"
    repo = PortfolioRepository(f"sqlite:///{db_file}")
    _seed_accounts(repo)

    http_client = RoutingFakeHttpClient(
        {
            "oauth/token": (200, {"access_token": "token", "expires_in": 1800}),
            "accounts/accountNumbers": (
                200,
                [{"accountNumber": "12345678", "hashValue": "valid-hash"}],
            ),
        }
    )
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=http_client),
        execution_settings=exec_settings,
        schwab_settings=settings,
    )
    app = create_app(
        FixturePortfolioProvider(),
        database_url=f"sqlite:///{db_file}",
        schwab_readiness_service=service,
        schwab_settings=settings,
        execution_settings=exec_settings,
    )
    test_client = TestClient(app)

    candidates = test_client.get(
        "/api/schwab/mapping/candidates?account_id=schwab-taxable-1"
    ).json()["candidates"]
    valid_candidate_id = candidates[0]["candidate_id"]

    # Saving with recognized candidate hash succeeds
    resp = test_client.post(
        "/api/schwab/mapping/schwab-taxable-1",
        json={
            "candidate_id": valid_candidate_id,
            "confirmed": True,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["mapping"]["account_id"] == "schwab-taxable-1"
    assert "schwab_account_hash" not in resp.json()["mapping"]

    # Saving with unrecognized candidate hash fails with 400
    resp = test_client.post(
        "/api/schwab/mapping/schwab-roth-2",
        json={
            "candidate_id": "unrecognized-candidate-id",
            "confirmed": True,
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid Schwab mapping request"


def test_api_schwab_candidates_error_handling(tmp_path: Path) -> None:
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    exec_settings = ExecutionSettings(provider="schwab", schwab_execution_enabled=True)
    db_file = tmp_path / "cand_err.db"
    repo = PortfolioRepository(f"sqlite:///{db_file}")
    _seed_accounts(repo)

    http_client = RoutingFakeHttpClient(
        {
            "oauth/token": (200, {"access_token": "token", "expires_in": 1800}),
            "accounts/accountNumbers": (401, {"error": "unauthorized"}),
        }
    )
    service = SchwabReadinessService(
        repo,
        transport=SchwabOAuthTransport(settings, http_client=http_client),
        execution_settings=exec_settings,
        schwab_settings=settings,
    )
    app = create_app(
        FixturePortfolioProvider(),
        database_url=f"sqlite:///{db_file}",
        schwab_readiness_service=service,
        schwab_settings=settings,
        execution_settings=exec_settings,
    )
    test_client = TestClient(app)

    # 1. Non-existent account returns 404 (not 500)
    resp = test_client.get(
        "/api/schwab/mapping/candidates?account_id=nonexistent-account"
    )
    assert resp.status_code == 404

    # 2. Provider auth failure returns 403 (not 500)
    resp = test_client.get("/api/schwab/mapping/candidates?account_id=schwab-taxable-1")
    assert resp.status_code == 403


def test_json_responses_contain_no_full_account_numbers_or_hashes(
    tmp_path: Path,
) -> None:
    raw_secret_number = "987654321098"
    raw_hash = "super-secret-schwab-hash-999"

    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    exec_settings = ExecutionSettings(provider="schwab", schwab_execution_enabled=True)
    db_file = tmp_path / "redact_json.db"
    repo = PortfolioRepository(f"sqlite:///{db_file}")
    _seed_accounts(repo)

    http_client = RoutingFakeHttpClient(
        {
            "oauth/token": (200, {"access_token": "token", "expires_in": 1800}),
            "accounts/accountNumbers": (
                200,
                [{"accountNumber": raw_secret_number, "hashValue": raw_hash}],
            ),
            f"accounts/{raw_hash}": (
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
        transport=SchwabOAuthTransport(settings, http_client=http_client),
        execution_settings=exec_settings,
        schwab_settings=settings,
    )
    app = create_app(
        FixturePortfolioProvider(),
        database_url=f"sqlite:///{db_file}",
        schwab_readiness_service=service,
        schwab_settings=settings,
        execution_settings=exec_settings,
    )
    client = TestClient(app)

    # 1. Candidates
    cands_resp = client.get(
        "/api/schwab/mapping/candidates?account_id=schwab-taxable-1"
    )
    assert cands_resp.status_code == 200
    assert raw_secret_number not in cands_resp.text
    assert raw_hash not in cands_resp.text
    cand = cands_resp.json()["candidates"][0]

    # 2. Save mapping
    save_resp = client.post(
        "/api/schwab/mapping/schwab-taxable-1",
        json={"candidate_id": cand["candidate_id"], "confirmed": True},
    )
    assert save_resp.status_code == 200
    assert raw_secret_number not in save_resp.text
    assert raw_hash not in save_resp.text

    # 3. Get mapping
    get_resp = client.get("/api/schwab/mapping/schwab-taxable-1")
    assert get_resp.status_code == 200
    assert raw_secret_number not in get_resp.text
    assert raw_hash not in get_resp.text

    # 4. List mappings
    list_resp = client.get("/api/schwab/mapping")
    assert list_resp.status_code == 200
    assert raw_secret_number not in list_resp.text
    assert raw_hash not in list_resp.text

    # 5. Account readiness
    readiness_resp = client.get("/api/schwab/readiness/schwab-taxable-1")
    assert readiness_resp.status_code == 200
    assert raw_secret_number not in readiness_resp.text
    assert raw_hash not in readiness_resp.text

    # 6. All readiness
    all_readiness_resp = client.get("/api/schwab/readiness")
    assert all_readiness_resp.status_code == 200
    assert raw_secret_number not in all_readiness_resp.text
    assert raw_hash not in all_readiness_resp.text


def test_schwab_mapping_api_redacts_provider_errors_and_maps_rate_limits(
    tmp_path: Path,
) -> None:
    settings = SchwabSettings(client_id="cid", client_secret="csec", refresh_token="rt")
    database_url = f"sqlite:///{tmp_path / 'api-errors.db'}"
    repo = PortfolioRepository(database_url)
    _seed_accounts(repo)
    provider_secret = "private-provider-payload-marker"

    class LeakyFailureService:
        candidate_error: Exception = ProviderResponseError(provider_secret)

        async def list_candidates_for_account(self, account_id: str) -> list[object]:
            raise self.candidate_error

        async def save_verified_mapping(
            self, *, account_id: str, candidate_id: str, confirmed: bool
        ) -> object:
            raise ProviderRateLimitError(provider_secret)

    service = LeakyFailureService()
    app = create_app(
        FixturePortfolioProvider(),
        database_url=database_url,
        schwab_readiness_service=cast(SchwabReadinessService, service),
        schwab_settings=settings,
    )
    client = TestClient(app)

    candidates = client.get(
        "/api/schwab/mapping/candidates?account_id=schwab-taxable-1"
    )
    assert candidates.status_code == 502
    assert candidates.json()["detail"] == "Schwab returned an invalid response"
    assert provider_secret not in candidates.text

    service.candidate_error = RuntimeError(provider_secret)
    unexpected = client.get(
        "/api/schwab/mapping/candidates?account_id=schwab-taxable-1"
    )
    assert unexpected.status_code == 500
    assert unexpected.json()["detail"] == "Unable to process Schwab mapping"
    assert provider_secret not in unexpected.text

    save = client.post(
        "/api/schwab/mapping/schwab-taxable-1",
        json={"candidate_id": "candidate", "confirmed": True},
    )
    assert save.status_code == 429
    assert save.json()["detail"] == "Schwab rate limit reached"
    assert provider_secret not in save.text
