"""Maps Python types to SQLite columns and provides value codecs.

Each model field becomes its own column. Scalar types go into native SQLite
types (INTEGER/REAL/TEXT/BLOB); everything composite (list/dict/nested
models/multi-arm Union) is serialized to TEXT as JSON.

Codecs are intentionally lenient: ``encode`` may receive either a "rich" object
(``datetime``, ``Enum``) or an already-simplified value — different adapters
(dataclass/pydantic/msgspec) reduce values to builtins differently.
"""

from __future__ import annotations

import datetime as _dt
import enum
import json
import uuid as _uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from types import NoneType, UnionType
from typing import Annotated, Any, Union, cast, get_args, get_origin

Encoder = Callable[[Any], Any]
Decoder = Callable[[Any], Any]


@dataclass(frozen=True)
class Column:
    """Description of a single table column."""

    name: str
    sqlite_type: str  # INTEGER | REAL | TEXT | BLOB
    nullable: bool
    encode: Encoder
    decode: Decoder
    py_type: Any


def _json_default(obj: Any) -> Any:
    if isinstance(obj, _dt.datetime | _dt.date | _dt.time):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, _uuid.UUID):
        return str(obj)
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, set | frozenset | tuple):
        return list(cast("list[Any]", obj))
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON-serializable")


def _json_encode(value: Any) -> str:
    return json.dumps(value, default=_json_default, ensure_ascii=False, separators=(",", ":"))


def _json_decode(value: Any) -> Any:
    return json.loads(value)


def _none_safe(fn: Encoder) -> Encoder:
    def wrapped(value: Any) -> Any:
        return None if value is None else fn(value)

    return wrapped


def _unwrap_annotated(tp: Any) -> Any:
    while get_origin(tp) is Annotated:
        tp = get_args(tp)[0]
    return tp


def _strip_optional(tp: Any) -> tuple[Any, bool]:
    """Unwraps Optional[X] / X | None; returns (type, nullable)."""
    origin = get_origin(tp)
    if origin is Union or origin is UnionType:
        args = [a for a in get_args(tp) if a is not NoneType]
        nullable = len(args) != len(get_args(tp))
        if len(args) == 1:
            return _unwrap_annotated(args[0]), nullable
        # Several non-None arms — store as JSON.
        return Any, nullable
    return tp, False


def _enum_codec(tp: type[enum.Enum]) -> tuple[str, Encoder, Decoder]:
    sample = next(iter(tp)).value
    if isinstance(sample, bool):
        sqlite_type = "INTEGER"
    elif isinstance(sample, int):
        sqlite_type = "INTEGER"
    elif isinstance(sample, float):
        sqlite_type = "REAL"
    else:
        sqlite_type = "TEXT"

    def encode(value: Any) -> Any:
        return value.value if isinstance(value, enum.Enum) else value

    def decode(value: Any) -> Any:
        return tp(value)

    return sqlite_type, encode, decode


def _base_codec(tp: Any) -> tuple[str, Encoder, Decoder]:
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        return _enum_codec(tp)

    if tp is bool:
        return "INTEGER", (lambda v: int(bool(v))), (lambda v: bool(v))
    if tp is int:
        return "INTEGER", (lambda v: int(v)), (lambda v: int(v))
    if tp is float:
        return "REAL", (lambda v: float(v)), (lambda v: float(v))
    if tp is str:
        return "TEXT", (lambda v: v), (lambda v: v)
    if tp is bytes or tp is bytearray:
        return "BLOB", (lambda v: bytes(v)), (lambda v: bytes(v))

    if tp is _dt.datetime:
        return (
            "TEXT",
            (lambda v: v.isoformat() if isinstance(v, _dt.datetime) else v),
            (lambda v: _dt.datetime.fromisoformat(v) if isinstance(v, str) else v),
        )
    if tp is _dt.date:
        return (
            "TEXT",
            (lambda v: v.isoformat() if isinstance(v, _dt.date) else v),
            (lambda v: _dt.date.fromisoformat(v) if isinstance(v, str) else v),
        )
    if tp is _dt.time:
        return (
            "TEXT",
            (lambda v: v.isoformat() if isinstance(v, _dt.time) else v),
            (lambda v: _dt.time.fromisoformat(v) if isinstance(v, str) else v),
        )
    if tp is Decimal:
        return (
            "TEXT",
            (lambda v: str(v)),
            (lambda v: Decimal(v) if not isinstance(v, Decimal) else v),
        )
    if tp is _uuid.UUID:
        return (
            "TEXT",
            (lambda v: str(v)),
            (lambda v: _uuid.UUID(v) if isinstance(v, str) else v),
        )

    # Everything else (list/dict/set/tuple/nested models/Any) — JSON in TEXT.
    return "TEXT", _json_encode, _json_decode


def resolve_column(name: str, tp: Any) -> Column:
    """Builds a column descriptor for a model field from its type annotation."""
    inner, nullable = _strip_optional(_unwrap_annotated(tp))
    sqlite_type, encode, decode = _base_codec(inner)
    return Column(
        name=name,
        sqlite_type=sqlite_type,
        nullable=nullable,
        encode=_none_safe(encode),
        decode=_none_safe(decode),
        py_type=inner,
    )
