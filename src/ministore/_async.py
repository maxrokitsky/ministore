"""Asynchronous store on top of ``aiosqlite`` (optional dependency).

aiosqlite keeps the connection in a dedicated thread and serializes operations,
so a single connection is safe to use from many coroutines.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from os import PathLike
from types import TracebackType
from typing import Any, Generic, Self, TypeVar

from . import _sql
from ._adapters import get_adapter
from ._base import Mapper
from ._clause import compile_filters, compile_tail
from ._schema import Table, build_projection, build_table
from .exceptions import MissingDependencyError

T = TypeVar("T")
P = TypeVar("P")
R = TypeVar("R")


async def _run_atomic(store: AsyncStore, work: Callable[[], Awaitable[R]]) -> R:
    """Run ``work`` against the store's connection, serialized and atomic.

    Inside the running task's own transaction it just runs (joining it).
    Otherwise it wraps ``work`` in a fresh transaction: the outer ``begin`` waits
    for any transaction held by another task, so ``work`` can never interleave
    into someone else's open transaction. Uses only the public primitives.
    """
    if store.in_transaction:
        return await work()
    await store.begin()
    try:
        result = await work()
    except BaseException:
        await store.rollback()
        raise
    await store.commit()
    return result


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
        # The single connection is shared by all coroutines; this lock keeps a
        # transaction's BEGIN..COMMIT window exclusive so other tasks' autonomous
        # operations cannot interleave into it.
        self._lock = asyncio.Lock()
        self._tx_depth = 0
        self._tx_task: asyncio.Task[Any] | None = None

    async def connection(self) -> Any:
        if self._conn is None:
            # isolation_level=None: manual transaction control (see _sync._connect).
            conn = await self._aiosqlite.connect(
                self.path, timeout=self._timeout, isolation_level=None
            )
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

            async def create_table() -> None:
                await conn.execute(_sql.create_table_sql(table))
                for stmt in _sql.create_index_sql(table):
                    await conn.execute(stmt)

            await _run_atomic(self, create_table)
        return AsyncCollection(self, model, table, Mapper(model, table, adapter))

    # -- transactions -----------------------------------------------------

    def _owns_tx(self) -> bool:
        """Whether the running task is the owner of the open transaction."""
        task = asyncio.current_task()
        return task is not None and task is self._tx_task

    @property
    def in_transaction(self) -> bool:
        """Whether the running task is inside an open transaction it owns."""
        return self._owns_tx()

    async def begin(self) -> None:
        """Open a transaction (or a nested SAVEPOINT) for the running task.

        The outermost ``begin`` waits for any transaction held by another task,
        then takes exclusive use of the connection until ``commit``/``rollback``.
        """
        conn = await self.connection()
        if not self._owns_tx():
            await self._lock.acquire()
            self._tx_task = asyncio.current_task()
            await conn.execute(_sql.BEGIN_SQL)
            self._tx_depth = 1
        else:
            await conn.execute(_sql.savepoint_sql(_sql.savepoint_name(self._tx_depth)))
            self._tx_depth += 1

    async def commit(self) -> None:
        """Commit the innermost open transaction (or release its SAVEPOINT)."""
        if not self._owns_tx():
            raise RuntimeError("commit() called without an active transaction")
        conn = await self.connection()
        self._tx_depth -= 1
        if self._tx_depth == 0:
            try:
                await conn.execute(_sql.COMMIT_SQL)
            finally:
                self._tx_task = None
                self._lock.release()
        else:
            await conn.execute(_sql.release_sql(_sql.savepoint_name(self._tx_depth)))

    async def rollback(self) -> None:
        """Roll back the innermost open transaction (or its SAVEPOINT)."""
        if not self._owns_tx():
            raise RuntimeError("rollback() called without an active transaction")
        conn = await self.connection()
        self._tx_depth -= 1
        if self._tx_depth == 0:
            try:
                await conn.execute(_sql.ROLLBACK_SQL)
            finally:
                self._tx_task = None
                self._lock.release()
        else:
            name = _sql.savepoint_name(self._tx_depth)
            await conn.execute(_sql.rollback_to_sql(name))
            await conn.execute(_sql.release_sql(name))

    def transaction(self) -> _AsyncTransaction:
        """Async context manager over :meth:`begin`/:meth:`commit`/:meth:`rollback`."""
        return _AsyncTransaction(self)

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


class _AsyncTransaction:
    """Thin async context manager over ``AsyncStore.begin``/``commit``/``rollback``."""

    def __init__(self, store: AsyncStore) -> None:
        self._store = store

    async def __aenter__(self) -> Self:
        await self._store.begin()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            await self._store.commit()
        else:
            await self._store.rollback()


class AsyncCollection(Generic[T]):
    """A typed asynchronous collection of objects of a single model."""

    def __init__(self, store: AsyncStore, model: type[T], table: Table, mapper: Mapper[T]) -> None:
        self._store = store
        self.model = model
        self.table = table
        self._mapper = mapper

    async def put(self, obj: T) -> T:
        conn = await self._store.connection()
        await _run_atomic(
            self._store,
            lambda: conn.execute(_sql.insert_sql(self.table), self._mapper.to_values(obj)),
        )
        return obj

    async def put_many(self, objs: Iterable[T]) -> int:
        rows = [self._mapper.to_values(obj) for obj in objs]
        conn = await self._store.connection()
        await _run_atomic(self._store, lambda: conn.executemany(_sql.insert_sql(self.table), rows))
        return len(rows)

    async def get(self, key: Any) -> T | None:
        conn = await self._store.connection()

        async def work() -> Any:
            async with conn.execute(
                _sql.get_sql(self.table), (self._mapper.encode_key(key),)
            ) as cur:
                return await cur.fetchone()

        row = await _run_atomic(self._store, work)
        return None if row is None else self._mapper.from_row(row)

    async def delete(self, key: Any) -> bool:
        conn = await self._store.connection()

        async def work() -> int:
            cur = await conn.execute(_sql.delete_one_sql(self.table), (self._mapper.encode_key(key),))
            return cur.rowcount

        return await _run_atomic(self._store, work) > 0

    def where(self, **filters: Any) -> AsyncQuery[T]:
        return AsyncQuery(self._store, self.table, self._mapper, dict(filters))

    def as_model(self, model: type[P]) -> AsyncQuery[P]:
        return self.where().as_model(model)

    async def all(self) -> list[T]:
        return await AsyncQuery(self._store, self.table, self._mapper, {}).all()

    async def count(self) -> int:
        return await AsyncQuery(self._store, self.table, self._mapper, {}).count()

    async def clear(self) -> int:
        return await AsyncQuery(self._store, self.table, self._mapper, {}).delete()

    async def drop(self) -> None:
        conn = await self._store.connection()
        await _run_atomic(
            self._store, lambda: conn.execute(f"DROP TABLE IF EXISTS {_sql.quote(self.table.name)}")
        )


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
        self._select_table: Table = table
        self._order: list[str] = []
        self._limit: int | None = None
        self._offset: int | None = None

    def where(self, **filters: Any) -> AsyncQuery[T]:
        merged = {**self._filters, **filters}
        clone = AsyncQuery(self._store, self._table, self._mapper, merged)
        clone._select_table = self._select_table
        clone._order = list(self._order)
        clone._limit = self._limit
        clone._offset = self._offset
        return clone

    def as_model(self, model: type[P]) -> AsyncQuery[P]:
        """Returns a read-only projection of this query shaped like ``model``.

        ``model`` declares a subset of the collection's fields; ``.all()`` then
        yields ``model`` instances built from just those columns. Filters and
        ordering still run against the full table — you can filter or sort by a
        field the projection omits.
        """
        adapter = get_adapter(model)
        view_table = build_projection(self._table, model, adapter)
        view_mapper: Mapper[P] = Mapper(model, view_table, adapter)
        clone: AsyncQuery[P] = AsyncQuery(self._store, self._table, view_mapper, dict(self._filters))
        clone._select_table = view_table
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

        async def work() -> list[Any]:
            async with conn.execute(
                _sql.select_sql(self._select_table, where, tail), params
            ) as cur:
                return await cur.fetchall()

        rows = await _run_atomic(self._store, work)
        return [self._mapper.from_row(row) for row in rows]

    async def first(self) -> T | None:
        for obj in await self.limit(1).all():
            return obj
        return None

    async def count(self) -> int:
        where, params = compile_filters(self._table, self._filters)
        conn = await self._store.connection()

        async def work() -> Any:
            async with conn.execute(_sql.count_sql(self._table, where), params) as cur:
                return await cur.fetchone()

        row = await _run_atomic(self._store, work)
        return int(row[0])

    async def exists(self) -> bool:
        return await self.count() > 0

    async def delete(self) -> int:
        where, params = compile_filters(self._table, self._filters)
        conn = await self._store.connection()

        async def work() -> int:
            cur = await conn.execute(_sql.delete_where_sql(self._table, where), params)
            return cur.rowcount

        return await _run_atomic(self._store, work)

    async def __aiter__(self) -> AsyncIterator[T]:
        for obj in await self.all():
            yield obj
