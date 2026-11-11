import asyncio
import base64
import json
import os
import re
import ssl
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

import certifi

from portfolio_mcp.config import SchwabSettings
from portfolio_mcp.provider import (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTLSConfigurationError,
    ProviderTLSVerificationError,
    ProviderUnavailableError,
    SchwabAuthorizationCodeRejectedError,
    SchwabClientAuthenticationError,
    SchwabReauthorizationRequiredError,
)

_GrantType = Literal["refresh_token", "authorization_code"]


def _validate_explicit_trust_paths() -> tuple[list[Path], list[Path]]:
    certificate_files: list[Path] = []
    certificate_directories: list[Path] = []

    if "SSL_CERT_FILE" in os.environ:
        raw_file = os.environ["SSL_CERT_FILE"]
        if not raw_file:
            raise ProviderTLSConfigurationError()
        certificate_file = Path(raw_file)
        try:
            if not certificate_file.is_file():
                raise ProviderTLSConfigurationError()
            with certificate_file.open("rb") as source:
                if not source.read(1):
                    raise ProviderTLSConfigurationError()
        except OSError:
            raise ProviderTLSConfigurationError() from None
        certificate_files.append(certificate_file)

    if "SSL_CERT_DIR" in os.environ:
        raw_directories = os.environ["SSL_CERT_DIR"]
        if not raw_directories:
            raise ProviderTLSConfigurationError()
        for raw_directory in raw_directories.split(os.pathsep):
            if not raw_directory:
                raise ProviderTLSConfigurationError()
            certificate_directory = Path(raw_directory)
            try:
                if not certificate_directory.is_dir():
                    raise ProviderTLSConfigurationError()
                with os.scandir(certificate_directory) as entries:
                    if not any(
                        entry.is_file()
                        and re.fullmatch(r"[0-9a-fA-F]{8}\.\d+", entry.name)
                        for entry in entries
                    ):
                        raise ProviderTLSConfigurationError()
            except OSError:
                raise ProviderTLSConfigurationError() from None
            certificate_directories.append(certificate_directory)

    return certificate_files, certificate_directories


def _schwab_ssl_context() -> ssl.SSLContext:
    try:
        certificate_files, certificate_directories = _validate_explicit_trust_paths()
        context = ssl.create_default_context()
        for certificate_file in certificate_files:
            context.load_verify_locations(cafile=str(certificate_file))
        for certificate_directory in certificate_directories:
            context.load_verify_locations(capath=str(certificate_directory))
        context.load_verify_locations(cafile=certifi.where())
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise ProviderTLSConfigurationError()
        return context
    except ProviderTLSConfigurationError:
        raise
    except (OSError, ssl.SSLError, ValueError):
        raise ProviderTLSConfigurationError() from None


def _raise_token_error(
    status: int, response: object, grant_type: _GrantType, context: str
) -> None:
    if 200 <= status < 300:
        return
    if status == 429 or status <= 0 or status >= 500 or status == 403:
        raise_for_status(status, context)

    oauth_error = response.get("error") if isinstance(response, Mapping) else None
    if not isinstance(oauth_error, str):
        oauth_error = None

    if oauth_error == "invalid_client" and status in (400, 401):
        raise SchwabClientAuthenticationError()
    if oauth_error == "invalid_grant" and status == 400:
        if grant_type == "refresh_token":
            raise SchwabReauthorizationRequiredError()
        raise SchwabAuthorizationCodeRejectedError()
    if status == 401:
        raise ProviderAuthenticationError("Unable to authenticate with Schwab")
    if status == 400:
        raise ProviderResponseError(f"{context} rejected the token request")
    raise_for_status(status, context)


class SchwabHttpClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, object]: ...


def decode_body(body: bytes) -> object:
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return None


class UrllibSchwabHttpClient:
    def __init__(self, context: str = "Schwab") -> None:
        self._context = context
        self._ssl_context: ssl.SSLContext | None = None

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, object]:
        request = Request(url, data=body, headers=headers, method=method)
        try:
            if self._ssl_context is None:
                self._ssl_context = _schwab_ssl_context()
            with urlopen(request, timeout=15, context=self._ssl_context) as response:
                return response.status, decode_body(response.read())
        except HTTPError as error:
            return error.code, decode_body(error.read())
        except ssl.SSLCertVerificationError:
            raise ProviderTLSVerificationError() from None
        except URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                raise ProviderTLSVerificationError() from None
            raise ProviderUnavailableError(
                f"{self._context} is temporarily unavailable"
            ) from None


