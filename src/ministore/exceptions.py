"""ministore exceptions."""

from __future__ import annotations


class MinistoreError(Exception):
    """Base class for all ministore errors."""


class UnsupportedModelError(MinistoreError, TypeError):
    """The model type is not supported by any serialization adapter."""


class SchemaMismatchError(MinistoreError):
    """An existing table's schema does not match the model.

    The ministore core creates tables from scratch but deliberately never
    alters existing ones. For versioned migrations use the separate
    ``ministore-migrate`` package.
    """


class MissingDependencyError(MinistoreError, ImportError):
    """A feature was requested that requires an optional dependency."""


class QueryError(MinistoreError, ValueError):
    """Invalid query: unknown field or operator."""
