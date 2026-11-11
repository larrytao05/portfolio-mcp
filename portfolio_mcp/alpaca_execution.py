from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from portfolio_mcp.config import AlpacaMarketDataSettings
from portfolio_mcp.execution import (
    CapabilityState,
    ExecutionCapability,
    ExecutionCommand,
    ExecutionError,
    ExecutionIndeterminateError,
    ExecutionPreflightError,
    ExecutionResult,
    FillSummary,
    OrderState,
)


class AlpacaExecutionTransport(Protocol):
    def request(
        self, method: str, path: str, body: dict[str, str] | None = None
    ) -> tuple[int, object]: ...


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibAlpacaExecutionTransport:
    def __init__(self, settings: AlpacaMarketDataSettings) -> None:
        self._headers = {
            "APCA-API-KEY-ID": settings.api_key,
            "APCA-API-SECRET-KEY": settings.secret_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._opener = build_opener(_RejectRedirects())

    def request(
        self, method: str, path: str, body: dict[str, str] | None = None
    ) -> tuple[int, object]:
        if method not in {"GET", "POST", "DELETE"} or not path.startswith("/v2/"):
            raise ExecutionError("Invalid Alpaca execution request")
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            "https://api.alpaca.markets" + path,
            data=data,
            headers=self._headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=15) as response:
                raw = response.read()
                return response.status, json.loads(
                    raw, parse_float=str
                ) if raw else None
        except HTTPError as error:
            return error.code, None


_STATES = {
    "new": OrderState.ACCEPTED,
    "accepted": OrderState.ACCEPTED,
    "pending_new": OrderState.ACCEPTED,
    "accepted_for_bidding": OrderState.ACCEPTED,
    "partially_filled": OrderState.PARTIALLY_FILLED,
    "filled": OrderState.FILLED,
    "rejected": OrderState.REJECTED,
    "pending_cancel": OrderState.CANCEL_PENDING,
    "canceled": OrderState.CANCELED,
    "expired": OrderState.CANCELED,
}


