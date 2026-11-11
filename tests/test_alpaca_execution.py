import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from urllib.request import Request

import pytest

from portfolio_mcp.alpaca_execution import AlpacaExecutionProvider
from portfolio_mcp.execution import (
    CapabilityState,
    ExecutionCommand,
    ExecutionError,
    ExecutionIndeterminateError,
    ExecutionPreflightError,
    OrderState,
)


class FakeTransport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def request(self, method, path, body=None):
        self.requests.append((method, path, body))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def command(**overrides):
    return replace(
        ExecutionCommand(
            client_order_id="client-123",
            account_id="canonical-1",
            provider="alpaca",
            instrument_id="us-equity:VTI",
            symbol="VTI",
            side="buy",
            order_type="limit",
            quantity=Decimal("2"),
            limit_price=Decimal("123.4500"),
            valid_until=datetime(2099, 1, 1, tzinfo=UTC),
        ),
        **overrides,
    )


def authorized_transport(responses):
    account = {
        "id": "upstream-1",
        "status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
    }
    return FakeTransport([(200, account), *responses])


def provider(transport):
    return AlpacaExecutionProvider(
        "canonical-1", "upstream-1", transport, dispatch_guard=lambda _: None
    )


def test_live_account_identity_and_flags_fail_closed():
    healthy = {
        "id": "upstream-1",
        "status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
    }
    for response, expected in [
        (healthy, CapabilityState.SUPPORTED),
        ({**healthy, "id": "other"}, CapabilityState.UNKNOWN),
        ({**healthy, "trading_blocked": True}, CapabilityState.UNKNOWN),
        (
            {key: value for key, value in healthy.items() if key != "account_blocked"},
            CapabilityState.UNKNOWN,
        ),
    ]:
        adapter = provider(FakeTransport([(200, response)]))
        capability = asyncio.run(adapter.get_execution_capability("canonical-1"))
        assert capability.state == expected
    transport = FakeTransport([])
    capability = asyncio.run(provider(transport).get_execution_capability("wrong"))
    assert capability.state == CapabilityState.UNKNOWN
    assert transport.requests == []


def test_submit_preserves_exact_order_and_identifier():
    transport = authorized_transport(
        [
            (
                200,
                {
                    "id": "broker-1",
                    "status": "new",
                    "filled_qty": "0",
                    "client_order_id": "client-123",
                    "symbol": "VTI",
                },
            )
        ]
    )
    result = asyncio.run(provider(transport).submit_order(command()))
    assert result.state == OrderState.ACCEPTED
    assert transport.requests == [
        ("GET", "/v2/account", None),
        (
            "POST",
            "/v2/orders",
            {
                "symbol": "VTI",
                "qty": "2",
                "side": "buy",
                "type": "limit",
                "time_in_force": "day",
                "client_order_id": "client-123",
                "limit_price": "123.4500",
            },
        ),
    ]


@pytest.mark.parametrize(
    "fields",
    [
        {"account_id": "other"},
        {"quantity": Decimal("0.5")},
        {"order_type": "stop"},
        {"time_in_force": "gtc"},
        {"order_type": "market", "limit_price": Decimal("10")},
    ],
)
def test_unsupported_order_never_reaches_transport(fields):
    transport = FakeTransport([])
    with pytest.raises(ExecutionError):
        asyncio.run(provider(transport).submit_order(command(**fields)))
    assert transport.requests == []


def test_unknown_submit_is_not_retried():
    transport = authorized_transport([TimeoutError()])
    with pytest.raises(ExecutionIndeterminateError):
        asyncio.run(provider(transport).submit_order(command()))
    assert len(transport.requests) == 2


def test_cancel_acknowledgement_is_pending_and_422_reconciles_fill():
    account = {
        "id": "upstream-1",
        "status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
    }
    current = {"id": "broker-1", "status": "new"}
    transport = FakeTransport(
        [
            (200, account),
            (200, current),
            (204, None),
            (200, account),
            (200, current),
            (422, None),
            (
                200,
                {
                    "id": "broker-1",
                    "status": "filled",
                    "filled_qty": "2",
                    "filled_avg_price": "123.45",
                },
            ),
        ]
    )
    adapter = provider(transport)
    assert (
        asyncio.run(adapter.cancel_order("broker-1")).state == OrderState.CANCEL_PENDING
    )
    result = asyncio.run(adapter.cancel_order("broker-1"))
    assert result.state == OrderState.FILLED
    assert result.fill is not None and result.fill.quantity == Decimal("2")


def test_lookup_by_client_id_encodes_input_and_keeps_unknown_status_unresolved():
    transport = FakeTransport([(200, {"id": "broker-1", "status": "pending_replace"})])
    assert asyncio.run(provider(transport).list_recent_orders("id & 1", 1)) == []
    assert (
        transport.requests[0][1]
        == "/v2/orders:by_client_order_id?client_order_id=id+%26+1"
    )


