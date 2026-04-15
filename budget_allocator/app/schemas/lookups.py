from __future__ import annotations

import uuid
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")

class LookupItemOut(BaseModel, Generic[T]):
    """Generic lightweight schema for dropdown lookups."""
    model_config = ConfigDict(from_attributes=True)
    
    id: T
    name: str

class FamilyLookupOut(LookupItemOut[T]):
    """Lookup schema for Families that includes the generic business unit."""
    business_unit: str
