"""Logic shared by the sync/async layers: mapping model <-> table row."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Generic, TypeVar, cast

from ._adapters import Adapter
from ._schema import Table

T = TypeVar("T")


class Mapper(Generic[T]):
    """Converts model instances into row values and back."""

    def __init__(self, model: type[T], table: Table, adapter: Adapter) -> None:
        self.model = model
        self.table = table
        self.adapter = adapter

    def to_values(self, obj: T) -> list[Any]:
        data = self.adapter.to_dict(obj)
        return [col.encode(data.get(col.name)) for col in self.table.columns]

    def key_of(self, obj: T) -> Any:
        data = self.adapter.to_dict(obj)
        return self.table.column(self.table.key).encode(data.get(self.table.key))

    def encode_key(self, key: Any) -> Any:
        return self.table.column(self.table.key).encode(key)

    def from_row(self, row: Mapping[str, Any]) -> T:
        data = {col.name: col.decode(row[col.name]) for col in self.table.columns}
        return cast(T, self.adapter.from_dict(self.model, data))