def test_transport_uses_fixed_live_host_and_blocks_redirects():
    from portfolio_mcp.alpaca_execution import UrllibAlpacaExecutionTransport
    from portfolio_mcp.config import AlpacaMarketDataSettings

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"id": "broker-1"}'

    class Opener:
        def __init__(self):
            self.request: Request | None = None

        def open(self, request, timeout):
            self.request = request
            assert timeout == 15
            return Response()

    transport = UrllibAlpacaExecutionTransport(
        AlpacaMarketDataSettings(api_key="synthetic-key", secret_key="synthetic-secret")
    )
    opener = Opener()
    transport._opener = cast(Any, opener)
    assert transport.request("POST", "/v2/orders", {"qty": "2"}) == (
        200,
        {"id": "broker-1"},
    )
    assert opener.request is not None
    assert opener.request.full_url == "https://api.alpaca.markets/v2/orders"
    assert opener.request.method == "POST"
    assert opener.request.data == b'{"qty": "2"}'
    with pytest.raises(ExecutionError):
        transport.request("POST", "https://example.invalid/v2/orders")


def test_mismatched_order_response_remains_unknown():
    transport = authorized_transport(
        [
            (
                200,
                {
                    "id": "broker-1",
                    "status": "new",
                    "client_order_id": "other",
                    "symbol": "VTI",
                },
            )
        ]
    )
    with pytest.raises(ExecutionIndeterminateError):
        asyncio.run(provider(transport).submit_order(command()))
    assert len(transport.requests) == 2


def test_cancellation_timeout_is_unknown_and_not_retried():
    transport = authorized_transport(
        [(200, {"id": "broker-1", "status": "new"}), TimeoutError()]
    )
    with pytest.raises(ExecutionIndeterminateError):
        asyncio.run(provider(transport).cancel_order("broker-1"))
    assert transport.requests == [
        ("GET", "/v2/account", None),
        ("GET", "/v2/orders/broker-1", None),
        ("DELETE", "/v2/orders/broker-1", None),
    ]


def test_redirect_handler_refuses_to_forward_credentials():
    from portfolio_mcp.alpaca_execution import _RejectRedirects

    assert (
        _RejectRedirects().redirect_request(
            cast(Any, None),
            cast(Any, None),
            302,
            "Found",
            cast(Any, {}),
            "https://example.invalid",
        )
        is None
    )


def test_market_order_omits_limit_price():
    transport = authorized_transport(
        [
            (
                200,
                {
                    "id": "broker-1",
                    "status": "accepted",
                    "client_order_id": "client-123",
                    "symbol": "VTI",
                },
            )
        ]
    )
    result = asyncio.run(
        provider(transport).submit_order(command(order_type="market", limit_price=None))
    )
    assert result.state == OrderState.ACCEPTED
    assert transport.requests[1][2] == {
        "symbol": "VTI",
        "qty": "2",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": "client-123",
    }


def test_order_lookup_rejects_different_broker_order_id():
    transport = FakeTransport([(200, {"id": "other", "status": "filled"})])
    assert asyncio.run(provider(transport).get_order("broker-1")) is None


def test_quote_deadline_and_guard_rechecked_after_account_request():
    from datetime import timedelta

    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock = [now]

    class SlowAccount(FakeTransport):
        def request(self, method, path, body=None):
            response = super().request(method, path, body)
            if path == "/v2/account":
                clock[0] += timedelta(seconds=61)
            return response

    transport = SlowAccount(
        [
            (
                200,
                {
                    "id": "upstream-1",
                    "status": "ACTIVE",
                    "trading_blocked": False,
                    "account_blocked": False,
                },
            )
        ]
    )
    adapter = AlpacaExecutionProvider(
        "canonical-1",
        "upstream-1",
        transport,
        dispatch_guard=lambda _: None,
        clock=lambda: clock[0],
    )
    with pytest.raises(ExecutionPreflightError):
        asyncio.run(
            adapter.submit_order(command(valid_until=now + timedelta(seconds=60)))
        )
    assert [request[0] for request in transport.requests] == ["GET"]

    transport = authorized_transport([])

    def blocked(_command):
        raise ExecutionError("Kill switch active")

    adapter = AlpacaExecutionProvider(
        "canonical-1",
        "upstream-1",
        transport,
        dispatch_guard=blocked,
        clock=lambda: now,
    )
    with pytest.raises(ExecutionPreflightError):
        asyncio.run(
            adapter.submit_order(command(valid_until=now + timedelta(seconds=60)))
        )
    assert [request[0] for request in transport.requests] == ["GET"]


def test_guard_delay_cannot_expire_quote_before_post():
    from datetime import timedelta

    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock = [now]
    transport = authorized_transport([])

    def delayed_guard(_command):
        clock[0] += timedelta(seconds=61)

    adapter = AlpacaExecutionProvider(
        "canonical-1",
        "upstream-1",
        transport,
        dispatch_guard=delayed_guard,
        clock=lambda: clock[0],
    )
    with pytest.raises(ExecutionPreflightError):
        asyncio.run(
            adapter.submit_order(command(valid_until=now + timedelta(seconds=60)))
        )
    assert [request[0] for request in transport.requests] == ["GET"]
