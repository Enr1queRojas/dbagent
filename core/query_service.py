"""Execution boundary for SQL that has already passed validation."""
from dataclasses import dataclass
from typing import Any, Sequence

from core.database_client import DatabaseClient, QueryResult


@dataclass(frozen=True)
class QueryData:
    """Lossless, ordered representation of a database result."""

    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool
    elapsed_seconds: float

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "elapsed_seconds": self.elapsed_seconds,
        }


class QueryService:
    """Execute a validator's decision; it does not validate or devise SQL.

    This class is an internal trust boundary.  Callers must not treat its simple
    ``allowed`` flag as an authentication or public authorization mechanism.
    """

    def __init__(self, db: DatabaseClient | None = None):
        self.db = db if db is not None else DatabaseClient.from_yaml()

    def execute(self, validation: dict[str, Any]) -> QueryData:
        if not isinstance(validation, dict):
            raise ValueError("La validación debe ser un diccionario")
        if validation.get("allowed") is not True:
            raise PermissionError("La consulta no fue autorizada por el validador")
        sql = validation.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("La validación no contiene SQL válido")
        params = validation.get("params", ())
        if isinstance(params, (str, bytes, bytearray)) or not isinstance(params, Sequence):
            raise ValueError("params debe ser una secuencia")

        # Deliberately one call: in particular, timeouts are never retried.
        result: QueryResult = self.db.query(sql, params)
        return QueryData(
            columns=list(result.columns),
            rows=list(result.rows),
            truncated=result.truncated,
            elapsed_seconds=result.elapsed_seconds,
        )
