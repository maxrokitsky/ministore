"""Serialization adapters for the supported kinds of models.

Supports dataclasses (stdlib), pydantic v2 and msgspec. An adapter does three
things: list a model's fields, turn an instance into a dict of builtins, and
rebuild an instance from a dict. pydantic/msgspec are imported lazily — they
are only needed when the corresponding models are actually used.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import enum
import uuid as _uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, cast, get_args, get_origin, get_type_hints

from .exceptions import UnsupportedModelError


@dataclass(frozen=True)
class FieldSpec:
    """A model field: name, type annotation, whether it is required.

    ``metadata`` holds the ``Annotated`` extras attached to the field (markers,
    constraints, …) — the source for inline ``Key`` / ``Index`` / ``Unique``.
    """

    name: str
    type: Any
    required: bool
    metadata: tuple[Any, ...] = ()


def _annotated_meta(tp: Any) -> tuple[Any, ...]:
    """Returns the ``Annotated`` metadata of a type, or ``()`` if it is plain."""
    if get_origin(tp) is Annotated:
        return tuple(get_args(tp)[1:])
    return ()


class Adapter:
    """Base interface for a serialization adapter."""

    @classmethod
    def matches(cls, model: type) -> bool:
        raise NotImplementedError

    def fields(self, model: type) -> list[FieldSpec]:
        raise NotImplementedError

    def to_dict(self, instance: Any) -> dict[str, Any]:
        raise NotImplementedError

    def from_dict(self, model: type, data: dict[str, Any]) -> Any:
        raise NotImplementedError


# --- dataclasses -----------------------------------------------------------


def _coerce(tp: Any, value: Any) -> Any:
    """Recursively rebuild nested values for a dataclass.

    Needed because nested structures are stored as JSON and come back as
    dict/list/str without their original types restored.
    """
    if value is None:
        return None

    origin = get_origin(tp)
    if origin is not None:
        args = get_args(tp)
        if origin in (list, set, frozenset, tuple) and args:
            built: list[Any] = [_coerce(args[0], item) for item in value]
            if origin is set:
                return set(built)
            if origin is frozenset:
                return frozenset(built)
            if origin is tuple:
                return tuple(built)
            return built
        if origin is dict and len(args) == 2 and isinstance(value, dict):
            mapping = cast("dict[Any, Any]", value)
            return {k: _coerce(args[1], v) for k, v in mapping.items()}
        return value

    if isinstance(tp, type):
        if dataclasses.is_dataclass(tp) and isinstance(value, dict):
            return _build_dataclass(tp, cast("dict[str, Any]", value))
        if issubclass(tp, enum.Enum):
            return tp(value)

    if tp is _dt.datetime and isinstance(value, str):
        return _dt.datetime.fromisoformat(value)
    if tp is _dt.date and isinstance(value, str):
        return _dt.date.fromisoformat(value)
    if tp is _dt.time and isinstance(value, str):
        return _dt.time.fromisoformat(value)
    if tp is Decimal and not isinstance(value, Decimal):
        return Decimal(value)
    if tp is _uuid.UUID and isinstance(value, str):
        return _uuid.UUID(value)
    return value


def _build_dataclass(model: type, data: dict[str, Any]) -> Any:
    hints = get_type_hints(model, include_extras=False)
    field_names = {f.name for f in dataclasses.fields(model)}
    kwargs = {
        name: _coerce(hints.get(name), value) for name, value in data.items() if name in field_names
    }
    return model(**kwargs)


class DataclassAdapter(Adapter):
    @classmethod
    def matches(cls, model: type) -> bool:
        return dataclasses.is_dataclass(model)

    def fields(self, model: type) -> list[FieldSpec]:
        hints = get_type_hints(model, include_extras=True)
        specs: list[FieldSpec] = []
        for f in dataclasses.fields(model):
            required = f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
            tp = hints.get(f.name, f.type)
            specs.append(FieldSpec(f.name, tp, required, _annotated_meta(tp)))
        return specs

    def to_dict(self, instance: Any) -> dict[str, Any]:
        return dataclasses.asdict(instance)

    def from_dict(self, model: type, data: dict[str, Any]) -> Any:
        return _build_dataclass(model, data)


# --- pydantic v2 -----------------------------------------------------------


class PydanticAdapter(Adapter):
    @classmethod
    def matches(cls, model: type) -> bool:
        try:
            import pydantic
        except ImportError:
            return False
        return issubclass(model, pydantic.BaseModel)

    def fields(self, model: type) -> list[FieldSpec]:
        import pydantic

        assert issubclass(model, pydantic.BaseModel)
        specs: list[FieldSpec] = []
        for name, info in model.model_fields.items():
            specs.append(FieldSpec(name, info.annotation, info.is_required(), tuple(info.metadata)))
        return specs

    def to_dict(self, instance: Any) -> dict[str, Any]:
        return instance.model_dump()

    def from_dict(self, model: type, data: dict[str, Any]) -> Any:
        import pydantic

        assert issubclass(model, pydantic.BaseModel)
        return model.model_validate(data)


# --- msgspec ---------------------------------------------------------------


class MsgspecAdapter(Adapter):
    @classmethod
    def matches(cls, model: type) -> bool:
        try:
            import msgspec
        except ImportError:
            return False
        return issubclass(model, msgspec.Struct)

    def fields(self, model: type) -> list[FieldSpec]:
        import msgspec

        specs: list[FieldSpec] = []
        for f in msgspec.structs.fields(model):
            required = f.default is msgspec.NODEFAULT and f.default_factory is msgspec.NODEFAULT
            specs.append(FieldSpec(f.name, f.type, required, _annotated_meta(f.type)))
        return specs

    def to_dict(self, instance: Any) -> dict[str, Any]:
        import msgspec

        result: dict[str, Any] = msgspec.to_builtins(instance)
        return result

    def from_dict(self, model: type, data: dict[str, Any]) -> Any:
        import msgspec

        return cast(Any, msgspec.convert(data, model, strict=False))


_REGISTRY: list[type[Adapter]] = [DataclassAdapter, PydanticAdapter, MsgspecAdapter]


def get_adapter(model: type) -> Adapter:
    """Returns a suitable adapter for the model, or raises UnsupportedModelError."""
    for adapter_cls in _REGISTRY:
        if adapter_cls.matches(model):
            return adapter_cls()
    raise UnsupportedModelError(
        f"Model {model!r} is not supported. Use a dataclass, pydantic.BaseModel or msgspec.Struct."
    )
