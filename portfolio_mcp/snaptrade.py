import asyncio
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Callable, cast

from snaptrade_client.auth import SnapTradeAuth
from snaptrade_client.client import SnapTrade
from snaptrade_client.exceptions import ApiException

from portfolio_mcp.config import SnapTradeSettings
from portfolio_mcp.models import (
    Account,
    HoldingsSnapshot,
    Position,
    Transaction,
    TransactionHistory,
)
from portfolio_mcp.provider import (
    AccountNotFoundError,
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)


class SnapTradeProvider:
    def __init__(self, settings: SnapTradeSettings) -> None:
        self._client = SnapTrade(
            auth=SnapTradeAuth.personal_api_key(
                client_id=settings.client_id,
                consumer_key=settings.consumer_key,
            )
        )

    async def list_accounts(self) -> list[Account]:
        response = await self._call(self._client.account_information.list_user_accounts)
        sources = cast(list[Mapping[str, Any]], response.body)
        return [
            account
            for source in sources
            if (account := self._to_account(source)) is not None
        ]

    async def get_holdings(self, account_id: str) -> HoldingsSnapshot:
        account = await self._get_account(account_id)
        response = await self._call(
            self._client.account_information.get_all_account_positions,
            account_id=account_id,
        )
        body = cast(Mapping[str, Any], response.body)
        freshness = _required_mapping(body, "data_freshness")
        positions = cast(list[Mapping[str, Any]], body.get("results", []))

        return HoldingsSnapshot(
            account=account,
            as_of=_to_date(freshness.get("as_of")),
            positions=tuple(_to_position(account_id, source) for source in positions),
        )

    async def get_transactions(
        self, account_id: str, start_date: date, end_date: date
    ) -> TransactionHistory:
        account = await self._get_account(account_id)
        transactions: list[Transaction] = []
        offset = 0
        page_size = 1000

        while True:
            response = await self._call(
                self._client.account_information.get_account_activities,
                account_id=account_id,
                start_date=start_date,
                end_date=end_date,
                offset=offset,
                limit=page_size,
            )
            body = cast(Mapping[str, Any], response.body)
            activities = cast(list[Mapping[str, Any]], body.get("data", []))
            transactions.extend(
                transaction
                for source in activities
                if start_date
                <= (transaction := _to_transaction(account_id, source)).occurred_on
                <= end_date
            )

            if not activities:
                break

            pagination = body.get("pagination")
            total = pagination.get("total") if isinstance(pagination, Mapping) else None
            offset += len(activities)
            if isinstance(total, int) and offset >= total:
                break
            if total is None and len(activities) < page_size:
                break

        return TransactionHistory(
            account=account,
            start_date=start_date,
            end_date=end_date,
            transactions=tuple(
                sorted(
                    transactions,
                    key=lambda transaction: transaction.occurred_on,
                    reverse=True,
                )
            ),
        )

    async def _get_account(self, account_id: str) -> Account:
        response = await self._call(
            self._client.account_information.get_user_account_details,
            account_id=account_id,
        )
        account = self._to_account(cast(Mapping[str, Any], response.body))
        if account is not None:
            return account

        raise AccountNotFoundError("Account not found")

    async def _call(
        self, operation: Callable[..., Any], /, *args: Any, **kwargs: Any
    ) -> Any:
        try:
            return await asyncio.to_thread(operation, *args, **kwargs)
        except ProviderError:
            raise
        except Exception as error:
            raise _translate_error(error) from None

    def _to_account(self, source: Mapping[str, Any]) -> Account | None:
        if source.get("account_category") != "INVESTMENT":
            return None

        raw_type = _required_string(source, "raw_type")
        name = _optional_string(source.get("name"))
        account_type = _to_account_type(raw_type, name)
        if account_type is None:
            return None

        number = _required_string(source, "number")
        name = name or account_type
        if not isinstance(name, str):
            raise ProviderResponseError("SnapTrade returned an unexpected response")

        provider = _required_string(source, "institution_name")
        return Account(
            id=_required_string(source, "id"),
            provider=provider,
            label=_to_account_label(provider, name, number),
            account_type=account_type,
            currency="USD",
        )