def raise_for_status(status: int, context: str = "Schwab") -> None:
    if 200 <= status < 300:
        return
    if status == 401:
        raise ProviderAuthenticationError("Unable to authenticate with Schwab")
    if status == 403:
        raise ProviderAuthorizationError(f"{context} access is not authorized")
    if status == 429:
        raise ProviderRateLimitError(f"{context} rate limit reached; try again later")
    if status <= 0 or status >= 500:
        raise ProviderUnavailableError(f"{context} is temporarily unavailable")
    raise ProviderResponseError(f"{context} returned an unexpected response")


class SchwabOAuthTransport:
    DEFAULT_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
    DEFAULT_AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"

    def __init__(
        self,
        settings: SchwabSettings,
        *,
        http_client: SchwabHttpClient | None = None,
        token_url: str = DEFAULT_TOKEN_URL,
        auth_url: str = DEFAULT_AUTH_URL,
        context: str = "Schwab",
    ) -> None:
        self._settings = settings
        self._http_client = http_client or UrllibSchwabHttpClient(context=context)
        self._token_url = token_url
        self._auth_url = auth_url
        self._context = context
        self._cached_access_token: str | None = None
        self._token_expires_at: float = 0.0

    @property
    def settings(self) -> SchwabSettings:
        return self._settings

    @property
    def http_client(self) -> SchwabHttpClient:
        return self._http_client

    @property
    def context(self) -> str:
        return self._context

    def authorization_url(self) -> str:
        parameters = urlencode(
            {
                "client_id": self._settings.client_id,
                "response_type": "code",
                "redirect_uri": self._settings.callback_url,
            }
        )
        return f"{self._auth_url}?{parameters}"

    def authorization_code_from_redirect_url(self, redirect_url: str) -> str:
        redirect = urlparse(redirect_url)
        callback = urlparse(self._settings.callback_url)
        redirect_base = urlunparse(
            (redirect.scheme, redirect.netloc, redirect.path, "", "", "")
        ).rstrip("/")
        callback_base = urlunparse(
            (callback.scheme, callback.netloc, callback.path, "", "", "")
        ).rstrip("/")
        if redirect_base != callback_base:
            raise ProviderResponseError(
                "Authorization redirect did not match the configured callback URL"
            )

        code = parse_qs(redirect.query).get("code", [""])[0]
        if not code:
            raise ProviderResponseError("Authorization redirect did not include a code")
        return code

    async def exchange_authorization_code(self, code: str) -> str:
        status, response = await self.http_request(
            "POST",
            self._token_url,
            self.token_headers(),
            urlencode(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._settings.callback_url,
                }
            ).encode(),
        )
        _raise_token_error(status, response, "authorization_code", self._context)
        raise_for_status(status, self._context)
        if not isinstance(response, Mapping) or not response.get("refresh_token"):
            raise ProviderResponseError(
                f"{self._context} returned an unexpected response"
            )
        return str(response["refresh_token"])

    async def access_token(self, force_refresh: bool = False) -> str:
        if (
            self._cached_access_token is not None
            and not force_refresh
            and time.monotonic() < self._token_expires_at
        ):
            return self._cached_access_token
        body = urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": self._settings.refresh_token,
            }
        ).encode()
        status, response = await self.http_request(
            "POST",
            self._token_url,
            self.token_headers(),
            body,
        )
        _raise_token_error(status, response, "refresh_token", self._context)
        raise_for_status(status, self._context)
        if not isinstance(response, Mapping) or not response.get("access_token"):
            raise ProviderResponseError(
                f"{self._context} returned an unexpected response"
            )
        token = str(response["access_token"])
        expires_in = 1800
        raw_expires_in = response.get("expires_in")
        if isinstance(raw_expires_in, (int, float, str)):
            try:
                expires_in = int(raw_expires_in)
            except ValueError:
                expires_in = 1800
        buffer = min(60, max(0, expires_in // 2))
        self._cached_access_token = token
        self._token_expires_at = time.monotonic() + max(1, expires_in - buffer)
        return token

    def token_headers(self) -> dict[str, str]:
        credentials = f"{self._settings.client_id}:{self._settings.client_secret}"
        authorization = base64.b64encode(credentials.encode()).decode()
        return {
            "Authorization": f"Basic {authorization}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

    async def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        additional_headers: dict[str, str] | None = None,
    ) -> tuple[int, object]:
        token = await self.access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if additional_headers:
            headers.update(additional_headers)
        status, response = await self.http_request(method, url, headers, body)
        if status == 401 and self._cached_access_token is not None:
            self._cached_access_token = None
            self._token_expires_at = 0.0
            token = await self.access_token(force_refresh=True)
            headers["Authorization"] = f"Bearer {token}"
            status, response = await self.http_request(method, url, headers, body)
        return status, response

    async def http_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, object]:
        try:
            return await asyncio.to_thread(
                self._http_client.request,
                method,
                url,
                headers,
                body,
            )
        except TimeoutError:
            raise ProviderUnavailableError(
                f"{self._context} is temporarily unavailable"
            ) from None
