from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from portfolio_mcp.database import (
    ConcurrentOrderUpdate,
    McpAuthorizationError,
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
from portfolio_mcp.order_history import (
    OrderEventActor,
    OrderEventCode,
    OrderStatusSource,
)
from portfolio_mcp.provider import MarketDataProvider, PortfolioProvider
from portfolio_mcp.trading_safety import (
    GuardDecision,
    TradeIntent,
    TradingGuard,
    TradingSettingsService,
)


class TradingValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


SubmissionValidator = Callable[[OrderDraft, datetime], Awaitable[None]]


def _authorization_failure_code(value: str) -> OrderEventCode:
    try:
        return OrderEventCode(value)
    except ValueError:
        return OrderEventCode.VALIDATION_FAILED


class OrderDraftService:
    def __init__(
        self,
        repository: PortfolioRepository,
        market_data_provider: MarketDataProvider,
        clock: Callable[[], datetime],
        trading_guard: TradingGuard | None = None,
    ) -> None:
        self._repository = repository
        self._market_data_provider = market_data_provider
        self._clock = clock
        self._trading_guard = trading_guard or TradingGuard(
            repository, TradingSettingsService(repository, clock)
        )

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

        quote = await self._market_data_provider.get_quote(instrument_id)
        if quote.instrument.asset_class not in {"equity", "etf", "equity_etf"}:
            raise TradingValidationError(
                "unsupported_instrument", "Only US stocks and ETFs are supported"
            )
        now = _utc_now(self._clock())
        if order_type == "market":
            quote_age = now - quote.observed_at
            if quote_age < timedelta() or quote_age > timedelta(seconds=60):
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
        decision = _require_guard(
            self._trading_guard,
            TradeIntent(
                account_id=account_id,
                symbol=quote.instrument.symbol,
                asset_class=quote.instrument.asset_class,
                side=side,
                order_type=order_type,
                quantity=parsed_quantity,
                limit_price=parsed_limit_price,
                bid_price=quote.bid_price,
                ask_price=quote.ask_price,
                last_price=quote.last_price,
            ),
        )
        stored_account = self._repository.stored_account(account_id)
        if stored_account is None:
            raise TradingValidationError("account_not_found", "Account not found")
        account = stored_account.account
        capability = self._repository.account_capability(account.id)
        assert capability is not None
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
            estimated_notional=decision.estimated_notional,
            account_refreshed_at=stored_account.source_refreshed_at,
            capability_observed_at=capability.observed_at,
            capability_last_success_at=capability.last_success_at,
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
        trading_guard: TradingGuard | None = None,
    ) -> None:
        self._repository = repository
        self._execution_provider = execution_provider
        self._clock = clock
        self._validator = validator
        self._trading_guard = trading_guard or TradingGuard(
            repository, TradingSettingsService(repository, clock)
        )
        if self._repository.has_stranded_submissions():
            self._repository.recover_stranded_submissions(_utc_now(self._clock()))

    async def confirm(
        self, draft_id: str, expected_fingerprint: str, confirmed: bool
    ) -> StoredOrder:
        attempt_id = uuid4()
        try:
            return await self._confirm(draft_id, expected_fingerprint, confirmed)
        except TradingValidationError as error:
            now = _utc_now(self._clock())
            self._repository.record_authorization_failure(
                attempt_id=attempt_id,
                draft_id=draft_id,
                action="submit",
                actor=OrderEventActor.DASHBOARD,
                code=_authorization_failure_code(error.code),
                occurred_at=now,
            )
            if error.code == OrderEventCode.DRAFT_EXPIRED.value:
                self._repository.record_draft_expiry(draft_id, observed_at=now)
            raise

    async def _confirm(
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
            _require_guard(self._trading_guard, _trade_intent(draft))
        except TradingValidationError as error:
            return self._repository.finish_order_submission(
                order.id,
                OrderState.REJECTED,
                final_now,
                result_code=error.code,
                result_message=str(error),
                expected_version=order.version,
                result_source=OrderStatusSource.LOCAL,
                actor=OrderEventActor.DASHBOARD,
            )
        try:
            order = self._repository.mark_provider_submission_started(
                order.id,
                expected_version=order.version,
                started_at=_utc_now(self._clock()),
            )
            result = await self._execution_provider.submit_order(command)
            observed_at = _utc_now(self._clock())
            return self._persist_provider_result(order, result, observed_at)
        except asyncio.CancelledError:
            self._unknown(order, _utc_now(self._clock()))
            raise
        except ConcurrentOrderUpdate:
            latest = self._repository.order(order.id)
            if latest is None:
                raise
            return latest
        except (ExecutionIndeterminateError, TimeoutError):
            return self._unknown(order, _utc_now(self._clock()))
        except Exception:
            return self._unknown(order, _utc_now(self._clock()))

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
            expected_version=order.version,
            fill=result.fill,
            result_source=OrderStatusSource.PROVIDER,
            actor=OrderEventActor.DASHBOARD,
        )

    def _require_fresh_market_quote(self, draft: OrderDraft, now: datetime) -> None:
        if draft.order_type != "market":
            return
        if draft.quote_observed_at is None:
            raise TradingValidationError(
                "quote_unavailable", "A current quote is required for a market order"
            )
        quote_age = now - draft.quote_observed_at
        if quote_age < timedelta() or quote_age > timedelta(seconds=60):
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
            expected_version=order.version,
            result_source=OrderStatusSource.SYSTEM,
            actor=OrderEventActor.DASHBOARD,
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


