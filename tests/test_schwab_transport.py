import asyncio
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from portfolio_mcp.config import SchwabSettings
from portfolio_mcp.provider import (
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTLSConfigurationError,
    ProviderTLSVerificationError,
    ProviderUnavailableError,
    SchwabAuthorizationCodeRejectedError,
    SchwabClientAuthenticationError,
    SchwabReauthorizationRequiredError,
)
from portfolio_mcp.schwab_transport import (
    SchwabOAuthTransport,
    UrllibSchwabHttpClient,
    _schwab_ssl_context,
)


def _settings() -> SchwabSettings:
    return SchwabSettings(
        client_id="client-id",
        client_secret="secret-sentinel",
        refresh_token="refresh-sentinel",
    )


def test_ssl_context_adds_certifi_and_keeps_verification(monkeypatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(
        ssl, "create_default_context", lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    )

    context = _schwab_ssl_context()

    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.get_ca_certs()


@pytest.mark.parametrize("invalid_path", ["", "/missing/ca.pem"])
def test_invalid_explicit_certificate_file_fails_as_configuration_error(
    monkeypatch, invalid_path: str
) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", invalid_path)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    with pytest.raises(ProviderTLSConfigurationError):
        _schwab_ssl_context()


def test_invalid_explicit_certificate_pem_fails_as_configuration_error(
    monkeypatch, tmp_path: Path
) -> None:
    invalid_pem = tmp_path / "invalid.pem"
    invalid_pem.write_text("not a certificate")
    monkeypatch.setenv("SSL_CERT_FILE", str(invalid_pem))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    with pytest.raises(ProviderTLSConfigurationError):
        _schwab_ssl_context()


def test_direct_certificate_verification_error_is_sanitized(monkeypatch) -> None:
    import portfolio_mcp.schwab_transport as transport_module

    def raise_verification_error(*args, **kwargs):
        raise ssl.SSLCertVerificationError("secret certificate details")

    monkeypatch.setattr(transport_module, "urlopen", raise_verification_error)
    monkeypatch.setattr(
        transport_module, "_schwab_ssl_context", ssl.create_default_context
    )

    with pytest.raises(ProviderTLSVerificationError) as caught:
        UrllibSchwabHttpClient().request("GET", "https://example.test", {})

    assert "secret certificate details" not in str(caught.value)


class _FakeHTTPClient:
    def __init__(self, responses: list[tuple[int, object]]) -> None:
        self.responses = iter(responses)
        self.calls = 0

    def request(self, method: str, url: str, headers: dict[str, str], body=None):
        self.calls += 1
        return next(self.responses)


def test_fresh_transport_refreshes_twice_across_client_restarts() -> None:
    async def run() -> None:
        first_client = _FakeHTTPClient(
            [
                (200, {"access_token": "first-access", "expires_in": 1800}),
                (200, {"value": 1}),
                (200, {"value": 2}),
            ]
        )
        first = SchwabOAuthTransport(_settings(), http_client=first_client)

        await first.request("GET", "https://example.test/data")
        await first.request("GET", "https://example.test/data")
        assert first_client.calls == 3

        second_client = _FakeHTTPClient(
            [
                (200, {"access_token": "second-access", "expires_in": 1800}),
                (200, {"value": 3}),
            ]
        )
        second = SchwabOAuthTransport(_settings(), http_client=second_client)
        await second.request("GET", "https://example.test/data")
        assert second_client.calls == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    ("grant", "status", "body", "error_type", "safe_text"),
    [
        (
            "refresh_token",
            400,
            {"error": "invalid_grant", "error_description": "secret-sentinel"},
            SchwabReauthorizationRequiredError,
            "rejected the refresh grant",
        ),
        (
            "authorization_code",
            400,
            {"error": "invalid_grant", "error_description": "refresh-sentinel"},
            SchwabAuthorizationCodeRejectedError,
            "rejected the authorization code",
        ),
        (
            "refresh_token",
            401,
            {"error": "invalid_client", "error_description": "secret-sentinel"},
            SchwabClientAuthenticationError,
            "rejected client authentication",
        ),
        (
            "refresh_token",
            400,
            {"error": "other_error", "error_description": "secret-sentinel"},
            ProviderResponseError,
            "rejected the token request",
        ),
        (
            "refresh_token",
            429,
            {"error": "rate", "error_description": "secret-sentinel"},
            ProviderRateLimitError,
            "rate limit",
        ),
        (
            "refresh_token",
            503,
            {"error": "temporary", "error_description": "secret-sentinel"},
            ProviderUnavailableError,
            "temporarily unavailable",
        ),
    ],
)
def test_token_errors_keep_safe_grant_classification(
    grant: str,
    status: int,
    body: object,
    error_type: type[Exception],
    safe_text: str,
) -> None:
    transport = SchwabOAuthTransport(
        _settings(), http_client=_FakeHTTPClient([(status, body)])
    )

    async def request_grant() -> None:
        if grant == "refresh_token":
            await transport.access_token()
        else:
            await transport.exchange_authorization_code("code-sentinel")

    with pytest.raises(error_type, match=safe_text) as caught:
        asyncio.run(request_grant())
    assert "secret-sentinel" not in str(caught.value)
    assert "refresh-sentinel" not in str(caught.value)
    assert "code-sentinel" not in str(caught.value)


