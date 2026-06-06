"""Derives a table schema from a model."""

from __future__ import annotations

from dataclasses import dataclass

from ._adapters import Adapter
from ._typemap import Column, resolve_column
from .exceptions import MinistoreError


@dataclass(frozen=True)
class Table:
    """A table schema derived from a model."""

    name: str
    columns: tuple[Column, ...]
    key: str
    indexes: tuple[str, ...]
    unique: tuple[str, ...]

    @property
    def column_map(self) -> dict[str, Column]:
        return {col.name: col for col in self.columns}

    def column(self, name: str) -> Column:
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(name)


def build_table(
    model: type,
    adapter: Adapter,
    *,
    name: str,
    key: str,
    indexes: tuple[str, ...],
    unique: tuple[str, ...],
) -> Table:
    specs = adapter.fields(model)
    columns = tuple(resolve_column(spec.name, spec.type) for spec in specs)
    names = {col.name for col in columns}

    if key not in names:
        raise MinistoreError(
            f"Key {key!r} not found among the fields of model {model.__name__}: {sorted(names)}"
        )
    for extra in (*indexes, *unique):
        if extra not in names:
            raise MinistoreError(
                f"Indexed field {extra!r} not found among the fields of model "
                f"{model.__name__}: {sorted(names)}"
            )

    return Table(
        name=name,
        columns=columns,
        key=key,
        indexes=indexes,
        unique=unique,
    )
