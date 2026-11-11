from datetime import datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import (
    case,
    select,
    update,
)
from sqlalchemy.engine import CursorResult

from portfolio_mcp.authorization_records import (
    ActiveMcpAuthorization,
    _active_mcp_authorization,
)
from portfolio_mcp.database import Database
from portfolio_mcp.order_history import (
    OrderEventActor,
    OrderEventCode,
    OrderEventType,
    require_aware_utc,
)
from portfolio_mcp.order_record_ops import _append_order_event
from portfolio_mcp.schema import (
    CancellationRequestRecord,
    McpAuthorizationRecord,
    OrderAuthorizationRecord,
    OrderDraftRecord,
    OrderRecord,
)
from portfolio_mcp.stored_orders import StoredOrderAuthorization, _stored_authorization


def create_mcp_authorization(
    db: Database,
    *,
    authorization_id: str,
    action: str,
    payload_fingerprint: str,
    account_id: str,
    salt_hex: str,
    digest_hex: str,
    created_at: datetime,
    expires_at: datetime,
    target_draft_id: str | None = None,
    target_cancellation_request_id: str | None = None,
    target_order_id: str | None = None,
) -> None:
    with db.sessions.begin() as session:
        supersede_stmt = update(McpAuthorizationRecord).where(
            McpAuthorizationRecord.action == action,
            McpAuthorizationRecord.consumed_at.is_(None),
            McpAuthorizationRecord.invalidation_reason.is_(None),
        )
        if target_draft_id is not None:
            supersede_stmt = supersede_stmt.where(
                McpAuthorizationRecord.target_draft_id == target_draft_id
            )
        if target_cancellation_request_id is not None:
            supersede_stmt = supersede_stmt.where(
                McpAuthorizationRecord.target_cancellation_request_id
                == target_cancellation_request_id
            )
        session.execute(supersede_stmt.values(invalidation_reason="superseded"))
        session.add(
            McpAuthorizationRecord(
                id=authorization_id,
                action=action,
                target_draft_id=target_draft_id,
                target_cancellation_request_id=target_cancellation_request_id,
                target_order_id=target_order_id,
                payload_fingerprint=payload_fingerprint,
                account_id=account_id,
                salt=salt_hex,
                digest=digest_hex,
                created_at=created_at,
                expires_at=expires_at,
                failed_attempts=0,
            )
        )
        _append_order_event(
            session,
            draft_id=target_draft_id,
            order_id=target_order_id,
            account_id=account_id,
            event_type=OrderEventType.AUTHORIZATION_CREATED,
            actor=OrderEventActor.DASHBOARD,
            occurred_at=created_at,
            details={
                "authorization_id": authorization_id,
                "action": action,
            },
            deduplication_key=f"mcp_authorization:{authorization_id}:created",
        )


def active_mcp_authorization(
    db: Database,
    *,
    action: str,
    target_draft_id: str | None = None,
    target_cancellation_request_id: str | None = None,
) -> ActiveMcpAuthorization | None:
    with db.sessions() as session:
        stmt = select(McpAuthorizationRecord).where(
            McpAuthorizationRecord.action == action,
            McpAuthorizationRecord.consumed_at.is_(None),
            McpAuthorizationRecord.invalidation_reason.is_(None),
        )
        if target_draft_id is not None:
            stmt = stmt.where(McpAuthorizationRecord.target_draft_id == target_draft_id)
        if target_cancellation_request_id is not None:
            stmt = stmt.where(
                McpAuthorizationRecord.target_cancellation_request_id
                == target_cancellation_request_id
            )
        record = session.scalars(
            stmt.order_by(McpAuthorizationRecord.created_at.desc())
        ).first()
        if record is None:
            return None
        return _active_mcp_authorization(record)


