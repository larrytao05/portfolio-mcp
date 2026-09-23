from urllib.parse import parse_qs, urlparse

import pytest

from portfolio_mcp.config import SchwabMarketDataSettings
from portfolio_mcp.schwab_market_data import SchwabMarketDataProvider
from portfolio_mcp.schwab_oauth import run_authorization


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
        SchwabMarketDataSettings(
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
        SchwabMarketDataSettings(
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