def _to_account_type(raw_type: str, name: str | None) -> str | None:
    normalized = f"{raw_type} {name or ''}".upper()
    if "ROTH" in normalized and "IRA" in normalized:
        return "Roth IRA"
    if "IRA" in normalized:
        return None
    return "Taxable brokerage"


def _to_account_label(provider: str, name: str, number: str) -> str:
    display_name = name.removesuffix(number).rstrip() or name
    suffix = "".join(character for character in number if character.isdigit())[-4:]
    if suffix and len(suffix) < 4 and display_name.endswith(suffix):
        display_name = display_name[: -len(suffix)].rstrip(" .•*-") or display_name
    masked_number = f"••••{suffix}" if suffix else "••••"
    return f"{provider} {display_name} {masked_number}"


def _to_position(account_id: str, source: Mapping[str, Any]) -> Position:
    instrument = _required_mapping(source, "instrument")
    quantity = _required_decimal(source, "units")
    current_price = _optional_decimal(source.get("price"))
    symbol = _required_string(instrument, "symbol")

    return Position(
        account_id=account_id,
        symbol=symbol,
        name=_optional_string(instrument.get("description")) or symbol,
        asset_class=_required_string(instrument, "kind"),
        quantity=quantity,
        current_price=current_price,
        market_value=(quantity * current_price if current_price is not None else None),
        cost_basis=_optional_decimal(source.get("cost_basis")),
        currency=(
            _optional_string(source.get("currency"))
            or _optional_string(instrument.get("currency"))
            or "USD"
        ),
    )


def _to_transaction(account_id: str, source: Mapping[str, Any]) -> Transaction:
    symbol_data = source.get("symbol")
    symbol = (
        _optional_string(symbol_data.get("symbol"))
        if isinstance(symbol_data, Mapping)
        else None
    )
    currency_data = source.get("currency")
    currency = (
        _optional_string(currency_data.get("code"))
        if isinstance(currency_data, Mapping)
        else None
    )
    transaction_type = _required_string(source, "type")
    trade_date = source.get("trade_date")

    return Transaction(
        id=_required_string(source, "id"),
        account_id=account_id,
        occurred_on=_to_date(trade_date),
        transaction_type=transaction_type,
        symbol=symbol,
        description=_optional_string(source.get("description")) or transaction_type,
        quantity=_optional_decimal(source.get("units")),
        amount=_optional_decimal(source.get("amount")) or Decimal("0"),
        fees=_optional_decimal(source.get("fee")) or Decimal("0"),
        currency=currency or "USD",
        occurred_at=_optional_datetime(trade_date),
    )


def _required_mapping(source: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = source.get(field)
    if isinstance(value, Mapping):
        return value
    raise ProviderResponseError("SnapTrade returned an unexpected response")


def _required_string(source: Mapping[str, Any], field: str) -> str:
    value = source.get(field)
    if isinstance(value, str) and value:
        return value
    raise ProviderResponseError("SnapTrade returned an unexpected response")


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _required_decimal(source: Mapping[str, Any], field: str) -> Decimal:
    value = _optional_decimal(source.get(field))
    if value is not None:
        return value
    raise ProviderResponseError("SnapTrade returned an unexpected response")


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        try:
            return Decimal(str(value))
        except ArithmeticError:
            pass
    raise ProviderResponseError("SnapTrade returned an unexpected response")


def _to_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    raise ProviderResponseError("SnapTrade returned an unexpected response")


def _optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else None
    if isinstance(value, str) and "T" in value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
        except ValueError:
            pass
    return None


def _translate_error(error: Exception) -> ProviderError:
    if not isinstance(error, ApiException):
        return ProviderUnavailableError("SnapTrade is temporarily unavailable")

    if error.status == 401:
        return ProviderAuthenticationError("Unable to authenticate with SnapTrade")
    if error.status == 403:
        return ProviderAuthorizationError("SnapTrade access is not authorized")
    if error.status == 404:
        return AccountNotFoundError("Account not found")
    if error.status == 429:
        return ProviderRateLimitError("SnapTrade rate limit reached; try again later")
    if error.status is None or error.status == 0 or error.status >= 500:
        return ProviderUnavailableError("SnapTrade is temporarily unavailable")
    return ProviderResponseError("SnapTrade returned an unexpected response")
