"""Tests for the asynchronous AsyncStore/AsyncCollection/AsyncQuery."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from ministore import AsyncStore
from tests.models import DUser, Role

pytest.importorskip("aiosqlite")


def make_user(uid: int, name: str, age: int) -> DUser:
    return DUser(id=uid, name=name, age=age, role=Role.user, created=datetime(2020, 1, 1, 12, 0))


async def test_async_crud(tmp_path: Path) -> None:
    async with AsyncStore(tmp_path / "a.db") as db:
        users = await db.collection(DUser, key="id")
        await users.put(make_user(1, "Anna", 30))
        got = await users.get(1)
        assert got is not None and got.name == "Anna"
        assert await users.delete(1) is True
        assert await users.get(1) is None


async def test_async_put_many_and_query(tmp_path: Path) -> None:
    async with AsyncStore(tmp_path / "a.db") as db:
        users = await db.collection(DUser, key="id")
        await users.put_many([make_user(i, f"u{i}", 10 * i) for i in range(1, 5)])
        assert await users.count() == 4

        adults = await users.where(age__gte=20).order_by("-age").all()
        assert [u.id for u in adults] == [4, 3, 2]

        ids = {u.id async for u in users.where(age__lt=30)}
        assert ids == {1, 2}

        first = await users.where(age__gte=30).order_by("age").first()
        assert first is not None and first.id == 3

        assert await users.where(age__lt=30).delete() == 2
        assert await users.count() == 2


async def test_async_persistence(tmp_path: Path) -> None:
    path = tmp_path / "ap.db"
    async with AsyncStore(path) as db:
        users = await db.collection(DUser, key="id")
        await users.put(make_user(7, "x", 7))
    async with AsyncStore(path) as db:
        users = await db.collection(DUser, key="id")
        got = await users.get(7)
        assert got is not None and got.name == "x"