def record_mcp_authorization_failure(
    db: Database,
    *,
    action: str,
    reason: str,
    authorization_id: str | None = None,
    target_draft_id: str | None = None,
    target_cancellation_request_id: str | None = None,
    max_attempts: int = 5,
) -> None:
    with db.sessions.begin() as session:
        record = (
            session.get(McpAuthorizationRecord, authorization_id)
            if authorization_id is not None
            else None
        )
        if record is not None and reason == "authorization_expired":
            record.invalidation_reason = "expired"
        elif record is not None and reason == "verification_failed":
            session.execute(
                update(McpAuthorizationRecord)
                .where(
                    McpAuthorizationRecord.id == record.id,
                    McpAuthorizationRecord.consumed_at.is_(None),
                    McpAuthorizationRecord.invalidation_reason.is_(None),
                )
                .values(
                    failed_attempts=McpAuthorizationRecord.failed_attempts + 1,
                    invalidation_reason=case(
                        (
                            McpAuthorizationRecord.failed_attempts + 1 >= max_attempts,
                            "max_attempts_exceeded",
                        ),
                        else_=None,
                    ),
                )
            )
        event_draft_id = (
            record.target_draft_id if record is not None else target_draft_id
        )
        event_request_id = (
            record.target_cancellation_request_id
            if record is not None
            else target_cancellation_request_id
        )
        draft = (
            session.get(OrderDraftRecord, event_draft_id)
            if event_draft_id is not None
            else None
        )
        request = (
            session.get(CancellationRequestRecord, event_request_id)
            if event_request_id is not None
            else None
        )
        event_order_id = (
            record.target_order_id
            if record is not None
            else request.order_id
            if request is not None
            else None
        )
        order = (
            session.get(OrderRecord, event_order_id)
            if event_order_id is not None
            else None
        )
        event_account_id = (
            record.account_id
            if record is not None
            else order.account_id
            if order is not None
            else draft.account_id
            if draft is not None
            else request.account_id
            if request is not None
            else None
        )
        now = require_aware_utc(db.clock())
        attempt_id = str(uuid4())
        _append_order_event(
            session,
            draft_id=event_draft_id if draft is not None else None,
            order_id=order.id if order is not None else None,
            account_id=event_account_id,
            event_type=OrderEventType.AUTHORIZATION_FAILED,
            actor=OrderEventActor.MCP,
            occurred_at=max(now, order.updated_at) if order is not None else now,
            code=OrderEventCode.AUTHORIZATION_INVALID,
            details={
                "attempt_id": attempt_id,
                "action": record.action if record is not None else action,
                "reason": reason,
            },
            deduplication_key=f"mcp_authorization:failure:{attempt_id}",
        )


def mark_mcp_authorization_consumed(
    db: Database, *, authorization_id: str, now: datetime
) -> bool:
    with db.sessions.begin() as session:
        record = session.get(McpAuthorizationRecord, authorization_id)
        stmt = (
            update(McpAuthorizationRecord)
            .where(
                McpAuthorizationRecord.id == authorization_id,
                McpAuthorizationRecord.consumed_at.is_(None),
                McpAuthorizationRecord.invalidation_reason.is_(None),
            )
            .values(consumed_at=now)
        )
        res = cast(CursorResult[object], session.execute(stmt))
        consumed = res.rowcount == 1
        if consumed and record is not None:
            _append_order_event(
                session,
                draft_id=record.target_draft_id,
                order_id=record.target_order_id,
                account_id=record.account_id,
                event_type=OrderEventType.AUTHORIZATION_CONSUMED,
                actor=OrderEventActor.MCP,
                occurred_at=now,
                details={
                    "authorization_id": authorization_id,
                    "action": record.action,
                },
                deduplication_key=f"mcp_authorization:{authorization_id}:consumed",
            )
        return consumed


def authorization_for_draft(
    db: Database, draft_id: str
) -> "StoredOrderAuthorization | None":
    with db.sessions() as session:
        record = session.scalar(
            select(OrderAuthorizationRecord)
            .where(OrderAuthorizationRecord.draft_id == draft_id)
            .order_by(OrderAuthorizationRecord.created_at.desc())
        )
        return _stored_authorization(record) if record is not None else None
