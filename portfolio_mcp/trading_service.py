from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from portfolio_mcp.database import (
    OrderDraft,
    PortfolioRepository,
    StoredOrder,
    StoredOrderAuthorization,
)
from portfolio_mcp.execution import (
    CapabilityState,
    ExecutionCapability,
    ExecutionCommand,
    ExecutionIndeterminateError,
    ExecutionProvider,
    ExecutionResult,
    OrderState,
)
from portfolio_mcp.provider import MarketDataProvider, PortfolioProvider


class TradingValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


SubmissionValidator = Callable[[OrderDraft, datetime], Awaitable[None]]


class OrderDraftService:
    def __init__(
        self,
        repository: PortfolioRepository,
        portfolio_provider: PortfolioProvider,
        market_data_provider: MarketDataProvider,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._portfolio_provider = portfolio_provider
        self._market_data_provider = market_data_provider
        self._clock = clock

    async def create(
        self,
        *,
        account_id: str,
        instrument_id: str,
        side: str,
        order_type: str,
        quantity: str,
        limit_price: str | None,
    ) -> OrderDraft:
        parsed_quantity = _positive_decimal(quantity, "quantity", whole_shares=True)
        parsed_limit_price = _limit_price(order_type, limit_price)
        if side not in {"buy", "sell"}:
            raise TradingValidationError("invalid_side", "side must be buy or sell")
        if order_type not in {"market", "limit"}:
            raise TradingValidationError(
                "invalid_order_type", "type must be market or limit"
            )

        accounts = await self._portfolio_provider.list_accounts()
        account = next((item for item in accounts if item.id == account_id), None)
        if account is None:
            raise TradingValidationError("account_not_found", "Account not found")
        quote = await self._market_data_provider.get_quote(instrument_id)
        if quote.instrument.asset_class not in {"equity", "etf", "equity_etf"}:
            raise TradingValidationError(
                "unsupported_instrument", "Only US stocks and ETFs are supported"
            )
        if side == "sell":
            holdings = await self._portfolio_provider.get_holdings(account_id)
            held = next(
                (
                    position.quantity
                    for position in holdings.positions
                    if position.symbol == quote.instrument.symbol
                ),
                Decimal("0"),
            )
            if parsed_quantity > held:
                raise TradingValidationError(
                    "insufficient_holdings", "Sell quantity exceeds known holdings"
                )
        now = _utc_now(self._clock())
        if order_type == "market" and now - quote.observed_at > timedelta(seconds=60):
            raise TradingValidationError(
                "quote_stale", "A current quote is required for a market order"
            )
        if order_type == "market" and all(
            value is None
            for value in (quote.last_price, quote.bid_price, quote.ask_price)
        ):
            raise TradingValidationError(
                "quote_unavailable", "A price is required for a market order"
            )
        fingerprint = _fingerprint(
            account_id,
            instrument_id,
            side,
            order_type,
            parsed_quantity,
            parsed_limit_price,
        )
        draft = OrderDraft(
            id=str(uuid4()),
            account_id=account.id,
            account_label=account.label,
            provider=account.provider,
            instrument_id=quote.instrument.id,
            symbol=quote.instrument.symbol,
            instrument_name=quote.instrument.name,
            asset_class=quote.instrument.asset_class,
            side=side,
            order_type=order_type,
            quantity=parsed_quantity,
            limit_price=parsed_limit_price,
            quote_observed_at=quote.observed_at,
            quote_last_price=quote.last_price,
            quote_bid_price=quote.bid_price,
            quote_ask_price=quote.ask_price,
            quote_source=quote.source,
            warnings=_draft_warnings(
                order_type, quote.last_price, quote.bid_price, quote.ask_price
            ),
            fingerprint=fingerprint,
            created_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        self._repository.save_order_draft(draft)
        return draft


class OrderSubmissionService:
    def __init__(
        self,
        repository: PortfolioRepository,
        execution_provider: ExecutionProvider,
        clock: Callable[[], datetime],
        validator: SubmissionValidator,
    ) -> None:
        self._repository = repository
        self._execution_provider = execution_provider
        self._clock = clock
        self._validator = validator
        if self._repository.has_stranded_submissions():
            self._repository.recover_stranded_submissions(_utc_now(self._clock()))

    async def confirm(
        self, draft_id: str, expected_fingerprint: str, confirmed: bool
    ) -> StoredOrder:
        if not confirmed:
            raise TradingValidationError(
                "confirmation_required", "Explicit confirmation is required"
            )
        draft = self._repository.order_draft(draft_id)
        if draft is None:
            raise TradingValidationError("draft_not_found", "Order draft not found")
        if draft.fingerprint != expected_fingerprint:
            raise TradingValidationError(
                "draft_changed", "The reviewed draft no longer matches"
            )
        existing = self._repository.order_for_draft(draft_id)
        if existing is not None:
            authorization = self._repository.authorization_for_draft(draft_id)
            if (
                existing.draft_id != draft.id
                or existing.fingerprint != draft.fingerprint
                or existing.account_id != draft.account_id
                or not _authorization_binds_to_draft(
                    authorization, draft.account_id, draft.fingerprint
                )
            ):
                raise TradingValidationError(
                    "authorization_invalid", "Order authorization is invalid"
                )
            return existing
        await self._validator(draft, _utc_now(self._clock()))
        try:
            capability = await self._execution_provider.get_execution_capability(
                draft.account_id
            )
            if not _capability_is_well_formed(capability, draft.account_id):
                raise TradingValidationError(
                    "capability_unavailable", "Trading capability is unavailable"
                )
            permits_submission = capability.permits_submission
        except TradingValidationError:
            raise
        except Exception as error:
            raise TradingValidationError(
                "capability_unavailable", "Trading capability is unavailable"
            ) from error
        if not permits_submission:
            raise TradingValidationError(
                "execution_unsupported", "This account cannot submit orders"
            )
        now = _utc_now(self._clock())
        if now > draft.expires_at:
            raise TradingValidationError("draft_expired", "Order draft has expired")
        self._require_fresh_market_quote(draft, now)
        order, created = self._repository.begin_order_submission(draft, now)
        if not created:
            return order
        command = ExecutionCommand(
            client_order_id=order.client_order_id,
            account_id=draft.account_id,
            provider=draft.provider,
            instrument_id=draft.instrument_id,
            symbol=draft.symbol,
            side=draft.side,
            order_type=draft.order_type,
            quantity=draft.quantity,
            limit_price=draft.limit_price,
        )
        final_now = _utc_now(self._clock())
        try:
            if final_now > draft.expires_at:
                raise TradingValidationError("draft_expired", "Order draft has expired")
            self._require_fresh_market_quote(draft, final_now)
            await self._validator(draft, final_now)
        except TradingValidationError as error:
            return self._repository.finish_order_submission(
                order.id,
                OrderState.REJECTED,
                final_now,
                result_code=error.code,
                result_message=str(error),
            )
        try:
            result = await self._execution_provider.submit_order(command)
            return self._persist_provider_result(order, result, now)
        except asyncio.CancelledError:
            self._unknown(order, now)
            raise
        except (ExecutionIndeterminateError, TimeoutError):
            return self._unknown(order, now)
        except Exception:
            return self._unknown(order, now)

    def _persist_provider_result(
        self, order: StoredOrder, result: object, now: datetime
    ) -> StoredOrder:
        if not isinstance(result, ExecutionResult):
            return self._unknown(order, now)
        if not isinstance(result.state, OrderState):
            return self._unknown(order, now)
        if result.state not in {
            OrderState.ACCEPTED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.REJECTED,
        }:
            return self._unknown(order, now)
        broker_order_id = result.broker_order_id
        if result.state != OrderState.REJECTED and (
            not isinstance(broker_order_id, str) or not broker_order_id.strip()
        ):
            return self._unknown(order, now)
        return self._repository.finish_order_submission(
            order.id,
            result.state,
            now,
            broker_order_id=(
                broker_order_id.strip() if isinstance(broker_order_id, str) else None
            ),
            result_code=result.state.lower(),
            result_message=_safe_message(result.state),
        )

    def _require_fresh_market_quote(self, draft: OrderDraft, now: datetime) -> None:
        if draft.order_type != "market":
            return
        if draft.quote_observed_at is None:
            raise TradingValidationError(
                "quote_unavailable", "A current quote is required for a market order"
            )
        if now - draft.quote_observed_at > timedelta(seconds=60):
            raise TradingValidationError(
                "quote_stale", "The market quote is stale; create a new draft"
            )

    def _unknown(self, order: StoredOrder, now: datetime) -> StoredOrder:
        return self._repository.finish_order_submission(
            order.id,
            OrderState.UNKNOWN,
            now,
            result_code="unknown",
            result_message="Order outcome is unknown. Reconciliation is required.",
        )


async def allow_fixture_submission(_: OrderDraft, __: datetime) -> None:
    return None


def fixture_submission_validator(
    portfolio_provider: PortfolioProvider,
) -> SubmissionValidator:
    async def validate(draft: OrderDraft, _now: datetime) -> None:
        try:
            accounts = await portfolio_provider.list_accounts()
        except Exception as error:
            raise TradingValidationError(
                "fixture_state_unavailable",
                "Current fixture account state is unavailable",
            ) from error
        account = next((item for item in accounts if item.id == draft.account_id), None)
        if account is None:
            raise TradingValidationError("account_not_found", "Account not found")
        if account.provider != draft.provider:
            raise TradingValidationError(
                "fixture_state_stale", "Current fixture account state is stale"
            )
        try:
            holdings = await portfolio_provider.get_holdings(draft.account_id)
        except Exception as error:
            raise TradingValidationError(
                "fixture_state_unavailable",
                "Current fixture holdings are unavailable",
            ) from error
        if (
            holdings.account.id != draft.account_id
            or holdings.account.provider != draft.provider
            or any(
                position.account_id != draft.account_id
                for position in holdings.positions
            )
        ):
            raise TradingValidationError(
                "fixture_state_stale", "Current fixture holdings are stale"
            )
        if draft.side != "sell":
            return
        held = sum(
            (
                position.quantity
                for position in holdings.positions
                if position.symbol == draft.symbol
            ),
            start=Decimal("0"),
        )
        if not held.is_finite() or held < 0:
            raise TradingValidationError(
                "fixture_state_unavailable", "Current fixture holdings are invalid"
            )
        if draft.quantity > held:
            raise TradingValidationError(
                "insufficient_holdings", "Sell quantity exceeds current holdings"
            )

    return validate


def _capability_is_well_formed(capability: object, account_id: str) -> bool:
    return (
        isinstance(capability, ExecutionCapability)
        and isinstance(capability.account_id, str)
        and bool(capability.account_id.strip())
        and capability.account_id == account_id
        and isinstance(capability.state, CapabilityState)
        and isinstance(capability.can_submit, bool)
        and isinstance(capability.can_cancel, bool)
    )


def _authorization_binds_to_draft(
    authorization: StoredOrderAuthorization | None, account_id: str, fingerprint: str
) -> bool:
    return (
        authorization is not None
        and authorization.action == "submit"
        and authorization.expected_fingerprint == fingerprint
        and authorization.account_id == account_id
        and authorization.consumed_at is not None
    )


def _positive_decimal(
    value: str, field_name: str, *, whole_shares: bool = False
) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise TradingValidationError(
            "invalid_value", f"{field_name} must be numeric"
        ) from error
    if not parsed.is_finite() or parsed <= 0:
        raise TradingValidationError(
            "invalid_value", f"{field_name} must be greater than zero"
        )
    if whole_shares and parsed != parsed.to_integral_value():
        raise TradingValidationError(
            "whole_shares_required", f"{field_name} must be a whole-share quantity"
        )
    return parsed


def _limit_price(order_type: str, value: str | None) -> Decimal | None:
    if order_type == "market":
        if value is not None:
            raise TradingValidationError(
                "invalid_limit_price", "Market orders cannot include a limit price"
            )
        return None
    if order_type == "limit" and value is not None:
        return _positive_decimal(value, "limit_price")
    raise TradingValidationError(
        "invalid_limit_price", "Limit orders require a positive limit price"
    )


def _fingerprint(
    account_id: str,
    instrument_id: str,
    side: str,
    order_type: str,
    quantity: Decimal,
    limit_price: Decimal | None,
) -> str:
    payload = {
        "account_id": account_id,
        "instrument_id": instrument_id,
        "side": side,
        "order_type": order_type,
        "quantity": _decimal_string(quantity),
        "limit_price": _decimal_string(limit_price)
        if limit_price is not None
        else None,
        "time_in_force": "day",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _decimal_string(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _utc_now(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Clock must return a timezone-aware value")
    return value.astimezone(UTC)


def _safe_message(state: OrderState) -> str:
    if state == OrderState.ACCEPTED:
        return "Fake execution accepted the order."
    if state == OrderState.PARTIALLY_FILLED:
        return "Fake execution partially filled the order."
    if state == OrderState.FILLED:
        return "Fake execution filled the order."
    return "Fake execution rejected the order."


def _draft_warnings(
    order_type: str,
    last_price: Decimal | None,
    bid_price: Decimal | None,
    ask_price: Decimal | None,
) -> tuple[str, ...]:
    warnings: list[str] = ["preview_unavailable", "impact_unavailable"]
    if last_price is None and bid_price is None and ask_price is None:
        warnings.insert(0, "quote_unavailable")
    if order_type == "market":
        return tuple(warning for warning in warnings if warning != "quote_unavailable")
    return tuple(warnings)
