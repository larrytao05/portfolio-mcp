import asyncio
import base64
import json
import time
from collections.abc import Mapping
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from portfolio_mcp.config import SchwabSettings
from portfolio_mcp.provider import (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)


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

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, object]:
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=15) as response:
                return response.status, decode_body(response.read())
        except HTTPError as error:
            return error.code, decode_body(error.read())
        except URLError:
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
        if status == 400:
            raise ProviderAuthenticationError(
                "Refresh token was rejected by Schwab; run "
                "`uv run --env-file .env python -m portfolio_mcp.schwab_oauth` "
                "to replace it"
            )
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
