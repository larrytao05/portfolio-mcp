from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlencode

from portfolio_mcp.database import PortfolioRepository
from portfolio_mcp.execution import (
    BrokerOrderSearch,
    BrokerOrderSnapshot,
    CapabilityState,
    ExecutionCapability,
    ExecutionCommand,
    ExecutionError,
    ExecutionIndeterminateError,
    ExecutionResult,
    FillSummary,
    OrderReadProvider,
    OrderState,
)
from portfolio_mcp.provider import ProviderUnavailableError
from portfolio_mcp.schwab_transport import (
    SchwabOAuthTransport,
    schwab_asset_class,
)

_asset_class = schwab_asset_class


def _extract_broker_order_id(headers: dict[str, str], body: object) -> str | None:
    location = None
    for k, v in headers.items():
        if k.lower() == "location":
            location = v
            break
    if location:
        segments = [s.strip() for s in location.rstrip("/").split("/") if s.strip()]
        if segments:
            candidate = segments[-1]
            if candidate:
                return candidate
    if isinstance(body, Mapping):
        order_id = body.get("orderId") or body.get("order_id")
        if order_id is not None:
            return str(order_id).strip()
    return None


def _extract_error_message(body: object, default: str) -> str:
    if isinstance(body, Mapping):
        for key in ("error", "message", "description"):
            val = body.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return default


def _parse_timestamp(val: object, default: datetime) -> datetime:
    if isinstance(val, str) and val.strip():
        try:
            cleaned = val.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except (ValueError, TypeError):
            pass
    return default


def _map_schwab_status(
    status: str, filled_qty: Decimal, total_qty: Decimal
) -> tuple[OrderState, str]:
    norm = status.upper().strip()
    if norm == "FILLED":
        return OrderState.FILLED, "FILLED"
    if norm == "CANCELED":
        return OrderState.CANCELED, "CANCELED"
    if norm == "REJECTED":
        return OrderState.REJECTED, "REJECTED"
    if norm == "EXPIRED":
        return OrderState.EXPIRED, "EXPIRED"
    if norm in {"PENDING_CANCEL", "AWAITING_UR_OUT"}:
        return OrderState.CANCEL_PENDING, "OPEN"
    if filled_qty > 0 and (total_qty == 0 or filled_qty < total_qty):
        return OrderState.PARTIALLY_FILLED, "PARTIALLY_FILLED"
    return OrderState.ACCEPTED, "OPEN"


def _extract_fill_summary(
    data: Mapping[str, object], filled_qty: Decimal
) -> FillSummary | None:
    if filled_qty <= 0:
        return None
    activities = data.get("orderActivityCollection")
    if isinstance(activities, list):
        total_fill_cost = Decimal("0")
        total_fill_qty = Decimal("0")
        for act in activities:
            if isinstance(act, Mapping):
                legs = act.get("executionLegs")
                if isinstance(legs, list):
                    for leg in legs:
                        if isinstance(leg, Mapping):
                            try:
                                l_qty = Decimal(str(leg.get("quantity", 0)))
                                l_px = Decimal(str(leg.get("price", 0)))
                                total_fill_cost += l_qty * l_px
                                total_fill_qty += l_qty
                            except (ArithmeticError, ValueError):
                                pass
        if total_fill_qty > 0:
            avg_px = total_fill_cost / total_fill_qty
            return FillSummary(quantity=total_fill_qty, average_price=avg_px)
    return FillSummary(quantity=filled_qty, average_price=None)


