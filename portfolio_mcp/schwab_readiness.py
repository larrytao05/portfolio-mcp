import asyncio
import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from portfolio_mcp.config import ExecutionSettings, SchwabSettings
from portfolio_mcp.database import (
    AccountNotFoundError,
    PortfolioRepository,
    StoredSchwabAccountMapping,
)
from portfolio_mcp.models import (
    Account,
    is_schwab_account_eligible,
    is_schwab_account_masked,
)
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

_PROCESS_HMAC_KEY: bytes = secrets.token_bytes(32)
_SCHWAB_ACCOUNT_NUMBER = re.compile(r"[0-9]{5,}\Z")


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
    candidate_id: str
    masked_account_number: str
    is_mapped: bool
    mapped_to_account_id: str | None
    suggested: bool
    schwab_account_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
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
    masked_account_number: str | None
    message: str
    details: dict[str, Any] | None = None
    schwab_account_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "state": self.state.value,
            "ready": self.ready,
            "masked_account_number": self.masked_account_number,
            "message": self.message,
            "details": self.details or {},
        }


def extract_last_four(account_number_str: str) -> str:
    digits = "".join(c for c in account_number_str if c.isdigit())
    return digits[-4:] if len(digits) >= 4 else digits


def mask_account_number(account_number_str: str) -> str:
    if _SCHWAB_ACCOUNT_NUMBER.fullmatch(account_number_str) is None:
        return "Unavailable"
    return f"*{account_number_str[-4:]}"


