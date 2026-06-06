"""ministore — a tiny local SQLite-backed store.

Store dataclass / pydantic / msgspec models in a local file, synchronously or
asynchronously, with minimal code:

    from dataclasses import dataclass
    from ministore import Store

    @dataclass
    class User:
        id: int
        name: str
        age: int

    db = Store("app.db")
    users = db.collection(User, key="id")
    users.put(User(1, "Anna", 30))
    adults = users.where(age__gte=18).order_by("-age").all()
"""

from __future__ import annotations

from ._async import AsyncCollection, AsyncQuery, AsyncStore
from ._markers import Index, Key, Unique
from ._sync import Collection, Query, Store
from .exceptions import (
    MinistoreError,
    MissingDependencyError,
    QueryError,
    SchemaMismatchError,
    UnsupportedModelError,
)

__version__ = "0.1.0"

__all__ = [
    "Store",
    "Collection",
    "Query",
    "AsyncStore",
    "AsyncCollection",
    "AsyncQuery",
    "Key",
    "Index",
    "Unique",
    "MinistoreError",
    "MissingDependencyError",
    "QueryError",
    "SchemaMismatchError",
    "UnsupportedModelError",
    "__version__",
]
