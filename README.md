# ministore

A tiny local **SQLite**-backed store for your models — `dataclass`, `pydantic`
or `msgspec`. Synchronous and asynchronous, fully typed, with minimal code.

```python
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
user = users.get(1)                                   # -> User | None
adults = users.where(age__gte=18).order_by("-age").all()
```

## Principles

- **No required dependencies.** The synchronous core runs on the stdlib
  `sqlite3`. `pip install ministore` and you're done.
- **Familiar models.** No ministore base classes: use your own `dataclass` /
  `pydantic.BaseModel` / `msgspec.Struct`. The adapter is detected
  automatically.
- **A column per field.** Every model field becomes a real SQLite column, so you
  can filter, sort and index by any field. Composite types (`list`, `dict`,
  nested models) are stored as JSON.
- **One API for sync and async.** `Store` and `AsyncStore` mirror each other.

## Installation

```bash
pip install ministore                 # core (sync only, stdlib)
pip install "ministore[async]"        # + async API (aiosqlite)
pip install "ministore[pydantic]"     # + pydantic model support
pip install "ministore[msgspec]"      # + msgspec model support
pip install "ministore[all]"          # everything
```

`dataclass` models work with zero dependencies.

## Queries

`where(**filters)` accepts filters in the `field__op=value` form (the default
operator is equality). It returns a lazy `Query` / `AsyncQuery`.

| Operator | SQL | Example |
|---|---|---|
| `exact` (default) | `=` | `where(name="Anna")` |
| `ne` | `!=` | `where(status__ne="off")` |
| `gt` `gte` `lt` `lte` | `> >= < <=` | `where(age__gte=18)` |
| `in` / `nin` | `IN` / `NOT IN` | `where(id__in=[1, 2, 3])` |
| `isnull` | `IS [NOT] NULL` | `where(deleted__isnull=True)` |
| `like` | `LIKE` (your own pattern) | `where(name__like="A%")` |
| `contains` / `icontains` | `LIKE %v%` | `where(name__contains="nn")` |
| `startswith` / `endswith` | `LIKE v%` / `%v` | `where(name__startswith="A")` |

```python
q = users.where(age__gte=18, name__startswith="A")
q.order_by("-age").limit(10).offset(0)

q.all()        # list[User]
q.first()      # User | None
q.count()      # int
q.exists()     # bool
q.delete()     # int — delete all matches, return the count

for u in users.where(age__gte=18):   # iterates lazily
    ...
```

The collection itself is convenient too:

```python
users.put_many([...])       # bulk insert (upsert by key)
users.all()                 # list[User]
users.count()  / len(users)
3 in users                  # is there a record with this key
users.delete(3)             # delete by key -> bool
users.clear()               # empty the collection -> int
users.drop()                # drop the table
```

## Indexes

```python
users = db.collection(
    User,
    key="id",
    indexes=["age"],      # plain index
    unique=["email"],     # unique index
    name="users",         # table name (defaults to the class name)
)
```

## Async API

Fully mirrors the sync one; methods are coroutines (`pip install
"ministore[async]"`):

```python
from ministore import AsyncStore

async with AsyncStore("app.db") as db:
    users = await db.collection(User, key="id")
    await users.put(User(1, "Anna", 30))
    user = await users.get(1)

    async for u in users.where(age__gte=18):
        ...
    adults = await users.where(age__gte=18).order_by("-age").all()
```

## Thread safety

A `Store` can be freely shared across threads: **WAL** mode is enabled, a
`busy_timeout` is set, and each thread lazily gets its own connection to the
file. An `AsyncStore` keeps a single connection that `aiosqlite` serves on a
dedicated thread and serializes, so it is safe to use from many coroutines.

## Schema and migrations

The core creates the table from the model on first access (`CREATE TABLE IF NOT
EXISTS`) and never **touches** an existing schema afterwards. If the model has
diverged from the table in the database, `collection(...)` raises a
`SchemaMismatchError` describing the difference:

```
SchemaMismatchError: Schema of table 'users' does not match the model:
  - missing column 'age' (INTEGER)
The ministore core does not alter existing tables.
Install 'ministore-migrate' for versioned migrations.
```

Versioned migrations are the job of a separate, optional package
**`ministore-migrate`**, keeping the core minimal. For now, a handy way to
evolve the schema without migrations is to put optional data into composite
fields (they go into a JSON column and don't change the schema).

## Type mapping

| Python | SQLite |
|---|---|
| `int`, `bool` | `INTEGER` |
| `float` | `REAL` |
| `str` | `TEXT` |
| `bytes` | `BLOB` |
| `datetime` / `date` / `time` | `TEXT` (ISO 8601) |
| `Decimal`, `UUID` | `TEXT` |
| `Enum` | by value type (`INTEGER`/`REAL`/`TEXT`) |
| `list` / `dict` / `set` / nested models / `X \| Y` | `TEXT` (JSON) |
| `Optional[T]` / `T \| None` | same column as `T`, but `NULL`-able |

## Development

```bash
uv sync            # environment + dev dependencies
uv run pytest      # tests
uv run pyright     # strict type checking
```

## Author

Max Rokitsky — <max@rokitsky.ru>