class SchwabReadinessService:
    DEFAULT_TRADER_API_URL = "https://api.schwabapi.com/trader/v1"

    def __init__(
        self,
        repository: PortfolioRepository,
        transport: SchwabOAuthTransport | None = None,
        execution_settings: ExecutionSettings | None = None,
        schwab_settings: SchwabSettings | None = None,
        trader_api_url: str = DEFAULT_TRADER_API_URL,
        hmac_key: bytes | None = None,
    ) -> None:
        self._repository = repository
        self._transport = transport
        self._execution_settings = execution_settings or ExecutionSettings()
        self._schwab_settings = schwab_settings
        self._trader_api_url = trader_api_url
        self._hmac_key = hmac_key or _PROCESS_HMAC_KEY

    def _candidate_id(self, account_id: str, schwab_account_hash: str) -> str:
        message = f"{account_id}:{schwab_account_hash}".encode("utf-8")
        return hmac.new(self._hmac_key, message, hashlib.sha256).hexdigest()

    def mapping_eligible(self, account: Account) -> bool:
        return is_schwab_account_eligible(account.provider, account.currency)

    @property
    def is_configured(self) -> bool:
        return self._transport is not None and self._schwab_settings is not None

    async def list_candidates_for_account(
        self, account_id: str
    ) -> list[SchwabAccountCandidate]:
        stored_acc = self._repository.stored_account(account_id)
        if stored_acc is None:
            raise AccountNotFoundError(f"Account '{account_id}' does not exist")

        if not self.mapping_eligible(stored_acc.account):
            raise ValueError(
                f"Account '{account_id}' is not eligible for Schwab mapping"
            )

        if not self.is_configured:
            raise ProviderUnavailableError("Schwab credentials are not configured")
        assert self._transport is not None

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

        label_digits = "".join(c for c in stored_acc.account.label if c.isdigit())
        label_suffix = label_digits[-4:] if len(label_digits) >= 4 else label_digits

        candidates: list[SchwabAccountCandidate] = []
        for item in body:
            if not isinstance(item, Mapping):
                continue
            raw_acc = item.get("accountNumber")
            hash_val = item.get("hashValue")
            if (
                not isinstance(raw_acc, str)
                or _SCHWAB_ACCOUNT_NUMBER.fullmatch(raw_acc) is None
                or not isinstance(hash_val, str)
                or not hash_val.strip()
            ):
                continue
            hash_val = hash_val.strip()

            suffix = extract_last_four(raw_acc)
            masked = mask_account_number(raw_acc)

            mapped_entry = mappings_by_hash.get(hash_val)
            is_mapped = mapped_entry is not None
            mapped_to_account_id = mapped_entry.account_id if mapped_entry else None

            suggested = False
            if bool(label_suffix and suffix and suffix == label_suffix):
                if not is_mapped or mapped_to_account_id == account_id:
                    suggested = True

            candidate_id = self._candidate_id(account_id, hash_val)

            candidates.append(
                SchwabAccountCandidate(
                    candidate_id=candidate_id,
                    schwab_account_hash=hash_val,
                    masked_account_number=masked,
                    is_mapped=is_mapped,
                    mapped_to_account_id=mapped_to_account_id,
                    suggested=suggested,
                )
            )

        return candidates

    async def save_verified_mapping(
        self, *, account_id: str, candidate_id: str, confirmed: bool
    ) -> StoredSchwabAccountMapping:
        normalized_account_id = account_id.strip()
        normalized_candidate_id = candidate_id.strip()
        if not confirmed:
            raise ValueError("Owner confirmation is required to save account mapping")
        if not normalized_candidate_id:
            raise ValueError("candidate_id cannot be empty")

        candidates = await self.list_candidates_for_account(normalized_account_id)
        matched = next(
            (c for c in candidates if c.candidate_id == normalized_candidate_id),
            None,
        )
        if matched is None:
            raise ValueError(
                "Invalid candidate ID; candidate is not recognized "
                "among active Schwab accounts"
            )

        return self._repository.save_schwab_account_mapping(
            account_id=normalized_account_id,
            schwab_account_hash=matched.schwab_account_hash,
            masked_account_number=matched.masked_account_number,
        )

    async def check_account_readiness(self, account_id: str) -> SchwabAccountReadiness:
        account_detail = self._repository.stored_account(account_id)
        if account_detail is None:
            return SchwabAccountReadiness(
                account_id=account_id,
                state=SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                ready=False,
                masked_account_number=None,
                message=f"Local account '{account_id}' was not found",
            )

        mapping = self._repository.get_schwab_account_mapping(account_id)

        if mapping is not None and not is_schwab_account_masked(
            mapping.masked_account_number
        ):
            mapping = None

        def blocked(
            state: SchwabReadinessState, message: str
        ) -> SchwabAccountReadiness:
            return SchwabAccountReadiness(
                account_id=account_id,
                state=state,
                ready=False,
                schwab_account_hash=mapping.schwab_account_hash if mapping else None,
                masked_account_number=mapping.masked_account_number
                if mapping
                else None,
                message=message,
            )

        if not self.mapping_eligible(account_detail.account):
            return blocked(
                SchwabReadinessState.UNSUPPORTED_ACCOUNT,
                f"Account '{account_id}' is not eligible for Schwab trading "
                "(must be a Schwab USD account)",
            )

        if not self.is_configured:
            return blocked(
                SchwabReadinessState.NOT_CONFIGURED,
                "Schwab API credentials are not configured",
            )

        if not self._execution_settings.permits_schwab_execution:
            opt_in_reason = (
                "Execution provider is not set to 'schwab'"
                if self._execution_settings.provider != "schwab"
                else "SCHWAB_EXECUTION_ENABLED is not set to true"
            )
            return blocked(
                SchwabReadinessState.NOT_OPTED_IN,
                f"Schwab production execution is not enabled ({opt_in_reason})",
            )

        if mapping is None:
            return blocked(
                SchwabReadinessState.UNMAPPED,
                f"Account '{account_id}' has not been mapped to a Schwab account hash",
            )

        assert self._transport is not None
        try:
            await self._transport.access_token()
        except ProviderAuthenticationError:
            return blocked(
                SchwabReadinessState.AUTH_FAILED, "Schwab authentication failed"
            )
        except Exception:
            return blocked(
                SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                "Schwab service is unavailable",
            )

        try:
            status, body = await self._transport.request(
                "GET", f"{self._trader_api_url}/accounts/accountNumbers"
            )
            raise_for_status(status, context="Schwab Trader API")
        except ProviderAuthorizationError:
            return blocked(
                SchwabReadinessState.NOT_ENTITLED,
                "Schwab Accounts and Trading product is not authorized or entitled",
            )
        except ProviderAuthenticationError:
            return blocked(
                SchwabReadinessState.AUTH_FAILED, "Schwab authentication failed"
            )
        except Exception:
            return blocked(
                SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                "Failed to fetch Schwab account numbers",
            )

        if not isinstance(body, list):
            return blocked(
                SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                "Unexpected account list response from Schwab",
            )

        known_hashes: set[str] = set()
        for item in body:
            if isinstance(item, Mapping):
                h = item.get("hashValue")
                if h:
                    known_hashes.add(str(h))

        if mapping.schwab_account_hash not in known_hashes:
            return blocked(
                SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                "Mapped Schwab account hash was not found in "
                "active Schwab account list",
            )

        try:
            status, detail_body = await self._transport.request(
                "GET", f"{self._trader_api_url}/accounts/{mapping.schwab_account_hash}"
            )
            if status == 404:
                return blocked(
                    SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                    "Schwab account detail returned 404 Not Found",
                )
            raise_for_status(status, context="Schwab Trader API")
        except ProviderAuthorizationError:
            return blocked(
                SchwabReadinessState.NOT_ENTITLED,
                "Schwab account detail access is not authorized",
            )
        except Exception:
            return blocked(
                SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                "Failed to read Schwab account detail",
            )

        if not isinstance(detail_body, Mapping):
            return blocked(
                SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                "Invalid account detail response from Schwab",
            )

        sec_account = detail_body.get("securitiesAccount")
        if not isinstance(sec_account, Mapping):
            return blocked(
                SchwabReadinessState.ACCOUNT_UNAVAILABLE,
                "Schwab account details missing securitiesAccount",
            )

        if sec_account.get("isClosingOnlyRestricted") is not False:
            return blocked(
                SchwabReadinessState.UNSUPPORTED_ACCOUNT,
                "Schwab account is restricted to closing transactions only",
            )

        broker_currency = sec_account.get("currency")
        if (
            not isinstance(broker_currency, str)
            or broker_currency.strip().upper() != "USD"
        ):
            return blocked(
                SchwabReadinessState.UNSUPPORTED_ACCOUNT,
                "Schwab account currency is unsupported (USD required)",
            )

        raw_account_type = sec_account.get("type")
        account_type = (
            raw_account_type.upper() if isinstance(raw_account_type, str) else ""
        )
        if account_type not in ("MARGIN", "CASH", "INDIVIDUAL"):
            return blocked(
                SchwabReadinessState.UNSUPPORTED_ACCOUNT,
                "Schwab account type is not supported for execution",
            )

        is_day_trader = sec_account.get("isDayTrader")
        if not isinstance(is_day_trader, bool):
            return blocked(
                SchwabReadinessState.UNSUPPORTED_ACCOUNT,
                "Schwab account details are incomplete",
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
                "is_day_trader": is_day_trader,
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
