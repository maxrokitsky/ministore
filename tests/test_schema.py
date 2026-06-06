"""Tests for schema derivation and drift detection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from ministore import SchemaMismatchError, Store, UnsupportedModelError


@dataclass
class V1:
    id: int
    name: str


@dataclass
class V2:
    id: int
    name: str
    age: int  # new field — a mismatch against the V1 schema


def test_schema_drift_raises(tmp_path: Path) -> None:
    path = tmp_path / "drift.db"
    with Store(path) as db:
        db.collection(V1, key="id", name="people")
    with Store(path) as db:
        with pytest.raises(SchemaMismatchError) as exc:
            db.collection(V2, key="id", name="people")
        assert "age" in str(exc.value)
        assert "ministore-migrate" in str(exc.value)


def test_same_schema_reopens_fine(tmp_path: Path) -> None:
    path = tmp_path / "ok.db"
    with Store(path) as db:
        db.collection(V1, key="id", name="people")
    with Store(path) as db:
        coll = db.collection(V1, key="id", name="people")
        assert coll.count() == 0


def test_unsupported_model(store_path: Path) -> None:
    class Plain:
        id: int

    with Store(store_path) as db:
        with pytest.raises(UnsupportedModelError):
            db.collection(Plain, key="id")


def test_bad_key_field(store_path: Path) -> None:
    with Store(store_path) as db:
        with pytest.raises(Exception) as exc:
            db.collection(V1, key="nope")
        assert "nope" in str(exc.value)


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "s.db"


def test_indexes_created(tmp_path: Path) -> None:
    with Store(tmp_path / "idx.db") as db:
        coll = db.collection(V2, key="id", indexes=["age"], name="indexed")
        conn = db.connection()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='indexed'"
        ).fetchall()
        names = {r[0] for r in rows}
        assert any("age" in n for n in names)
        coll.put(V2(id=1, name="a", age=5))
        assert coll.where(age=5).first() is not None
