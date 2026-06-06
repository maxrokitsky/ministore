"""Derives a table schema from a model."""

from __future__ import annotations

from dataclasses import dataclass

from ._adapters import Adapter, FieldSpec
from ._markers import marker_kind
from ._typemap import Column, resolve_column
from .exceptions import MinistoreError, QueryError


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
    key: str | None = None,
    indexes: tuple[str, ...] = (),
    unique: tuple[str, ...] = (),
) -> Table:
    specs = adapter.fields(model)
    columns = tuple(resolve_column(spec.name, spec.type) for spec in specs)
    names = {col.name for col in columns}

    resolved_key = _resolve_key(model, specs, key)
    resolved_indexes = _merge_indexed(indexes, specs, "index")
    resolved_unique = _merge_indexed(unique, specs, "unique")

    if resolved_key not in names:
        raise MinistoreError(
            f"Key {resolved_key!r} not found among the fields of model "
            f"{model.__name__}: {sorted(names)}"
        )
    for extra in (*resolved_indexes, *resolved_unique):
        if extra not in names:
            raise MinistoreError(
                f"Indexed field {extra!r} not found among the fields of model "
                f"{model.__name__}: {sorted(names)}"
            )

    return Table(
        name=name,
        columns=columns,
        key=resolved_key,
        indexes=resolved_indexes,
        unique=resolved_unique,
    )


def _marked_fields(specs: list[FieldSpec], kind: str) -> list[str]:
    """Field names carrying a marker of the given kind, in declaration order."""
    return [
        spec.name
        for spec in specs
        for meta in spec.metadata
        if marker_kind(meta) == kind
    ]


def _resolve_key(model: type, specs: list[FieldSpec], key: str | None) -> str:
    """Reconciles an explicit ``key=`` argument with any ``Key`` marker."""
    marked = _marked_fields(specs, "key")
    if len(marked) > 1:
        raise MinistoreError(
            f"Model {model.__name__} declares more than one Key marker "
            f"({sorted(marked)}); a table has exactly one primary key."
        )
    marked_key = marked[0] if marked else None

    if key is not None and marked_key is not None and key != marked_key:
        raise MinistoreError(
            f"Conflicting key for model {model.__name__}: collection(key={key!r}) "
            f"but field {marked_key!r} is annotated with Key."
        )
    resolved = key if key is not None else marked_key
    if resolved is None:
        raise MinistoreError(
            f"No key specified for model {model.__name__}: pass collection(key=...) "
            f"or annotate a field with Key."
        )
    return resolved


def _merge_indexed(explicit: tuple[str, ...], specs: list[FieldSpec], kind: str) -> tuple[str, ...]:
    """Unions explicit index fields with marked ones, preserving order and de-duping."""
    seen: set[str] = set()
    out: list[str] = []
    for field in (*explicit, *_marked_fields(specs, kind)):
        if field not in seen:
            seen.add(field)
            out.append(field)
    return tuple(out)


def build_projection(base: Table, model: type, adapter: Adapter) -> Table:
    """Builds a read-only projection of ``base`` shaped like ``model``.

    The projection's columns are a subset of ``base``'s, in the order declared
    by the view model, with the base columns (and their codecs) reused verbatim:
    values were written with the base column's codec, so they read back to the
    original Python type. The view model's field types are only for the type
    checker and reconstruction — they do not affect decoding.

    The table ``name`` (the ``FROM`` clause) stays the base table's, so a
    projected query selects fewer columns from the same table. Raises
    ``QueryError`` if a view field is not a column of the base table.
    """
    specs = adapter.fields(model)
    if not specs:
        raise QueryError(f"Projection model {model.__name__} has no fields")
    base_map = base.column_map
    cols: list[Column] = []
    for spec in specs:
        if spec.name not in base_map:
            raise QueryError(
                f"Field {spec.name!r} of projection {model.__name__} is not a "
                f"column of table {base.name!r}: {sorted(base_map)}"
            )
        cols.append(base_map[spec.name])  # reuse the base column as-is
    key = base.key if base.key in {col.name for col in cols} else cols[0].name
    return Table(name=base.name, columns=tuple(cols), key=key, indexes=(), unique=())