class AlpacaExecutionProvider:
    def __init__(
        self,
        account_id: str,
        upstream_account_id: str,
        transport: AlpacaExecutionTransport,
        *,
        dispatch_guard: Callable[[ExecutionCommand], None] | None = None,
        clock: Callable[[], datetime] | None = None,
        cancel_dispatch: Callable[
            [Callable[[], tuple[int, object]]], tuple[int, object]
        ]
        | None = None,
    ) -> None:
        self._cancel_dispatch = cancel_dispatch
        self._account_id = account_id
        self._upstream_account_id = upstream_account_id
        self._transport = transport
        self._dispatch_guard = dispatch_guard
        self._clock = clock or (lambda: datetime.now(UTC))

    async def _request(
        self, method: str, path: str, body: dict[str, str] | None = None
    ) -> tuple[int, object]:
        return await asyncio.to_thread(self._transport.request, method, path, body)

    async def get_execution_capability(self, account_id: str) -> ExecutionCapability:
        if account_id != self._account_id:
            return ExecutionCapability(account_id, CapabilityState.UNKNOWN)
        try:
            status, body = await self._request("GET", "/v2/account")
        except Exception:
            return ExecutionCapability(account_id, CapabilityState.UNKNOWN)
        if status != 200 or not isinstance(body, Mapping):
            return ExecutionCapability(account_id, CapabilityState.UNKNOWN)
        valid = (
            body.get("id") == self._upstream_account_id
            and body.get("status") == "ACTIVE"
            and body.get("trading_blocked") is False
            and body.get("account_blocked") is False
        )
        return ExecutionCapability(
            account_id,
            CapabilityState.SUPPORTED if valid else CapabilityState.UNKNOWN,
            can_submit=valid,
            can_cancel=valid,
        )

    async def submit_order(self, command: ExecutionCommand) -> ExecutionResult:
        if (
            command.account_id != self._account_id
            or command.provider.lower() != "alpaca"
            or command.side not in {"buy", "sell"}
            or command.order_type not in {"market", "limit"}
            or command.time_in_force != "day"
            or not command.quantity.is_finite()
            or command.quantity <= 0
            or command.quantity != command.quantity.to_integral_value()
            or (command.order_type == "market" and command.limit_price is not None)
            or (
                command.order_type == "limit"
                and (
                    command.limit_price is None
                    or not command.limit_price.is_finite()
                    or command.limit_price <= 0
                )
            )
        ):
            raise ExecutionError("Unsupported order")
        capability = await self.get_execution_capability(command.account_id)
        if not capability.permits_submission:
            raise ExecutionPreflightError("Account cannot submit orders")
        body = {
            "symbol": command.symbol,
            "qty": str(command.quantity),
            "side": command.side,
            "type": command.order_type,
            "time_in_force": "day",
            "client_order_id": command.client_order_id,
        }
        if command.limit_price is not None:
            body["limit_price"] = str(command.limit_price)
        try:
            status, result = await asyncio.to_thread(
                self._dispatch_submit, command, body
            )
        except ExecutionPreflightError:
            raise
        except Exception as error:
            raise ExecutionIndeterminateError("Order outcome is unknown") from error
        if status in {400, 403, 422}:
            return ExecutionResult(OrderState.REJECTED)
        if status != 200:
            raise ExecutionIndeterminateError("Order outcome is unknown")
        parsed = _result(result)
        if (
            not isinstance(result, Mapping)
            or result.get("client_order_id") != command.client_order_id
            or result.get("symbol") != command.symbol
        ):
            raise ExecutionIndeterminateError("Order outcome is unknown")
        if parsed is None or parsed.state not in {
            OrderState.ACCEPTED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.REJECTED,
        }:
            raise ExecutionIndeterminateError("Order outcome is unknown")
        return parsed

    def _dispatch_submit(
        self, command: ExecutionCommand, body: dict[str, str]
    ) -> tuple[int, object]:
        now = self._clock()
        if (
            self._dispatch_guard is None
            or command.valid_until is None
            or now.tzinfo is None
            or command.valid_until.tzinfo is None
            or now > command.valid_until
        ):
            raise ExecutionPreflightError("Trading review is unavailable or expired")
        try:
            self._dispatch_guard(command)
        except Exception as error:
            raise ExecutionPreflightError(
                "Trading safeguards blocked the order"
            ) from error
        if self._clock() > command.valid_until:
            raise ExecutionPreflightError("Trading review has expired")
        return self._transport.request("POST", "/v2/orders", body)

    async def get_order(self, broker_order_id: str) -> ExecutionResult | None:
        status, result = await self._request(
            "GET", f"/v2/orders/{quote(broker_order_id, safe='')}"
        )
        if status != 200 or not isinstance(result, Mapping):
            return None
        return _result(result) if result.get("id") == broker_order_id else None

    async def list_recent_orders(
        self, client_order_id: str, limit: int
    ) -> list[ExecutionResult]:
        if not 1 <= limit <= 100:
            raise ExecutionError("Recent-order limit must be between 1 and 100")
        status, result = await self._request(
            "GET",
            "/v2/orders:by_client_order_id?"
            + urlencode({"client_order_id": client_order_id}),
        )
        parsed = (
            _result(result)
            if status == 200
            and isinstance(result, Mapping)
            and result.get("client_order_id") == client_order_id
            else None
        )
        return [parsed] if parsed else []

    def _dispatch_cancellation(self, broker_order_id: str) -> tuple[int, object]:
        if self._cancel_dispatch is None:
            raise ExecutionPreflightError("Cancellation route guard is unavailable")
        path = f"/v2/orders/{quote(broker_order_id, safe='')}"
        return self._cancel_dispatch(lambda: self._transport.request("DELETE", path))

    async def cancel_order(self, broker_order_id: str) -> ExecutionResult:
        capability = await self.get_execution_capability(self._account_id)
        if not capability.permits_cancellation:
            raise ExecutionError("Account cannot cancel orders")
        existing = await self.get_order(broker_order_id)
        if existing is None or existing.state not in {
            OrderState.ACCEPTED,
            OrderState.PARTIALLY_FILLED,
        }:
            raise ExecutionError("Order cannot be canceled")
        try:
            status, _ = await asyncio.to_thread(
                self._dispatch_cancellation, broker_order_id
            )
        except Exception as error:
            raise ExecutionIndeterminateError(
                "Cancellation outcome is unknown"
            ) from error
        if status == 204:
            return ExecutionResult(OrderState.CANCEL_PENDING, broker_order_id)
        if status == 422:
            result = await self.get_order(broker_order_id)
            if result is not None:
                return result
        raise ExecutionIndeterminateError("Cancellation outcome is unknown")


def _result(value: object) -> ExecutionResult | None:
    if not isinstance(value, Mapping):
        return None
    status = value.get("status")
    if not isinstance(status, str):
        return None
    state = _STATES.get(status)
    broker_order_id = value.get("id")
    if state is None or not isinstance(broker_order_id, str) or not broker_order_id:
        return None
    raw_quantity, raw_price = value.get("filled_qty"), value.get("filled_avg_price")
    fill = None
    if raw_quantity is not None:
        try:
            quantity = Decimal(str(raw_quantity))
            price = Decimal(str(raw_price)) if raw_price is not None else None
            if (
                not quantity.is_finite()
                or quantity < 0
                or (price is not None and (not price.is_finite() or price <= 0))
            ):
                return None
            fill = FillSummary(quantity, price)
        except (InvalidOperation, ValueError):
            return None
    return ExecutionResult(state, broker_order_id, fill=fill)
