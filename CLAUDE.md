# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

The project uses `uv` (Python 3.14+).

```bash
uv sync                         # create .venv and install deps + dev group
uv run pytest                   # run all tests
uv run pytest tests/test_sync.py::test_put_get_roundtrip   # single test
uv run pytest -k thread         # tests matching a name
uv run pyright                  # strict type check (primary) — MUST report 0 errors
uv run mypy                     # strict type check (secondary, src only) — MUST report 0 errors
```

There is no separate build/lint step beyond the two type checkers. `pytest` is
configured with `asyncio_mode = "auto"`, so async tests need no decorator.

## What this is

`ministore` is a tiny local SQLite-backed store for `dataclass` / `pydantic` /
`msgspec` models, exposing mirrored synchronous (`Store`) and asynchronous
(`AsyncStore`) APIs. Storage model: **one real SQLite column per model field**;
composite types (`list`/`dict`/nested models/multi-arm unions) fall back to a
JSON `TEXT` column. See `README.md` for the user-facing API.

## Non-negotiable constraints

- **Zero required dependencies in the core.** The sync path runs on stdlib
  `sqlite3` only. `aiosqlite`, `pydantic` and `msgspec` are *optional extras*
  and must be **imported lazily inside functions/methods** — never at module top
  level. Importing `ministore` must succeed with none of them installed.
  (`_async._require_aiosqlite` and the `import pydantic`/`import msgspec` inside
  adapter methods are the pattern to follow.)
- **`pyright --strict` must stay at 0 errors.** The introspection-heavy modules
  (`_adapters`, `_typemap`) deliberately use `typing.cast(...)` to launder
  `Unknown` types that arise from `get_type_hints` / dynamic model APIs. Prefer
  `cast` over `# type: ignore`.
- **`mypy` (strict) is a secondary check over `src/` only** — its sole job is to
  ensure that projects installing `ministore` under mypy never see errors leak
  out of our `py.typed` sources. Pyright is the primary checker; mypy does not
  gate the test suite. One config caveat: `[tool.mypy].warn_redundant_casts` is
  off, because mypy narrows `isinstance(x: Any, dict)` straight to `dict[Any, Any]`
  and flags the `cast(...)` calls that pyright still needs (it narrows to
  `dict[Unknown]`). pyright's `reportUnnecessaryCast` still polices dead casts.
  When you add a `cast`, run **both** checkers — a fix for one can break the other.
- **Private modules.** Everything under `ministore/` except what is re-exported
  in `__init__.py`'s `__all__` is implementation detail (`_`-prefixed). The
  public contract is exactly that `__all__`.

## Architecture — request flow

A `collection(Model, key=...)` call wires together a pipeline of small,
single-purpose modules. Understanding any change usually means tracing this flow:

```
Model (dataclass/pydantic/msgspec)
  │
  ├─ _adapters.get_adapter(Model)   → picks Adapter via _REGISTRY (first match)
  │     Adapter = { fields(), to_dict(), from_dict() }   ← the only place that
  │                                                        knows the model kind
  ├─ _typemap.resolve_column(name, type)  → Column(sqlite_type, encode, decode)
  │     maps each field's Python type to a SQLite type + value codecs
  ├─ _schema.build_table(...)        → Table (tuple of Columns + key/indexes)
  ├─ _sql.*                          → pure SQL strings (DDL/DML) + check_schema()
  ├─ _clause.compile_filters/order   → turns where(field__op=value) into WHERE/params
  └─ _base.Mapper                    → bridges Model ↔ table row (to_values/from_row)
```

`_sync.py` and `_async.py` are **thin execution layers** over that shared
pipeline. They are near-identical except for `sqlite3` (per-thread connections)
vs `aiosqlite` (`await`). All SQL generation, type mapping, adapter dispatch and
filter compilation are shared — keep logic in the shared modules, not duplicated
across the two layers.

### Key design points (require reading multiple files)

- **Adapters are the model-kind boundary.** To support a new model framework,
  add an `Adapter` subclass and register it in `_adapters._REGISTRY`. Nothing
  else should branch on the model kind.
- **Codecs are intentionally lenient.** `_typemap._base_codec` builds
  `encode`/`decode` pairs that accept either rich objects (`datetime`, `Enum`)
  or already-simplified values, because the three adapters reduce instances to
  builtins differently (e.g. `to_builtins` vs `model_dump` vs `asdict`). Adding
  a new scalar type = extend `_base_codec`. Anything unmapped becomes JSON.
- **Composite/nested values round-trip via JSON.** Dataclasses need
  `_adapters._coerce`/`_build_dataclass` to rebuild nested structures from JSON
  (pydantic/msgspec reconstruct via `model_validate`/`convert`).
- **Schema drift is the core's hard boundary with migrations.** On `collection`,
  the core runs `PRAGMA table_info`, calls `_sql.check_schema`, and either
  creates the table from scratch or raises `SchemaMismatchError`. It **never
  alters** an existing table. Versioned migrations are intentionally out of
  scope — planned as a separate optional `ministore-migrate` package. Do not add
  `ALTER TABLE` logic to the core.
- **Adding a query operator** = add a branch in `_clause.compile_filters`
  (keyed on the `__op` suffix). Values are always parameter-bound; identifiers
  are validated against `Table.column_map` and quoted via `_sql.quote`.
- **Projections (`Query.as_model(View)`) are read-only and schema-free.**
  `_schema.build_projection(base, View, adapter)` builds a `Table` whose columns
  are a subset of the base table's (reused verbatim, so codecs/round-trips are
  identical) but whose `name` is still the base table's. The `Query` then keeps
  two tables: `_table` (base — used for WHERE/ORDER/count/delete and as `FROM`)
  and `_select_table` (what `SELECT` lists + what `Mapper` reconstructs; defaults
  to `_table`, becomes the projection after `as_model`). This is why you can
  filter/sort by a field the view omits. It never creates or checks tables —
  fully decoupled from the schema path. Computed (SQL-expression) fields are a
  planned extension: the "field not in base" branch in `build_projection` is
  where a `Computed(...)` marker would slot in (adding `Column.expression`,
  emitting `expr AS name` in `_sql.select_sql`).
- **Thread/concurrency model.** Sync `Store` is shareable across threads: WAL +
  `busy_timeout`, one lazily-created `sqlite3` connection per thread
  (`threading.local`). Async `AsyncStore` holds a single `aiosqlite` connection
  that serializes operations on its own thread.

## Tests

`tests/` is a package (`tests/__init__.py` exists); tests import shared models
via `from tests.models import ...`. `tests/models.py` defines one model of each
kind (dataclass + `make_pydantic()`/`make_msgspec()` factories). pydantic/msgspec
tests guard with `pytest.importorskip`.
