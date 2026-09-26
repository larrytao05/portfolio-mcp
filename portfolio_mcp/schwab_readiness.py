import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from portfolio_mcp.config import ExecutionSettings, SchwabSettings
from portfolio_mcp.database import PortfolioRepository
from portfolio_mcp.provider import (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from portfolio_mcp.schwab_transport import (
    SchwabOAuthTransport,
    raise_for_status,
)


class SchwabReadinessState(StrEnum):
    READY = "ready"
    NOT_CONFIGURED = "not_configured"
    NOT_OPTED_IN = "not_opted_in"
    AUTH_FAILED = "auth_failed"
    NOT_ENTITLED = "not_entitled"
    UNMAPPED = "unmapped"
    ACCOUNT_UNAVAILABLE = "account_unavailable"
    UNSUPPORTED_ACCOUNT = "unsupported_account"


@dataclass(frozen=True)
class SchwabAccountCandidate:
    schwab_account_hash: str
    masked_account_number: str
    is_mapped: bool
    mapped_to_account_id: str | None
    suggested: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schwab_account_hash": self.schwab_account_hash,
            "masked_account_number": self.masked_account_number,
            "is_mapped": self.is_mapped,
            "mapped_to_account_id": self.mapped_to_account_id,
            "suggested": self.suggested,
        }


@dataclass(frozen=True)
class SchwabAccountReadiness:
    account_id: str
    state: SchwabReadinessState
    ready: bool
    schwab_account_hash: str | None
    masked_account_number: str | None
    message: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "state": self.state.value,
            "ready": self.ready,
            "schwab_account_hash": self.schwab_account_hash,
            "masked_account_number": self.masked_account_number,
            "message": self.message,
            "details": self.details or {},
        }


def extract_last_four(account_number_str: str) -> str:
    digits = "".join(c for c in account_number_str if c.isdigit())
    return digits[-4:] if len(digits) >= 4 else digits


def mask_account_number(account_number_str: str) -> str:
    suffix = extract_last_four(account_number_str)
    return f"*{suffix}" if suffix else account_number_str


_extract_last_four = extract_last_four


