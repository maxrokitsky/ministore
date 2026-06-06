"""Asynchronous store on top of ``aiosqlite`` (optional dependency).

aiosqlite keeps the connection in a dedicated thread and serializes operations,
so a single connection is safe to use from many coroutines.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from os import PathLike
from types import TracebackType
from typing import Any, Generic, Self, TypeVar

from . import _sql
from ._adapters import get_adapter
from ._base import Mapper
from ._clause import compile_filters, compile_tail
from ._schema import Table, build_table
from .exceptions import MissingDependencyError

T = TypeVar("T")


def _require_aiosqlite() -> Any:
    try:
        import aiosqlite
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise MissingDependencyError(
            "The async API requires aiosqlite. Install it with: pip install ministore[async]"
        ) from exc
    return aiosqlite


class AsyncStore:
    """Local SQLite store (asynchronous)."""

    def __init__(self, path: str | PathLike[str], *, timeout: float = 5.0) -> None:
        self.path = str(path)
        self._timeout = timeout
        self._aiosqlite = _require_aiosqlite()
        self._conn: Any | None = None

    async def connection(self) -> Any:
        if self._conn is None:
            conn = await self._aiosqlite.connect(self.path, timeout=self._timeout)
            conn.row_factory = self._aiosqlite.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA synchronous=NORMAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute(f"PRAGMA busy_timeout={int(self._timeout * 1000)}")
            self._conn = conn
        return self._conn

    async def collection(
        self,
        model: type[T],
        *,
        key: str | None = None,
        name: str | None = None,
        indexes: Iterable[str] = (),
        unique: Iterable[str] = (),
        create: bool = True,
    ) -> AsyncCollection[T]:
        adapter = get_adapter(model)
        table = build_table(
            model,
            adapter,
            name=name or model.__name__,
            key=key,
            indexes=tuple(indexes),
            unique=tuple(unique),
        )
        conn = await self.connection()
        async with conn.execute(_sql.table_info_sql(table)) as cur:
            rows = await cur.fetchall()
        _sql.check_schema(table, rows)
        if create and not rows:
            await conn.execute(_sql.create_table_sql(table))
            for stmt in _sql.create_index_sql(table):
                await conn.execute(stmt)
            await conn.commit()
        return AsyncCollection(self, model, table, Mapper(model, table, adapter))

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()


class AsyncCollection(Generic[T]):
    """A typed asynchronous collection of objects of a single model."""

    def __init__(self, store: AsyncStore, model: type[T], table: Table, mapper: Mapper[T]) -> None:
        self._store = store
        self.model = model
        self.table = table
        self._mapper = mapper

    async def put(self, obj: T) -> T:
        conn = await self._store.connection()
        await conn.execute(_sql.insert_sql(self.table), self._mapper.to_values(obj))
        await conn.commit()
        return obj

    async def put_many(self, objs: Iterable[T]) -> int:
        conn = await self._store.connection()
        rows = [self._mapper.to_values(obj) for obj in objs]
        await conn.executemany(_sql.insert_sql(self.table), rows)
        await conn.commit()
        return len(rows)

    async def get(self, key: Any) -> T | None:
        conn = await self._store.connection()
        async with conn.execute(_sql.get_sql(self.table), (self._mapper.encode_key(key),)) as cur:
            row = await cur.fetchone()
        return None if row is None else self._mapper.from_row(row)

    async def delete(self, key: Any) -> bool:
        conn = await self._store.connection()
        cur = await conn.execute(_sql.delete_one_sql(self.table), (self._mapper.encode_key(key),))
        await conn.commit()
        return cur.rowcount > 0

    def where(self, **filters: Any) -> AsyncQuery[T]:
        return AsyncQuery(self._store, self.table, self._mapper, dict(filters))

    async def all(self) -> list[T]:
        return await AsyncQuery(self._store, self.table, self._mapper, {}).all()

    async def count(self) -> int:
        return await AsyncQuery(self._store, self.table, self._mapper, {}).count()

    async def clear(self) -> int:
        return await AsyncQuery(self._store, self.table, self._mapper, {}).delete()

    async def drop(self) -> None:
        conn = await self._store.connection()
        await conn.execute(f"DROP TABLE IF EXISTS {_sql.quote(self.table.name)}")
        await conn.commit()


class AsyncQuery(Generic[T]):
    """A lazy asynchronous query."""

    def __init__(
        self,
        store: AsyncStore,
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

    def where(self, **filters: Any) -> AsyncQuery[T]:
        merged = {**self._filters, **filters}
        clone = AsyncQuery(self._store, self._table, self._mapper, merged)
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

    async def all(self) -> list[T]:
        where, params = compile_filters(self._table, self._filters)
        tail = compile_tail(self._table, self._order, self._limit, self._offset)
        conn = await self._store.connection()
        async with conn.execute(_sql.select_sql(self._table, where, tail), params) as cur:
            rows = await cur.fetchall()
        return [self._mapper.from_row(row) for row in rows]

    async def first(self) -> T | None:
        for obj in await self.limit(1).all():
            return obj
        return None

    async def count(self) -> int:
        where, params = compile_filters(self._table, self._filters)
        conn = await self._store.connection()
        async with conn.execute(_sql.count_sql(self._table, where), params) as cur:
            row = await cur.fetchone()
        return int(row[0])

    async def exists(self) -> bool:
        return await self.count() > 0

    async def delete(self) -> int:
        where, params = compile_filters(self._table, self._filters)
        conn = await self._store.connection()
        cur = await conn.execute(_sql.delete_where_sql(self._table, where), params)
        await conn.commit()
        return cur.rowcount

    async def __aiter__(self) -> AsyncIterator[T]:
        for obj in await self.all():
            yield obj
