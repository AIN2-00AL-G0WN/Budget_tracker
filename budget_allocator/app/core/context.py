"""
app/core/context.py
--------------------
Request-scoped context variables.

We use Python's ``contextvars.ContextVar`` to propagate per-request state
(e.g., the authenticated user's ID) into code that has no direct access to
the HTTP request — specifically the SQLAlchemy audit event listeners, which
run synchronously and cannot accept FastAPI ``Depends`` arguments.

Usage
-----
Set the variable inside a FastAPI dependency:

    current_user_id.set(user.id)

Read it anywhere in the same async task (including sync event listeners
called from the same coroutine chain):

    uid = current_user_id.get()   # Returns None if not set
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Optional

# Holds the UUID of the currently authenticated user for the duration of
# one HTTP request.  Defaults to None for unauthenticated / system calls.
current_user_id: ContextVar[Optional[uuid.UUID]] = ContextVar(
    "current_user_id",
    default=None,
)

# Holds mandatory audit reasons injected from API controllers boundaries
current_change_reason: ContextVar[Optional[str]] = ContextVar(
    "current_change_reason",
    default=None,
)