class SchwabReadinessService:
    DEFAULT_TRADER_API_URL = "https://api.schwabapi.com/trader/v1"

    def __init__(
        self,
        repository: PortfolioRepository,
        transport: SchwabOAuthTransport | None = None,
        execution_settings: ExecutionSettings | None = None,
        schwab_settings: SchwabSettings | None = None,
        trader_api_url: str = DEFAULT_TRADER_API_URL,
    ) -> None:
        self._repository = repository
        self._transport = transport
        self._execution_settings = execution_settings or ExecutionSettings()
        self._schwab_settings = schwab_settings
        self._trader_api_url = trader_api_url

    @property
    def is_configured(self) -> bool:
        return self._transport is not None and self._schwab_settings is not None

    async def list_candidates_for_account(
        self, account_id: str
    ) -> list[SchwabAccountCandidate]:
        stored_acc = self._repository.stored_account(account_id)
        if stored_acc is None:
            raise ValueError(f"Account '{account_id}' does not exist")

        if self._transport is None or self._schwab_settings is None:
            raise ProviderUnavailableError("Schwab credentials are not configured")

        status, body = await self._transport.request(
            "GET", f"{self._trader_api_url}/accounts/accountNumbers"
        )
        raise_for_status(status, context="Schwab Trader API")
        if not isinstance(body, list):
            raise ProviderResponseError(
                "Schwab Trader API returned an unexpected account numbers payload"
            )

        mappings = self._repository.list_schwab_account_mappings()
        mappings_by_hash = {m.schwab_account_hash: m for m in mappings}

        # Check account label for digits to suggest
        label_digits = "".join(c for c in stored_acc.account.label if c.isdigit())
        label_suffix = label_digits[-4:] if len(label_digits) >= 4 else label_digits

        candidates: list[SchwabAccountCandidate] = []
        for item in body:
            if not isinstance(item, Mapping):
                continue
            raw_acc = str(item.get("accountNumber") or "").strip()
            hash_val = str(item.get("hashValue") or "").strip()
            if not raw_acc or not hash_val:
                continue

            # Strip full account number immediately to masked suffix
            suffix = _extract_last_four(raw_acc)
            masked = f"*{suffix}"

            mapped_entry = mappings_by_hash.get(hash_val)
            is_mapped = mapped_entry is not None
            mapped_to_account_id = mapped_entry.account_id if mapped_entry else None

            # Suggestion matches last 4 digits if not mapped to another account
            suggested = False
            if bool(label_suffix and suffix and suffix == label_suffix):
                if not is_mapped or mapped_to_account_id == account_id:
                    suggested = True

            candidates.append(
                SchwabAccountCandidate(
                    schwab_account_hash=hash_val,
                    masked_account_number=masked,
                    is_mapped=is_mapped,
                    mapped_to_account_id=mapped_to_account_id,
                    suggested=suggested,
                )
            )

        return candidates

    async def check_account_readiness(self, account_id: str) -> SchwabAccountReadiness:
        # 1. Verify account exists in local repository
        account_detail = self._repository.stored_account(account_id)
        if account_detail is None:
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                ready=False,
                schwab_account_hash=None,
                masked_account_number=None,
                message=f"Local account '{account_id}' was not found",
            )

        # 2. Check configuration
        if self._schwab_settings is None or self._transport is None:
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.NOT_CONFIGURED,
                ready=False,
                schwab_account_hash=None,
                masked_account_number=None,
                message="Schwab API credentials are not configured",
            )

        # 3. Check opt-in
        if not self._execution_settings.permits_schwab_execution:
            opt_in_reason = (
                "Execution provider is not set to 'schwab'"
                if self._execution_settings.provider != "schwab"
                else "SCHWAB_EXECUTION_ENABLED is not set to true"
            )
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.NOT_OPTED_IN,
                ready=False,
                schwab_account_hash=None,
                masked_account_number=None,
                message=f"Schwab production execution is not enabled ({opt_in_reason})",
            )

        # 4. Check explicit mapping
        mapping = self._repository.get_schwab_account_mapping(account_id)
        if mapping is None:
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.UNMAPPED,
                ready=False,
                schwab_account_hash=None,
                masked_account_number=None,
                message=(
                    f"Account '{account_id}' has not been mapped "
                    "to a Schwab account hash"
                ),
            )

        # 5. Check authentication (token refresh)
        try:
            await self._transport.access_token()
        except ProviderAuthenticationError as exc:
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.AUTH_FAILED,
                ready=False,
                schwab_account_hash=mapping.schwab_account_hash,
                masked_account_number=mapping.masked_account_number,
                message=f"Schwab authentication failed: {exc}",
            )
        except (ProviderUnavailableError, ProviderResponseError) as exc:
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                ready=False,
                schwab_account_hash=mapping.schwab_account_hash,
                masked_account_number=mapping.masked_account_number,
                message=f"Schwab service is unavailable: {exc}",
            )

        # 6. Check entitlement and account list
        try:
            status, body = await self._transport.request(
                "GET", f"{self._trader_api_url}/accounts/accountNumbers"
            )
            raise_for_status(status, context="Schwab Trader API")
        except ProviderAuthorizationError:
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.NOT_ENTITLED,
                ready=False,
                schwab_account_hash=mapping.schwab_account_hash,
                masked_account_number=mapping.masked_account_number,
                message=(
                    "Schwab Accounts and Trading product is not authorized or entitled"
                ),
            )
        except ProviderAuthenticationError as exc:
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.AUTH_FAILED,
                ready=False,
                schwab_account_hash=mapping.schwab_account_hash,
                masked_account_number=mapping.masked_account_number,
                message=f"Schwab authentication failed: {exc}",
            )
        except Exception as exc:
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                ready=False,
                schwab_account_hash=mapping.schwab_account_hash,
                masked_account_number=mapping.masked_account_number,
                message=f"Failed to fetch Schwab account numbers: {exc}",
            )

        if not isinstance(body, list):
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                ready=False,
                schwab_account_hash=mapping.schwab_account_hash,
                masked_account_number=mapping.masked_account_number,
                message="Unexpected account list response from Schwab",
            )

        known_hashes: set[str] = set()
        for item in body:
            if isinstance(item, Mapping):
                h = item.get("hashValue")
                if h:
                    known_hashes.add(str(h))

        if mapping.schwab_account_hash not in known_hashes:
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                ready=False,
                schwab_account_hash=mapping.schwab_account_hash,
                masked_account_number=mapping.masked_account_number,
                message=(
                    "Mapped Schwab account hash was not found in "
                    "active Schwab account list"
                ),
            )

        # 7. Check account detail & supported USD account type
        try:
            status, detail_body = await self._transport.request(
                "GET", f"{self._trader_api_url}/accounts/{mapping.schwab_account_hash}"
            )
            if status == 404:
                return SchwabAccountReadiness(
                    account_id=account_id,
                    state=SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                    ready=False,
                    schwab_account_hash=mapping.schwab_account_hash,
                    masked_account_number=mapping.masked_account_number,
                    message="Schwab account detail returned 404 Not Found",
                )
            raise_for_status(status, context="Schwab Trader API")
        except ProviderAuthorizationError:
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.NOT_ENTITLED,
                ready=False,
                schwab_account_hash=mapping.schwab_account_hash,
                masked_account_number=mapping.masked_account_number,
                message="Schwab account detail access is not authorized",
            )
        except Exception as exc:
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                ready=False,
                schwab_account_hash=mapping.schwab_account_hash,
                masked_account_number=mapping.masked_account_number,
                message=f"Failed to read Schwab account detail: {exc}",
            )

        if not isinstance(detail_body, Mapping):
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                ready=False,
                schwab_account_hash=mapping.schwab_account_hash,
                masked_account_number=mapping.masked_account_number,
                message="Invalid account detail response from Schwab",
            )

        sec_account = detail_body.get("securitiesAccount")
        if not isinstance(sec_account, Mapping):
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                ready=False,
                schwab_account_hash=mapping.schwab_account_hash,
                masked_account_number=mapping.masked_account_number,
                message="Schwab account details missing securitiesAccount",
            )

        if sec_account.get("isClosingOnlyRestricted") is True:
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.UNSUPPORTED_ACCOUNT,
                ready=False,
                schwab_account_hash=mapping.schwab_account_hash,
                masked_account_number=mapping.masked_account_number,
                message="Schwab account is restricted to closing transactions only",
            )

        account_type = str(sec_account.get("type") or "").upper()
        if account_type not in ("MARGIN", "CASH", "INDIVIDUAL"):
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.UNSUPPORTED_ACCOUNT,
                ready=False,
                schwab_account_hash=mapping.schwab_account_hash,
                masked_account_number=mapping.masked_account_number,
                message=(
                    f"Schwab account type '{account_type}' is "
                    "not supported for execution"
                ),
            )

        return SchwabAccountReadiness(
            account_id=account_id,
            state=SchwabReadinessState.READY,
            ready=True,
            schwab_account_hash=mapping.schwab_account_hash,
            masked_account_number=mapping.masked_account_number,
            message="Schwab execution is ready for this account",
            details={
                "account_type": account_type,
                "is_day_trader": bool(sec_account.get("isDayTrader", False)),
            },
        )

    async def check_all_readiness(self) -> list[SchwabAccountReadiness]:
        accounts = self._repository.list_accounts()
        if not accounts:
            return []
        return list(
            await asyncio.gather(
                *(self.check_account_readiness(a.account.id) for a in accounts)
            )
        )
