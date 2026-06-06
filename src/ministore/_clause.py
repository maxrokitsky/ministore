"""Parses ``field__op=value`` filters into SQL WHERE / ORDER BY clauses."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from ._schema import Table
from ._sql import quote
from .exceptions import QueryError


def compile_filters(table: Table, filters: dict[str, Any]) -> tuple[str, list[Any]]:
    """Returns (WHERE fragment without the WHERE keyword, params)."""
    clauses: list[str] = []
    params: list[Any] = []

    for raw_key, value in filters.items():
        field, _, op = raw_key.partition("__")
        op = op or "exact"
        try:
            col = table.column(field)
        except KeyError:
            raise QueryError(
                f"Unknown field {field!r} in table {table.name!r}"
            ) from None

        if op in ("exact", "eq"):
            clauses.append(f"{quote(col.name)} = ?")
            params.append(col.encode(value))
        elif op in ("ne", "neq"):
            clauses.append(f"{quote(col.name)} != ?")
            params.append(col.encode(value))
        elif op == "gt":
            clauses.append(f"{quote(col.name)} > ?")
            params.append(col.encode(value))
        elif op == "gte":
            clauses.append(f"{quote(col.name)} >= ?")
            params.append(col.encode(value))
        elif op == "lt":
            clauses.append(f"{quote(col.name)} < ?")
            params.append(col.encode(value))
        elif op == "lte":
            clauses.append(f"{quote(col.name)} <= ?")
            params.append(col.encode(value))
        elif op == "in":
            items = _as_sequence(value)
            if not items:
                clauses.append("0 = 1")  # empty IN — always false
                continue
            placeholders = ", ".join("?" for _ in items)
            clauses.append(f"{quote(col.name)} IN ({placeholders})")
            params.extend(col.encode(item) for item in items)
        elif op == "nin":
            items = _as_sequence(value)
            if not items:
                clauses.append("1 = 1")
                continue
            placeholders = ", ".join("?" for _ in items)
            clauses.append(f"{quote(col.name)} NOT IN ({placeholders})")
            params.extend(col.encode(item) for item in items)
        elif op == "isnull":
            clauses.append(f"{quote(col.name)} IS {'NULL' if value else 'NOT NULL'}")
        elif op == "like":
            clauses.append(f"{quote(col.name)} LIKE ?")
            params.append(str(value))
        elif op == "contains":
            clauses.append(f"{quote(col.name)} LIKE ?")
            params.append(f"%{value}%")
        elif op == "icontains":
            clauses.append(f"{quote(col.name)} LIKE ? COLLATE NOCASE")
            params.append(f"%{value}%")
        elif op == "startswith":
            clauses.append(f"{quote(col.name)} LIKE ?")
            params.append(f"{value}%")
        elif op == "endswith":
            clauses.append(f"{quote(col.name)} LIKE ?")
            params.append(f"%{value}")
        else:
            raise QueryError(f"Unknown operator {op!r} in {raw_key!r}")

    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


def _as_sequence(value: Any) -> list[Any]:
    if isinstance(value, str | bytes):
        return [value]
    if isinstance(value, Iterable):
        return list(value)  # type: ignore[arg-type]
    return [value]


def compile_order(table: Table, order: Sequence[str]) -> str:
    if not order:
        return ""
    parts: list[str] = []
    for field in order:
        descending = field.startswith("-")
        name = field[1:] if descending else field
        if name not in table.column_map:
            raise QueryError(f"Cannot order by unknown field {name!r}")
        parts.append(f"{quote(name)} {'DESC' if descending else 'ASC'}")
    return " ORDER BY " + ", ".join(parts)


def compile_tail(table: Table, order: Sequence[str], limit: int | None, offset: int | None) -> str:
    tail = compile_order(table, order)
    if limit is not None:
        tail += f" LIMIT {int(limit)}"
        if offset is not None:
            tail += f" OFFSET {int(offset)}"
    elif offset is not None:
        tail += f" LIMIT -1 OFFSET {int(offset)}"
    return tail