def _require_guard(trading_guard: TradingGuard, intent: TradeIntent) -> GuardDecision:
    decision = trading_guard.evaluate(intent)
    if decision.allowed:
        return decision
    violation = decision.violations[0]
    raise TradingValidationError(violation.code, violation.message)


def _trade_intent(draft: OrderDraft) -> TradeIntent:
    return TradeIntent(
        account_id=draft.account_id,
        symbol=draft.symbol,
        asset_class=draft.asset_class,
        side=draft.side,
        order_type=draft.order_type,
        quantity=draft.quantity,
        limit_price=draft.limit_price,
        bid_price=draft.quote_bid_price,
        ask_price=draft.quote_ask_price,
        last_price=draft.quote_last_price,
    )


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


class OrderCancellationService:
    def __init__(
        self,
        repository: PortfolioRepository,
        execution_provider: ExecutionProvider,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._execution_provider = execution_provider
        self._clock = clock
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    def _lock_for(self, order_id: str) -> asyncio.Lock:
        lock = self._locks.get(order_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[order_id] = lock
        return lock

    async def cancel(
        self,
        *,
        order_id: str,
        expected_version: int,
        expected_state: OrderState,
        confirmed: bool,
        actor: OrderEventActor = OrderEventActor.DASHBOARD,
    ) -> StoredOrder:
        if not confirmed:
            raise TradingValidationError(
                "confirmation_required", "Order cancellation confirmation is required"
            )
        order = self._repository.order(order_id)
        if order is None:
            raise TradingValidationError("order_not_found", "Order not found")

        try:
            capability = await self._execution_provider.get_execution_capability(
                order.account_id
            )
        except Exception as error:
            raise TradingValidationError(
                "capability_unavailable", "Trading capability is unavailable"
            ) from error
        if (
            not _capability_is_well_formed(capability, order.account_id)
            or not capability.permits_cancellation
        ):
            raise TradingValidationError(
                "execution_unsupported", "This account cannot cancel orders"
            )

        async with self._lock_for(order_id):
            now = _utc_now(self._clock())
            try:
                order_pending, attempt_id, started = (
                    self._repository.begin_order_cancellation(
                        order_id,
                        expected_version=expected_version,
                        expected_state=expected_state,
                        now=now,
                        actor=actor,
                    )
                )
            except ConcurrentOrderUpdate:
                raise TradingValidationError(
                    "order_conflict", "Order changed before cancellation"
                )
            except ValueError as error:
                raise TradingValidationError("order_not_cancelable", str(error))

            if not started or attempt_id is None:
                return order_pending

            assert order_pending.broker_order_id is not None
            try:
                result = await asyncio.wait_for(
                    self._execution_provider.cancel_order(
                        order_pending.broker_order_id
                    ),
                    timeout=10.0,
                )
            except asyncio.CancelledError:
                self._repository.finish_order_cancellation(
                    order_id,
                    attempt_id=attempt_id,
                    state=OrderState.UNKNOWN,
                    now=_utc_now(self._clock()),
                    outcome="unknown",
                    result_code="unknown",
                    result_message=(
                        "Cancellation interrupted; outcome is unknown. "
                        "Reconciliation required."
                    ),
                    result_source=OrderStatusSource.SYSTEM,
                    actor=actor,
                )
                raise
            except (ExecutionIndeterminateError, asyncio.TimeoutError):
                return self._repository.finish_order_cancellation(
                    order_id,
                    attempt_id=attempt_id,
                    state=OrderState.UNKNOWN,
                    now=_utc_now(self._clock()),
                    outcome="unknown",
                    result_code="unknown",
                    result_message=(
                        "Cancellation outcome is unknown. Reconciliation is required."
                    ),
                    result_source=OrderStatusSource.SYSTEM,
                    actor=actor,
                )
            except Exception as error:
                return self._repository.finish_order_cancellation(
                    order_id,
                    attempt_id=attempt_id,
                    state=OrderState.UNKNOWN,
                    now=_utc_now(self._clock()),
                    outcome="unknown",
                    result_code="unknown",
                    result_message=f"Cancellation failed: {error}",
                    result_source=OrderStatusSource.SYSTEM,
                    actor=actor,
                )

            observed_at = _utc_now(self._clock())
            if not isinstance(result, ExecutionResult):
                return self._repository.finish_order_cancellation(
                    order_id,
                    attempt_id=attempt_id,
                    state=OrderState.UNKNOWN,
                    now=observed_at,
                    outcome="unknown",
                    result_code="unknown",
                    result_message="Cancellation result was malformed.",
                    result_source=OrderStatusSource.SYSTEM,
                    actor=actor,
                )

            if result.state == OrderState.CANCELED:
                return self._repository.finish_order_cancellation(
                    order_id,
                    attempt_id=attempt_id,
                    state=OrderState.CANCELED,
                    now=observed_at,
                    outcome="canceled",
                    result_code="canceled",
                    result_message="Order canceled successfully",
                    result_source=OrderStatusSource.PROVIDER,
                    fill=result.fill,
                    actor=actor,
                )
            else:
                target_state = (
                    result.state
                    if result.state
                    in {
                        OrderState.FILLED,
                        OrderState.EXPIRED,
                        OrderState.PARTIALLY_FILLED,
                        OrderState.ACCEPTED,
                    }
                    else expected_state
                )
                return self._repository.finish_order_cancellation(
                    order_id,
                    attempt_id=attempt_id,
                    state=target_state,
                    now=observed_at,
                    outcome="refused",
                    result_code="cancel_rejected",
                    result_message=result.message
                    or "Cancellation was rejected by the broker.",
                    result_source=OrderStatusSource.PROVIDER,
                    fill=result.fill,
                    actor=actor,
                )


@dataclass(frozen=True)
class CreatedMcpAuthorization:
    id: str
    action: str
    target_draft_id: str
    account_id: str
    plaintext_code: str = field(repr=False)
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class StoredMcpAuthorization:
    id: str
    action: str
    target_draft_id: str
    payload_fingerprint: str
    account_id: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None


class McpAuthorizationService:
    def __init__(
        self,
        repository: PortfolioRepository,
        clock: Callable[[], datetime] | None = None,
        code_generator: Callable[[], str] | None = None,
        scrypt_n: int = 16384,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._code_generator = code_generator or self._default_code_generator
        self._scrypt_n = scrypt_n

    @staticmethod
    def _default_code_generator() -> str:
        return f"{secrets.randbelow(100_000_000):08d}"

    def create_authorization(
        self,
        *,
        action: str,
        target_draft_id: str,
    ) -> CreatedMcpAuthorization:
        now = _utc_now(self._clock())
        draft = self._repository.order_draft(target_draft_id)
        if draft is None:
            raise McpAuthorizationError("draft_not_found", "Order draft not found")
        if now > draft.expires_at:
            raise McpAuthorizationError("draft_expired", "Order draft has expired")

        auth_id = str(uuid4())
        plaintext_code = self._code_generator()
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(
            plaintext_code.encode("utf-8"),
            salt=salt,
            n=self._scrypt_n,
            r=8,
            p=1,
        )

        expires_at = min(now + timedelta(minutes=5), draft.expires_at)

        self._repository.create_mcp_authorization(
            authorization_id=auth_id,
            action=action,
            target_draft_id=target_draft_id,
            payload_fingerprint=draft.fingerprint,
            account_id=draft.account_id,
            salt_hex=salt.hex(),
            digest_hex=digest.hex(),
            created_at=now,
            expires_at=expires_at,
        )

        return CreatedMcpAuthorization(
            id=auth_id,
            action=action,
            target_draft_id=target_draft_id,
            account_id=draft.account_id,
            plaintext_code=plaintext_code,
            created_at=now,
            expires_at=expires_at,
        )

    def consume_authorization(
        self,
        *,
        action: str,
        target_draft_id: str,
        expected_fingerprint: str,
        account_id: str,
        candidate_code: str,
    ) -> StoredMcpAuthorization:
        now = _utc_now(self._clock())
        record = self._repository.active_mcp_authorization(
            action=action, target_draft_id=target_draft_id
        )
        if record is None:
            raise McpAuthorizationError("invalid_or_expired_code")
        if now > record.expires_at:
            self._repository.invalidate_mcp_authorization(
                authorization_id=record.id, reason="expired"
            )
            raise McpAuthorizationError("invalid_or_expired_code")

        matches = False
        if (
            record.account_id == account_id
            and record.payload_fingerprint == expected_fingerprint
        ):
            try:
                candidate_digest = hashlib.scrypt(
                    candidate_code.encode("utf-8"),
                    salt=bytes.fromhex(record.salt),
                    n=self._scrypt_n,
                    r=8,
                    p=1,
                )
                matches = hmac.compare_digest(
                    candidate_digest, bytes.fromhex(record.digest)
                )
            except Exception:
                matches = False

        if not matches:
            self._repository.record_mcp_authorization_failure(
                authorization_id=record.id, max_attempts=5
            )
            raise McpAuthorizationError("invalid_or_expired_code")

        if not self._repository.mark_mcp_authorization_consumed(
            authorization_id=record.id, now=now
        ):
            raise McpAuthorizationError("invalid_or_expired_code")

        return StoredMcpAuthorization(
            id=record.id,
            action=action,
            target_draft_id=target_draft_id,
            payload_fingerprint=expected_fingerprint,
            account_id=account_id,
            created_at=record.created_at,
            expires_at=record.expires_at,
            consumed_at=now,
        )
