"""Dialect-portable SQL expressions.

SQLite and PostgreSQL have no common way to subtract two timestamps, so the
duration used by the utilisation and reporting queries is compiled per dialect.
"""
from sqlalchemy import Float, func
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement


class duration_hours(FunctionElement):
    """Hours between two datetime columns: duration_hours(starts_at, ends_at)."""

    type = Float()
    inherit_cache = True
    name = "duration_hours"


@compiles(duration_hours)
def _duration_hours_default(element, compiler, **kw):
    # PostgreSQL: timestamp subtraction yields an interval; epoch is in seconds.
    start, end = list(element.clauses)
    return compiler.process(func.extract("epoch", end - start) / 3600.0, **kw)


@compiles(duration_hours, "sqlite")
def _duration_hours_sqlite(element, compiler, **kw):
    start, end = list(element.clauses)
    return compiler.process((func.julianday(end) - func.julianday(start)) * 24.0, **kw)
