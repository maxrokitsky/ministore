"""Synchronous store on top of the stdlib ``sqlite3``.

Thread safety: WAL + busy_timeout, one connection per thread
(``threading.local``). ``Store`` and ``Collection`` objects can be freely
shared across threads — each thread lazily gets its own connection to the file.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Iterator
from os import PathLike
from types import TracebackType
from typing import Any, Generic, Self, TypeVar

from . import _sql
from ._adapters import get_adapter
from ._base import Mapper
from ._clause import compile_filters, compile_tail
from ._schema import Table, build_table

T = TypeVar("T")


class Store:
    """Local SQLite store (synchronous)."""

    def __init__(self, path: str | PathLike[str], *, timeout: float = 5.0) -> None:
        self.path = str(path)
        self._timeout = timeout
        self._local = threading.local()
        self._lock = threading.Lock()
        self._connections: list[sqlite3.Connection] = []
        self._closed = False

    def connection(self) -> sqlite3.Connection:
        """The connection bound to the current thread (created lazily)."""
        if self._closed:
            raise RuntimeError("Store is closed")
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
            with self._lock:
                self._connections.append(conn)
        return conn

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=self._timeout, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={int(self._timeout * 1000)}")
        return conn

    def collection(
        self,
        model: type[T],
        *,
        key: str | None = None,
        name: str | None = None,
        indexes: Iterable[str] = (),
        unique: Iterable[str] = (),
        create: bool = True,
    ) -> Collection[T]:
        adapter = get_adapter(model)
        table = build_table(
            model,
            adapter,
            name=name or model.__name__,
            key=key,
            indexes=tuple(indexes),
            unique=tuple(unique),
        )
        conn = self.connection()
        rows = conn.execute(_sql.table_info_sql(table)).fetchall()
        _sql.check_schema(table, rows)
        if create and not rows:
            conn.execute(_sql.create_table_sql(table))
            for stmt in _sql.create_index_sql(table):
                conn.execute(stmt)
            conn.commit()
        return Collection(self, model, table, Mapper(model, table, adapter))

    def close(self) -> None:
        self._closed = True
        with self._lock:
            for conn in self._connections:
                conn.close()
            self._connections.clear()
        self._local = threading.local()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class Collection(Generic[T]):
    """A typed collection of objects of a single model."""

    def __init__(self, store: Store, model: type[T], table: Table, mapper: Mapper[T]) -> None:
        self._store = store
        self.model = model
        self.table = table
        self._mapper = mapper

    def put(self, obj: T) -> T:
        conn = self._store.connection()
        conn.execute(_sql.insert_sql(self.table), self._mapper.to_values(obj))
        conn.commit()
        return obj

    def put_many(self, objs: Iterable[T]) -> int:
        conn = self._store.connection()
        rows = [self._mapper.to_values(obj) for obj in objs]
        with conn:
            conn.executemany(_sql.insert_sql(self.table), rows)
        return len(rows)

    def get(self, key: Any) -> T | None:
        conn = self._store.connection()
        row = conn.execute(_sql.get_sql(self.table), (self._mapper.encode_key(key),)).fetchone()
        return None if row is None else self._mapper.from_row(row)

    def delete(self, key: Any) -> bool:
        conn = self._store.connection()
        cur = conn.execute(_sql.delete_one_sql(self.table), (self._mapper.encode_key(key),))
        conn.commit()
        return cur.rowcount > 0

    def where(self, **filters: Any) -> Query[T]:
        return Query(self._store, self.table, self._mapper, dict(filters))

    def all(self) -> list[T]:
        return Query(self._store, self.table, self._mapper, {}).all()

    def count(self) -> int:
        return Query(self._store, self.table, self._mapper, {}).count()

    def clear(self) -> int:
        return Query(self._store, self.table, self._mapper, {}).delete()

    def drop(self) -> None:
        conn = self._store.connection()
        conn.execute(f"DROP TABLE IF EXISTS {_sql.quote(self.table.name)}")
        conn.commit()

    def __iter__(self) -> Iterator[T]:
        return iter(self.all())

    def __len__(self) -> int:
        return self.count()

    def __contains__(self, key: Any) -> bool:
        return self.get(key) is not None


class Query(Generic[T]):
    """A lazy query with filters, ordering and slicing."""

    def __init__(
        self,
        store: Store,
        table: Table,
        mapper: Mapper[T],
        filters: dict[str, Any],
    ) -> None:
        self._store = store
        self._table = table
        self._mapper = mapper
        self._filters = filters
        self._order: list[str] = []
        self._limit: int | None = None
        self._offset: int | None = None

    def where(self, **filters: Any) -> Query[T]:
        merged = {**self._filters, **filters}
        clone = Query(self._store, self._table, self._mapper, merged)
        clone._order = list(self._order)
        clone._limit = self._limit
        clone._offset = self._offset
        return clone

    def order_by(self, *fields: str) -> Self:
        self._order = list(fields)
        return self

    def limit(self, n: int) -> Self:
        self._limit = n
        return self

    def offset(self, n: int) -> Self:
        self._offset = n
        return self

    def all(self) -> list[T]:
        return list(self)

    def first(self) -> T | None:
        for obj in self.limit(1):
            return obj
        return None

    def count(self) -> int:
        where, params = compile_filters(self._table, self._filters)
        conn = self._store.connection()
        row = conn.execute(_sql.count_sql(self._table, where), params).fetchone()
        return int(row[0])

    def exists(self) -> bool:
        return self.count() > 0

    def delete(self) -> int:
        where, params = compile_filters(self._table, self._filters)
        conn = self._store.connection()
        cur = conn.execute(_sql.delete_where_sql(self._table, where), params)
        conn.commit()
        return cur.rowcount

    def __iter__(self) -> Iterator[T]:
        where, params = compile_filters(self._table, self._filters)
        tail = compile_tail(self._table, self._order, self._limit, self._offset)
        conn = self._store.connection()
        for row in conn.execute(_sql.select_sql(self._table, where, tail), params):
            yield self._mapper.from_row(row)
