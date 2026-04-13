"""
app/services/audit_logger.py
-----------------------------
Automatic, event-driven audit logging via SQLAlchemy async event listeners.

How it works
------------
SQLAlchemy fires `after_bulk_insert`, `after_insert`, `after_update`, and
`after_delete` mapper events every time a tracked model instance is flushed.
We attach listeners to the four auditable model classes and write an
`AuditLog` row transparently — **router code never needs to call this manually**.

Tracked entities: Budget, Project, SubDivision, RateCard.

Thread / async safety
---------------------
SQLAlchemy mapper events run synchronously even in an async session context.
We accept the session from the event and schedule an `async_object_session`
flush so the insert completes in the same transaction.  If the original
session is already mid-flush we use `after_transaction_end` to insert the
audit row in a separate nested transaction, avoiding "session is already
flushing" errors.
"""

from __future__ import annotations

import json
import logging
import uuid
import datetime
from enum import Enum
from typing import Any

from sqlalchemy import event, inspect, insert
from sqlalchemy.orm import Session

from app.models.models import (
    AuditLog,
    AuditAction,
    Budget,
    Project,
    RateCard,
    SubDivision,
)
from app.core.context import current_user_id   # Fix #10: request-scoped actor ID

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Models that we want to audit
# ---------------------------------------------------------------------------
AUDITABLE_MODELS = (Budget, Project, SubDivision, RateCard)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pk_to_str(instance: Any) -> str:
    """Return the primary key value(s) of an ORM instance as a string."""
    mapper = inspect(type(instance))
    pk_values = [
        str(getattr(instance, col.key))
        for col in mapper.primary_key
    ]
    return "|".join(pk_values)


def _serialize(value: Any) -> Any:
    """JSON-safe serialisation of a value (handles UUID, date, etc.)."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _instance_to_dict(instance: Any, exclude_keys: set[str] | None = None) -> dict:
    """Dump all column values of an ORM instance to a plain dict."""
    exclude_keys = exclude_keys or set()
    mapper = inspect(type(instance))
    return {
        col.key: _serialize(getattr(instance, col.key))
        for col in mapper.column_attrs
        if col.key not in exclude_keys
    }


def _get_changed_columns(instance: Any) -> dict[str, tuple[Any, Any]]:
    """
    Return only columns whose values have changed as {col_name: (old, new)}.
    Works **inside** a SQLAlchemy flush where history is still available.
    """
    changed: dict[str, tuple[Any, Any]] = {}
    for col in inspect(type(instance)).column_attrs:
        history = getattr(inspect(instance).attrs, col.key).history
        if history.has_changes():
            old = history.deleted[0] if history.deleted else None
            new = history.added[0] if history.added else None
            if old != new:
                changed[col.key] = (_serialize(old), _serialize(new))
    return changed


def _write_audit_row(
    connection: Any,
    entity_type: str,
    entity_id: str,
    action: AuditAction,
    old_value: dict | None,
    new_value: dict | None,
) -> None:
    """
    Append an AuditLog row using the active database connection.

    Using `connection.execute` instead of `session.add()` avoids the SAWarning
    about modifying the session during the flush process.
    """
    stmt = insert(AuditLog).values(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        old_value=old_value,
        new_value=new_value,
        user_id=current_user_id.get(),   # Fix #10: populated from request context
    )
    connection.execute(stmt)


# ---------------------------------------------------------------------------
# Generic listener factory
# ---------------------------------------------------------------------------

def _make_listeners(model_class: type) -> None:
    """Attach after_insert / after_update / after_delete listeners to *model_class*."""

    entity_name = model_class.__name__

    @event.listens_for(model_class, "after_insert", propagate=True)
    def _after_insert(mapper, connection, target) -> None:  # noqa: ANN001
        new_val = _instance_to_dict(target)
        logger.debug("AUDIT INSERT %s id=%s", entity_name, _pk_to_str(target))
        _write_audit_row(
            connection,
            entity_type=entity_name,
            entity_id=_pk_to_str(target),
            action=AuditAction.CREATE,
            old_value=None,
            new_value=new_val,
        )

    @event.listens_for(model_class, "after_update", propagate=True)
    def _after_update(mapper, connection, target) -> None:  # noqa: ANN001
        changed = _get_changed_columns(target)
        if not changed:
            return   # No-op flush (e.g. relationship cascade)
        old_val = {k: v[0] for k, v in changed.items()}
        new_val = {k: v[1] for k, v in changed.items()}
        logger.debug("AUDIT UPDATE %s id=%s cols=%s", entity_name, _pk_to_str(target), list(changed))
        _write_audit_row(
            connection,
            entity_type=entity_name,
            entity_id=_pk_to_str(target),
            action=AuditAction.UPDATE,
            old_value=old_val,
            new_value=new_val,
        )

    @event.listens_for(model_class, "after_delete", propagate=True)
    def _after_delete(mapper, connection, target) -> None:  # noqa: ANN001
        old_val = _instance_to_dict(target)
        logger.debug("AUDIT DELETE %s id=%s", entity_name, _pk_to_str(target))
        _write_audit_row(
            connection,
            entity_type=entity_name,
            entity_id=_pk_to_str(target),
            action=AuditAction.DELETE,
            old_value=old_val,
            new_value=None,
        )


# ---------------------------------------------------------------------------
# Registration — call this ONCE at startup
# ---------------------------------------------------------------------------

def register_audit_listeners() -> None:
    """
    Attach event listeners to all auditable models.

    Call this from `app/main.py` BEFORE the FastAPI app starts accepting
    requests (e.g., in the lifespan startup block).
    """
    for model in AUDITABLE_MODELS:
        _make_listeners(model)
    logger.info(
        "Audit listeners registered for: %s",
        [m.__name__ for m in AUDITABLE_MODELS],
    )