def _openssl(*arguments: str) -> None:
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.fail("OpenSSL is required for concrete Schwab HTTPS regression tests")
    assert openssl is not None
    result = subprocess.run(
        [openssl, *arguments], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def _make_tls_certificates(tmp_path: Path) -> tuple[Path, Path, Path]:
    ca_key = tmp_path / "test-ca.key"
    ca_cert = tmp_path / "test-ca.pem"
    server_key = tmp_path / "server.key"
    server_csr = tmp_path / "server.csr"
    server_cert = tmp_path / "server.pem"
    extensions = tmp_path / "server.ext"
    extensions.write_text("subjectAltName=DNS:localhost\n")
    _openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(ca_key),
        "-out",
        str(ca_cert),
        "-days",
        "1",
        "-subj",
        "/CN=Portfolio MCP test CA",
    )
    _openssl(
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(server_key),
        "-out",
        str(server_csr),
        "-subj",
        "/CN=localhost",
    )
    _openssl(
        "x509",
        "-req",
        "-in",
        str(server_csr),
        "-CA",
        str(ca_cert),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-out",
        str(server_cert),
        "-days",
        "1",
        "-extfile",
        str(extensions),
    )
    return ca_cert, server_cert, server_key


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def local_https_server(tmp_path: Path):
    ca_cert, server_cert, server_key = _make_tls_certificates(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(server_cert, server_key)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            ca_cert,
            f"https://localhost:{server.server_port}",
            f"https://127.0.0.1:{server.server_port}",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_urllib_client_uses_explicit_trust_and_verifies_hostname(
    monkeypatch, local_https_server
) -> None:
    ca_cert, matching_url, wrong_hostname_url = local_https_server
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_cert))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    status, body = UrllibSchwabHttpClient().request("GET", matching_url, {})
    assert (status, body) == (200, None)

    with pytest.raises(ProviderTLSVerificationError):
        UrllibSchwabHttpClient().request("GET", wrong_hostname_url, {})


def test_urllib_client_rejects_untrusted_issuer(
    monkeypatch, tmp_path, local_https_server
) -> None:
    _, matching_url, _ = local_https_server
    wrong_ca = tmp_path / "unrelated-ca.pem"
    wrong_key = tmp_path / "unrelated-ca.key"
    _openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(wrong_key),
        "-out",
        str(wrong_ca),
        "-days",
        "1",
        "-subj",
        "/CN=Unrelated test CA",
    )
    monkeypatch.setenv("SSL_CERT_FILE", str(wrong_ca))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    with pytest.raises(ProviderTLSVerificationError):
        UrllibSchwabHttpClient().request("GET", matching_url, {})
