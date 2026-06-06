"""Test models of all three kinds: dataclass, pydantic, msgspec."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class Role(str, enum.Enum):
    admin = "admin"
    user = "user"


def _empty_tags() -> list[str]:
    return []


@dataclass
class DUser:
    id: int
    name: str
    age: int
    role: Role
    created: datetime
    tags: list[str] = field(default_factory=_empty_tags)
    nickname: str | None = None


def make_pydantic() -> type[Any]:
    import pydantic

    class PUser(pydantic.BaseModel):
        id: int
        name: str
        age: int
        role: Role
        created: datetime
        tags: list[str] = pydantic.Field(default_factory=_empty_tags)
        nickname: str | None = None

    return PUser


def make_msgspec() -> type[Any]:
    import msgspec

    class MUser(msgspec.Struct):
        id: int
        name: str
        age: int
        role: Role
        created: datetime
        tags: list[str] = msgspec.field(default_factory=_empty_tags)
        nickname: str | None = None

    return MUser


# --- projection (view) models: a subset of the fields above ----------------


@dataclass
class DUserBrief:
    id: int
    name: str


def make_pydantic_brief() -> type[Any]:
    import pydantic

    class PUserBrief(pydantic.BaseModel):
        id: int
        name: str

    return PUserBrief


def make_msgspec_brief() -> type[Any]:
    import msgspec

    class MUserBrief(msgspec.Struct):
        id: int
        name: str

    return MUserBrief
