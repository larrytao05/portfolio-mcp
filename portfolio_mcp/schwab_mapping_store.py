from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    select,
)

from portfolio_mcp.database import Database
from portfolio_mcp.models import (
    is_schwab_account_eligible,
    is_schwab_account_masked,
)
from portfolio_mcp.schema import (
    AccountRecord,
    SchwabAccountMappingRecord,
)


@dataclass(frozen=True)
class StoredSchwabAccountMapping:
    id: str
    account_id: str
    schwab_account_hash: str
    masked_account_number: str
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "masked_account_number": self.masked_account_number,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class AccountNotFoundError(KeyError):
    pass


class SchwabAccountMappingConflictError(ValueError):
    pass


def save_schwab_account_mapping(
    db: Database,
    account_id: str,
    schwab_account_hash: str,
    masked_account_number: str,
) -> StoredSchwabAccountMapping:
    normalized_account_id = account_id.strip()
    normalized_hash = schwab_account_hash.strip()
    normalized_masked = masked_account_number.strip()
    if not normalized_account_id or not normalized_hash or not normalized_masked:
        raise ValueError(
            "account_id, schwab_account_hash, and masked_account_number "
            "must not be empty"
        )
    if not is_schwab_account_masked(normalized_masked):
        raise ValueError("masked account number must use the canonical *dddd format")

    with db.sessions() as session:
        account = session.get(AccountRecord, normalized_account_id)
        if account is None:
            raise AccountNotFoundError(
                f"Account '{normalized_account_id}' does not exist"
            )
        if not is_schwab_account_eligible(account.provider, account.currency):
            raise ValueError(
                f"Account '{normalized_account_id}' is not eligible for Schwab mapping"
            )

        existing_with_hash = session.scalar(
            select(SchwabAccountMappingRecord).where(
                SchwabAccountMappingRecord.schwab_account_hash == normalized_hash,
                SchwabAccountMappingRecord.account_id != normalized_account_id,
            )
        )
        if existing_with_hash is not None:
            raise SchwabAccountMappingConflictError(
                "Schwab account hash is already mapped to account "
                f"'{existing_with_hash.account_id}'"
            )

        now = db.clock()
        existing_mapping = session.scalar(
            select(SchwabAccountMappingRecord).where(
                SchwabAccountMappingRecord.account_id == normalized_account_id
            )
        )
        if existing_mapping is not None:
            existing_mapping.schwab_account_hash = normalized_hash
            existing_mapping.masked_account_number = normalized_masked
            existing_mapping.updated_at = now
            session.commit()
            return _stored_schwab_mapping(existing_mapping)

        record = SchwabAccountMappingRecord(
            id=str(uuid4()),
            account_id=normalized_account_id,
            schwab_account_hash=normalized_hash,
            masked_account_number=normalized_masked,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        session.commit()
        return _stored_schwab_mapping(record)


def get_schwab_account_mapping(
    db: Database, account_id: str
) -> StoredSchwabAccountMapping | None:
    with db.sessions() as session:
        record = session.scalar(
            select(SchwabAccountMappingRecord).where(
                SchwabAccountMappingRecord.account_id == account_id.strip()
            )
        )
        return _stored_schwab_mapping(record) if record is not None else None


def get_schwab_account_mapping_by_hash(
    db: Database, schwab_account_hash: str
) -> StoredSchwabAccountMapping | None:
    with db.sessions() as session:
        record = session.scalar(
            select(SchwabAccountMappingRecord).where(
                SchwabAccountMappingRecord.schwab_account_hash
                == schwab_account_hash.strip()
            )
        )
        return _stored_schwab_mapping(record) if record is not None else None


def list_schwab_account_mappings(db: Database) -> list[StoredSchwabAccountMapping]:
    with db.sessions() as session:
        records = session.scalars(
            select(SchwabAccountMappingRecord).order_by(
                SchwabAccountMappingRecord.created_at
            )
        )
        return [_stored_schwab_mapping(r) for r in records]


def delete_schwab_account_mapping(db: Database, account_id: str) -> bool:
    with db.sessions() as session:
        record = session.scalar(
            select(SchwabAccountMappingRecord).where(
                SchwabAccountMappingRecord.account_id == account_id.strip()
            )
        )
        if record is None:
            return False
        session.delete(record)
        session.commit()
        return True


def _stored_schwab_mapping(
    record: SchwabAccountMappingRecord,
) -> StoredSchwabAccountMapping:
    return StoredSchwabAccountMapping(
        id=record.id,
        account_id=record.account_id,
        schwab_account_hash=record.schwab_account_hash,
        masked_account_number=record.masked_account_number
        if is_schwab_account_masked(record.masked_account_number)
        else "Unavailable",
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