class SchwabExecutionProvider(OrderReadProvider):
    supports_client_order_id_lookup: bool = False

    def __init__(
        self,
        transport: SchwabOAuthTransport,
        repository: PortfolioRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        trader_api_url: str = "https://api.schwabapi.com/trader/v1",
        recent_orders_gate_seconds: float = 30.0,
    ) -> None:
        self._transport = transport
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._trader_api_url = trader_api_url.rstrip("/")
        self._recent_orders_gate_seconds = recent_orders_gate_seconds
        self._last_search_at: dict[str, datetime] = {}
        self._last_search_window: dict[str, tuple[datetime, datetime]] = {}
        self._last_search_result: dict[str, tuple[BrokerOrderSnapshot, ...]] = {}

    async def get_execution_capability(self, account_id: str) -> ExecutionCapability:
        mapping = self._repository.get_schwab_account_mapping(account_id)
        if mapping is not None:
            return ExecutionCapability(
                account_id=account_id,
                state=CapabilityState.SUPPORTED,
                can_submit=True,
                can_cancel=True,
            )
        return ExecutionCapability(
            account_id=account_id,
            state=CapabilityState.UNSUPPORTED,
            can_submit=False,
            can_cancel=False,
        )

    async def submit_order(self, command: ExecutionCommand) -> ExecutionResult:
        mapping = self._repository.get_schwab_account_mapping(command.account_id)
        if mapping is None:
            raise ExecutionError(
                f"Account {command.account_id} is not mapped to Schwab"
            )
        account_hash = mapping.schwab_account_hash

        if command.quantity <= 0 or command.quantity % 1 != 0:
            raise ExecutionError(
                "Schwab execution requires positive whole-share quantity"
            )

        side = command.side.lower()
        if side not in {"buy", "sell"}:
            raise ExecutionError(
                f"Unsupported side '{command.side}'; "
                "only 'buy' and 'sell' are supported"
            )

        order_type = command.order_type.lower()
        if order_type not in {"limit", "market"}:
            raise ExecutionError(f"Unsupported order type '{command.order_type}'")

        if order_type == "limit":
            if command.limit_price is None or command.limit_price <= 0:
                raise ExecutionError("Limit price is required for limit orders")

        tif = command.time_in_force.lower()
        if tif != "day":
            raise ExecutionError(
                f"Unsupported time_in_force '{command.time_in_force}'; "
                "only 'day' is supported"
            )

        payload: dict[str, object] = {
            "orderType": command.order_type.upper(),
            "session": "NORMAL",
            "duration": "DAY",
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [
                {
                    "instruction": command.side.upper(),
                    "quantity": int(command.quantity),
                    "instrument": {
                        "symbol": command.symbol,
                        "assetType": "EQUITY",
                    },
                }
            ],
        }
        if order_type == "limit":
            assert command.limit_price is not None
            payload["price"] = f"{command.limit_price:.2f}"

        url = f"{self._trader_api_url}/accounts/{account_hash}/orders"
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}

        try:
            (
                status,
                resp_body,
                resp_headers,
            ) = await self._transport.request_with_headers(
                "POST", url, body=body, additional_headers=headers
            )
        except (TimeoutError, ProviderUnavailableError) as err:
            raise ExecutionIndeterminateError(
                f"Order submission outcome is indeterminate: {err}"
            ) from err
        except Exception as err:
            raise ExecutionError(f"Order submission failed: {err}") from err

        if status == 201:
            broker_order_id = _extract_broker_order_id(resp_headers, resp_body)
            if broker_order_id:
                return ExecutionResult(
                    state=OrderState.ACCEPTED,
                    broker_order_id=broker_order_id,
                )
            return ExecutionResult(
                state=OrderState.UNKNOWN,
                broker_order_id=None,
                message=(
                    "Order submitted with HTTP 201, but broker order ID could "
                    "not be parsed; reconciliation required"
                ),
            )

        if status == 400:
            error_msg = _extract_error_message(
                resp_body, default="Order rejected by broker"
            )
            return ExecutionResult(
                state=OrderState.REJECTED,
                broker_order_id=None,
                message=error_msg,
            )

        raise ExecutionError(f"Broker returned unexpected status {status}")

    async def cancel_order(
        self, broker_order_id: str, account_id: str | None = None
    ) -> ExecutionResult:
        if account_id is None:
            order = self._repository.order_by_broker_id(broker_order_id)
            if order is not None:
                account_id = order.account_id
        if account_id is None:
            raise ExecutionError(
                f"Cannot resolve account for broker order {broker_order_id}"
            )

        mapping = self._repository.get_schwab_account_mapping(account_id)
        if mapping is None:
            raise ExecutionError(f"Account {account_id} is not mapped to Schwab")
        account_hash = mapping.schwab_account_hash

        url = f"{self._trader_api_url}/accounts/{account_hash}/orders/{broker_order_id}"
        try:
            status, resp_body = await self._transport.request("DELETE", url)
        except (TimeoutError, ProviderUnavailableError) as err:
            raise ExecutionIndeterminateError(
                f"Cancellation outcome for {broker_order_id} is indeterminate: {err}"
            ) from err
        except Exception as err:
            raise ExecutionError(f"Order cancellation failed: {err}") from err

        if status in {200, 204}:
            return ExecutionResult(
                state=OrderState.CANCELED,
                broker_order_id=broker_order_id,
            )

        if status in {400, 404, 409}:
            msg = _extract_error_message(resp_body, default="Order cannot be canceled")
            return ExecutionResult(
                state=OrderState.ACCEPTED,
                broker_order_id=broker_order_id,
                message=msg,
            )

        raise ExecutionError(f"Broker returned unexpected status {status} on cancel")

    async def get_order(self, broker_order_id: str) -> ExecutionResult | None:
        order = self._repository.order_by_broker_id(broker_order_id)
        if order is None:
            return None
        snapshot = await self.find_by_broker_order_id(order.account_id, broker_order_id)
        if snapshot is None:
            return None
        return ExecutionResult(
            state=snapshot.state,
            broker_order_id=snapshot.broker_order_id,
            fill=snapshot.fill,
        )

    async def list_recent_orders(
        self, client_order_id: str, limit: int
    ) -> list[ExecutionResult]:
        if not 1 <= limit <= 100:
            raise ExecutionError("Recent-order limit must be between 1 and 100")
        return []

    async def find_by_broker_order_id(
        self, account_id: str, broker_order_id: str
    ) -> BrokerOrderSnapshot | None:
        mapping = self._repository.get_schwab_account_mapping(account_id)
        if mapping is None:
            raise ExecutionError(f"Account {account_id} is not mapped to Schwab")
        account_hash = mapping.schwab_account_hash

        url = f"{self._trader_api_url}/accounts/{account_hash}/orders/{broker_order_id}"
        status, resp_body = await self._transport.request("GET", url)

        if status == 404:
            return None

        if status != 200 or not isinstance(resp_body, Mapping):
            raise ExecutionError(f"Broker order lookup failed with status {status}")

        return self._parse_snapshot(account_id, resp_body)

    async def find_by_client_order_id(
        self, account_id: str, client_order_id: str
    ) -> BrokerOrderSearch:
        return BrokerOrderSearch(orders=(), complete=False)

    async def search_orders(
        self, account_id: str, start_at: datetime, end_at: datetime
    ) -> BrokerOrderSearch:
        mapping = self._repository.get_schwab_account_mapping(account_id)
        if mapping is None:
            raise ExecutionError(f"Account {account_id} is not mapped to Schwab")
        account_hash = mapping.schwab_account_hash

        now = self._clock()
        last_at = self._last_search_at.get(account_id)
        last_window = self._last_search_window.get(account_id)
        if last_at is not None and last_window is not None:
            elapsed = (now - last_at).total_seconds()
            if elapsed < self._recent_orders_gate_seconds:
                cached_start, cached_end = last_window
                if cached_start <= start_at and end_at <= cached_end:
                    cached = self._last_search_result.get(account_id, ())
                    filtered = tuple(
                        s for s in cached if start_at <= s.submitted_at <= end_at
                    )
                    return BrokerOrderSearch(orders=filtered, complete=True)
                return BrokerOrderSearch(orders=(), complete=False)

        from_str = start_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        to_str = end_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        params = urlencode(
            {
                "fromEnteredTime": from_str,
                "toEnteredTime": to_str,
                "maxResults": 50,
            }
        )
        url = f"{self._trader_api_url}/accounts/{account_hash}/orders?{params}"

        status, resp_body = await self._transport.request("GET", url)

        if status != 200 or not isinstance(resp_body, list):
            raise ExecutionError(f"Broker order search failed with status {status}")

        snapshots = tuple(
            self._parse_snapshot(account_id, item)
            for item in resp_body
            if isinstance(item, Mapping)
        )
        self._last_search_at[account_id] = now
        self._last_search_window[account_id] = (start_at, end_at)
        self._last_search_result[account_id] = snapshots

        filtered = tuple(s for s in snapshots if start_at <= s.submitted_at <= end_at)
        return BrokerOrderSearch(orders=filtered, complete=True)

    def _resolve_instrument_id(
        self, broker_order_id: str, symbol: str, asset_type: str
    ) -> str:
        order = self._repository.order_by_broker_id(broker_order_id)
        if order is not None:
            return order.instrument_id
        asset_class = _asset_class(asset_type)
        return f"us-{asset_class}:{symbol}"

    def _parse_snapshot(
        self, account_id: str, data: Mapping[str, object]
    ) -> BrokerOrderSnapshot:
        broker_order_id = str(data.get("orderId", ""))
        legs = data.get("orderLegCollection")
        first_leg: Mapping[str, object] = {}
        if isinstance(legs, list) and len(legs) > 0 and isinstance(legs[0], Mapping):
            first_leg = legs[0]

        raw_instr = first_leg.get("instrument")
        instr: Mapping[str, object] = (
            raw_instr if isinstance(raw_instr, Mapping) else {}
        )
        symbol = str(instr.get("symbol", ""))
        asset_type = str(instr.get("assetType", "EQUITY"))
        instrument_id = self._resolve_instrument_id(broker_order_id, symbol, asset_type)

        side = str(first_leg.get("instruction", "")).lower()
        order_type = str(data.get("orderType", "")).lower()

        raw_qty = first_leg.get("quantity") or data.get("quantity") or 0
        total_qty = Decimal(str(raw_qty))

        raw_filled = data.get("filledQuantity") or 0
        filled_qty = Decimal(str(raw_filled))

        price_val = data.get("price")
        limit_price = Decimal(str(price_val)) if price_val is not None else None

        tif = str(data.get("duration", "DAY")).lower()

        now = self._clock()
        submitted_at = _parse_timestamp(data.get("enteredTime"), default=now)
        updated_at = _parse_timestamp(data.get("closeTime"), default=submitted_at)

        status_str = str(data.get("status", ""))
        state, status_label = _map_schwab_status(status_str, filled_qty, total_qty)

        fill = _extract_fill_summary(data, filled_qty)

        tag = data.get("tag")
        client_order_id = str(tag) if tag is not None else None

        return BrokerOrderSnapshot(
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            account_id=account_id,
            instrument_id=instrument_id,
            side=side,
            order_type=order_type,
            quantity=total_qty,
            limit_price=limit_price,
            time_in_force=tif,
            submitted_at=submitted_at,
            state=state,
            updated_at=updated_at,
            status_label=status_label,
            fill=fill,
        )
