import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from portfolio_mcp.schema import (
    CancellationRequestRecord,
    McpAuthorizationRecord,
)


@dataclass(frozen=True)
class ActiveMcpAuthorization:
    id: str
    action: str
    target_draft_id: str | None
    target_cancellation_request_id: str | None
    target_order_id: str | None
    payload_fingerprint: str
    account_id: str
    salt: str = field(repr=False)
    digest: str = field(repr=False)
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class StoredCancellationRequest:
    id: str
    order_id: str
    expected_order_version: int
    expected_order_state: str
    account_id: str
    provider: str
    symbol: str
    broker_order_id: str | None
    remaining_quantity: Decimal
    action_fingerprint: str
    created_at: datetime
    expires_at: datetime
    status: str
    invalidation_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "order_id": self.order_id,
            "expected_version": self.expected_order_version,
            "expected_state": self.expected_order_state,
            "account_id": self.account_id,
            "provider": self.provider,
            "symbol": self.symbol,
            "broker_order_id": self.broker_order_id,
            "remaining_quantity": str(self.remaining_quantity),
            "fingerprint": self.action_fingerprint,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "status": self.status,
            "invalidation_reason": self.invalidation_reason,
        }


class McpAuthorizationError(ValueError):
    def __init__(
        self, code: str, message: str = "Invalid or expired authorization code"
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def compute_cancellation_fingerprint(
    *,
    order_id: str,
    expected_version: int,
    expected_state: str,
    account_id: str,
    remaining_quantity: Decimal,
) -> str:
    payload = {
        "order_id": order_id,
        "expected_version": expected_version,
        "expected_state": expected_state,
        "account_id": account_id,
        "remaining_quantity": format(remaining_quantity.normalize(), "f"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _active_mcp_authorization(record: McpAuthorizationRecord) -> ActiveMcpAuthorization:
    return ActiveMcpAuthorization(
        id=record.id,
        action=record.action,
        target_draft_id=record.target_draft_id,
        target_cancellation_request_id=record.target_cancellation_request_id,
        target_order_id=record.target_order_id,
        payload_fingerprint=record.payload_fingerprint,
        account_id=record.account_id,
        salt=record.salt,
        digest=record.digest,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


def _stored_cancellation_request(
    record: CancellationRequestRecord,
) -> StoredCancellationRequest:
    return StoredCancellationRequest(
        id=record.id,
        order_id=record.order_id,
        expected_order_version=record.expected_order_version,
        expected_order_state=record.expected_order_state,
        account_id=record.account_id,
        provider=record.provider,
        symbol=record.symbol,
        broker_order_id=record.broker_order_id,
        remaining_quantity=record.remaining_quantity,
        action_fingerprint=record.action_fingerprint,
        created_at=record.created_at,
        expires_at=record.expires_at,
        status=record.status,
        invalidation_reason=record.invalidation_reason,
    )
