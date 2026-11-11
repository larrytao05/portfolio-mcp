from urllib.parse import parse_qs, urlparse

import pytest

from portfolio_mcp.config import SchwabSettings
from portfolio_mcp.provider import (
    ProviderConfigurationError,
    SchwabReauthorizationRequiredError,
)
from portfolio_mcp.schwab_market_data import SchwabMarketDataProvider
from portfolio_mcp.schwab_oauth import main, run_authorization


class RecordingHttpClient:
    def __init__(self, responses: list[tuple[int, object]]) -> None:
        self._responses = iter(responses)
        self.requests: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, object]:
        self.requests.append((method, url, headers, body))
        return next(self._responses)


@pytest.mark.asyncio
async def test_schwab_authorization_uses_the_configured_callback_url() -> None:
    client = RecordingHttpClient([(200, {"refresh_token": "new-refresh-token"})])
    provider = SchwabMarketDataProvider(
        SchwabSettings(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="old-refresh-token",
            callback_url="https://127.0.0.1:8182",
        ),
        http_client=client,
    )

    authorization = urlparse(provider.authorization_url())

    assert authorization.scheme == "https"
    assert authorization.netloc == "api.schwabapi.com"
    assert authorization.path == "/v1/oauth/authorize"
    assert parse_qs(authorization.query) == {
        "client_id": ["client-id"],
        "response_type": ["code"],
        "redirect_uri": ["https://127.0.0.1:8182"],
    }

    code = provider.authorization_code_from_redirect_url(
        "https://127.0.0.1:8182/?code=authorization%2Bcode&session=ignored"
    )
    refresh_token = await provider.exchange_authorization_code(code)

    assert refresh_token == "new-refresh-token"
    method, url, _, body = client.requests[0]
    assert method == "POST"
    assert url == "https://api.schwabapi.com/v1/oauth/token"
    assert parse_qs((body or b"").decode()) == {
        "grant_type": ["authorization_code"],
        "code": ["authorization+code"],
        "redirect_uri": ["https://127.0.0.1:8182"],
    }


@pytest.mark.asyncio
async def test_authorization_cli_prints_a_new_refresh_token_without_writing_files() -> (
    None
):
    provider = SchwabMarketDataProvider(
        SchwabSettings(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="old-refresh-token",
            callback_url="https://127.0.0.1:8182",
        ),
        http_client=RecordingHttpClient([(200, {"refresh_token": "new-token"})]),
    )
    output: list[str] = []

    await run_authorization(
        provider,
        read_redirect_url=lambda: "https://127.0.0.1:8182/?code=new-code",
        write=output.append,
    )

    assert output[0].startswith("Open this URL in a browser: https://")
    assert output[-1] == "SCHWAB_REFRESH_TOKEN=new-token"


def test_authorization_cli_reports_safe_provider_error(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import portfolio_mcp.schwab_oauth as oauth_module

    monkeypatch.setattr(
        SchwabSettings,
        "from_environment",
        classmethod(
            lambda cls: SchwabSettings(
                client_id="test-id",
                client_secret="test-secret",
                refresh_token="test-refresh",
            )
        ),
    )

    async def reject_authorization(*args, **kwargs) -> None:
        raise SchwabReauthorizationRequiredError()

    monkeypatch.setattr(oauth_module, "run_authorization", reject_authorization)

    with pytest.raises(SystemExit, match="1"):
        main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "schwab_reauthorization_required" in captured.err
    assert "Traceback" not in captured.err
    assert "test-secret" not in captured.err
    assert "test-refresh" not in captured.err


def test_authorization_cli_reports_safe_configuration_error(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def missing_settings(cls):
        raise ProviderConfigurationError("SCHWAB_CLIENT_ID is required")

    monkeypatch.setattr(
        SchwabSettings,
        "from_environment",
        classmethod(missing_settings),
    )

    with pytest.raises(SystemExit, match="1"):
        main()

    captured = capsys.readouterr()
    assert "provider_configuration_error" in captured.err
    assert "SCHWAB_CLIENT_ID is required" in captured.err
    assert "Traceback" not in captured.err
