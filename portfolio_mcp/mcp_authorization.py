from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from portfolio_mcp.database import (
    McpAuthorizationError,
    PortfolioRepository,
    StoredCancellationRequest,
)


def _utc_now(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass(frozen=True)
class CreatedMcpAuthorization:
    id: str
    action: str
    account_id: str
    plaintext_code: str = field(repr=False)
    created_at: datetime
    expires_at: datetime
    target_draft_id: str | None = None
    target_cancellation_request_id: str | None = None
    target_order_id: str | None = None


@dataclass(frozen=True)
class StoredMcpAuthorization:
    id: str
    action: str
    payload_fingerprint: str
    account_id: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None
    target_draft_id: str | None = None
    target_cancellation_request_id: str | None = None
    target_order_id: str | None = None


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
        target_draft_id: str | None = None,
        target_cancellation_request_id: str | None = None,
        target_order_id: str | None = None,
        payload_fingerprint: str | None = None,
        account_id: str | None = None,
    ) -> CreatedMcpAuthorization:
        now = _utc_now(self._clock())
        if action == "submit":
            if target_draft_id is None:
                raise McpAuthorizationError(
                    "draft_not_found", "Target draft ID required for submit"
                )
            draft = self._repository.order_draft(target_draft_id)
            if draft is None:
                raise McpAuthorizationError("draft_not_found", "Order draft not found")
            if now > draft.expires_at:
                raise McpAuthorizationError("draft_expired", "Order draft has expired")
            payload_fingerprint = draft.fingerprint
            account_id = draft.account_id
            expires_at = min(now + timedelta(minutes=5), draft.expires_at)
        elif action == "cancel":
            if (
                target_cancellation_request_id is None
                or target_order_id is None
                or payload_fingerprint is None
                or account_id is None
            ):
                raise McpAuthorizationError(
                    "invalid_request", "Missing required target information for cancel"
                )
            expires_at = now + timedelta(minutes=5)
        else:
            raise McpAuthorizationError(
                "invalid_action", f"Unsupported authorization action: {action}"
            )

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

        self._repository.create_mcp_authorization(
            authorization_id=auth_id,
            action=action,
            target_draft_id=target_draft_id,
            target_cancellation_request_id=target_cancellation_request_id,
            target_order_id=target_order_id,
            payload_fingerprint=payload_fingerprint,
            account_id=account_id,
            salt_hex=salt.hex(),
            digest_hex=digest.hex(),
            created_at=now,
            expires_at=expires_at,
        )

        return CreatedMcpAuthorization(
            id=auth_id,
            action=action,
            target_draft_id=target_draft_id,
            target_cancellation_request_id=target_cancellation_request_id,
            target_order_id=target_order_id,
            account_id=account_id,
            plaintext_code=plaintext_code,
            created_at=now,
            expires_at=expires_at,
        )

    def authorize_cancellation_request(
        self,
        request_id: str,
        *,
        expected_fingerprint: str,
        now: datetime | None = None,
    ) -> tuple[StoredCancellationRequest, CreatedMcpAuthorization]:
        current_time = _utc_now(self._clock() if now is None else now)
        expires_at = current_time + timedelta(minutes=5)
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
        stored_req, order_id = self._repository.authorize_cancellation_request(
            request_id=request_id,
            expected_fingerprint=expected_fingerprint,
            authorization_id=auth_id,
            salt_hex=salt.hex(),
            digest_hex=digest.hex(),
            now=current_time,
            expires_at=expires_at,
        )
        created = CreatedMcpAuthorization(
            id=auth_id,
            action="cancel",
            target_draft_id=None,
            target_cancellation_request_id=stored_req.id,
            target_order_id=order_id,
            account_id=stored_req.account_id,
            plaintext_code=plaintext_code,
            created_at=current_time,
            expires_at=expires_at,
        )
        return stored_req, created

    def consume_authorization(
        self,
        *,
        action: str,
        target_draft_id: str | None = None,
        target_cancellation_request_id: str | None = None,
        expected_fingerprint: str,
        account_id: str,
        candidate_code: str,
    ) -> StoredMcpAuthorization:
        now = _utc_now(self._clock())
        record = self._repository.active_mcp_authorization(
            action=action,
            target_draft_id=target_draft_id,
            target_cancellation_request_id=target_cancellation_request_id,
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
            action=record.action,
            target_draft_id=record.target_draft_id,
            target_cancellation_request_id=record.target_cancellation_request_id,
            target_order_id=record.target_order_id,
            payload_fingerprint=record.payload_fingerprint,
            account_id=record.account_id,
            created_at=record.created_at,
            expires_at=record.expires_at,
            consumed_at=now,
        )
