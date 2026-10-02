"""SQL Server access for developer-authored queries and database discovery.

This module does not execute model-generated SQL. Before exposing it as an
agent tool, enforce database permissions and a separate SQL authorization layer.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
import base64
import json
import logging
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator, Sequence
from uuid import UUID

import pyodbc
import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "database.yml"


class DatabaseError(RuntimeError):
    """Database operation failed; the underlying exception is available as cause."""


class DatabaseSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    driver: str = Field(default="ODBC Driver 17 for SQL Server", min_length=1)
    server: str = Field(min_length=1)
    database: str = Field(min_length=1)
    trusted_connection: bool = True
    encrypt: bool = False
    trust_server_certificate: bool = False
    connection_timeout: int = Field(default=10, ge=1, le=300)
    query_timeout: int = Field(default=30, ge=1, le=3600)
    max_rows: int = Field(default=1000, ge=1, le=100000)

    def connection_string(self) -> str:
        if not self.trusted_connection:
            raise ValueError("Esta versión utiliza autenticación de Windows exclusivamente.")
        # ODBC values use braces; embedded closing braces must be escaped.
        def quote(value: str) -> str:
            if "\x00" in value:
                raise ValueError("ODBC values cannot contain NUL")
            return "{" + value.replace("}", "}}") + "}"
        return (
            f"DRIVER={quote(self.driver)};SERVER={quote(self.server)};"
            f"DATABASE={quote(self.database)};Trusted_Connection=yes;"
            f"Encrypt={'yes' if self.encrypt else 'no'};"
            f"TrustServerCertificate={'yes' if self.trust_server_certificate else 'no'};"
        )


def load_database_settings(path: str | Path | None = None) -> DatabaseSettings:
    config_path = Path(path) if path is not None else DEFAULT_CONFIG
    try:
        with config_path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML inválido: {config_path}") from exc
    if not isinstance(data, dict) or set(data) != {"database"}:
        raise ValueError("El YAML debe contener únicamente la sección database")
    return DatabaseSettings.model_validate(data["database"])


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)  # Preserve decimal precision.
    if isinstance(value, bytes):
        return {"encoding": "base64", "value": base64.b64encode(value).decode("ascii")}
    raise TypeError(f"Unsupported JSON type: {type(value).__name__}")


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool
    elapsed_seconds: float

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_records(self) -> list[dict[str, Any]]:
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("Columnas duplicadas: usa alias SQL antes de convertir a diccionarios.")
        return [dict(zip(self.columns, row)) for row in self.rows]

    def to_json(self, indent: int | None = 2) -> str:
        # Column names and row arrays preserve duplicate column labels.
        return json.dumps({
            "columns": self.columns, "rows": self.rows,
            "row_count": self.row_count, "truncated": self.truncated,
            "elapsed_seconds": self.elapsed_seconds,
        }, ensure_ascii=False, default=_json_value, indent=indent)


class DatabaseClient:
    """Short-lived connections per operation; driver pooling may reuse them.

    No shared cursors/connections between agent threads. No automatic retries.
    Query text must come from trusted application code, not directly from an LLM.
    The readonly ODBC flag is a hint, not a SQL Server authorization boundary.
    """
    def __init__(self, settings: DatabaseSettings):
        self.settings = settings
        self.settings.connection_string()  # Validate auth before first call.

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> "DatabaseClient":
        return cls(load_database_settings(path))

    @staticmethod
    def available_drivers() -> list[str]:
        return list(pyodbc.drivers())

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        config = self.settings
        if config.driver not in self.available_drivers():
            raise DatabaseError(f"No está instalado el driver '{config.driver}'. Revisa pyodbc.drivers().")
        connection = None
        try:
            connection = pyodbc.connect(
                config.connection_string(), timeout=config.connection_timeout,
                autocommit=False, readonly=True,
            )
            connection.timeout = config.query_timeout
            yield connection
        except pyodbc.Error as exc:
            state = str(exc.args[0]) if exc.args else "unknown"
            raise DatabaseError(f"Error SQL Server (SQLSTATE {state}); revisa instancia, acceso y consulta.") from exc
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                except pyodbc.Error:
                    logger.warning("No se pudo revertir la transacción al cerrar")
                finally:
                    connection.close()

    def query(self, sql: str, params: Sequence[Any] = (), *, max_rows: int | None = None) -> QueryResult:
        """Fetch a bounded result from a trusted SELECT; placeholders use '?'.

        This is not a SQL sandbox. Row limits bound fetched rows, not server work
        or bytes in one value. SQL Server permissions must enforce read-only use.
        """
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("La consulta no puede estar vacía")
        if isinstance(params, (str, bytes)):
            raise ValueError("params debe ser una secuencia de valores, por ejemplo (country,)")
        limit = self.settings.max_rows if max_rows is None else max_rows
        if type(limit) is not int or not 1 <= limit <= self.settings.max_rows:
            raise ValueError(f"max_rows debe estar entre 1 y {self.settings.max_rows}")
        started = perf_counter()
        with self._connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(sql, tuple(params))
                if cursor.description is None:
                    raise DatabaseError("La consulta no devolvió una tabla de resultados.")
                columns = [item[0] for item in cursor.description]
                fetched = cursor.fetchmany(limit + 1)
                rows = [tuple(row) for row in fetched[:limit]]
                truncated = len(fetched) > limit
                # Reject additional results; this is NOT a prevention of side effects.
                if cursor.nextset():
                    raise DatabaseError("Se esperaba un único conjunto de resultados.")
            finally:
                cursor.close()
        elapsed = perf_counter() - started
        logger.info("db_query rows=%d truncated=%s elapsed=%.3fs", len(rows), truncated, elapsed)
        return QueryResult(columns, rows, truncated, elapsed)

    def test_connection(self) -> QueryResult:
        return self.query("SELECT DB_NAME() AS database_name, SUSER_SNAME() AS login_name, @@SERVERNAME AS server_name", max_rows=1)

    def list_tables(self) -> QueryResult:
        return self.query("""
            SELECT TABLE_SCHEMA AS schema_name, TABLE_NAME AS table_name,
                   TABLE_TYPE AS table_type
            FROM INFORMATION_SCHEMA.TABLES
            ORDER BY TABLE_SCHEMA, TABLE_NAME
        """)

    def get_columns(self, table: str, schema: str = "dbo") -> QueryResult:
        return self.query("""
            SELECT ORDINAL_POSITION AS position, COLUMN_NAME AS column_name,
                   DATA_TYPE AS data_type, CHARACTER_MAXIMUM_LENGTH AS max_length,
                   NUMERIC_PRECISION AS numeric_precision, NUMERIC_SCALE AS numeric_scale,
                   IS_NULLABLE AS is_nullable, COLUMN_DEFAULT AS default_value
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
        """, (schema, table))

    def get_relationships(self) -> QueryResult:
        return self.query("""
            SELECT fk.name AS constraint_name, fkc.constraint_column_id AS column_position,
                   ss.name AS source_schema, st.name AS source_table, sc.name AS source_column,
                   ts.name AS target_schema, tt.name AS target_table, tc.name AS target_column
            FROM sys.foreign_key_columns AS fkc
            JOIN sys.foreign_keys AS fk ON fk.object_id = fkc.constraint_object_id
            JOIN sys.tables AS st ON st.object_id = fkc.parent_object_id
            JOIN sys.schemas AS ss ON ss.schema_id = st.schema_id
            JOIN sys.columns AS sc ON sc.object_id = st.object_id AND sc.column_id = fkc.parent_column_id
            JOIN sys.tables AS tt ON tt.object_id = fkc.referenced_object_id
            JOIN sys.schemas AS ts ON ts.schema_id = tt.schema_id
            JOIN sys.columns AS tc ON tc.object_id = tt.object_id AND tc.column_id = fkc.referenced_column_id
            ORDER BY ss.name, st.name, fk.name, fkc.constraint_column_id
        """)

    def get_primary_keys(self) -> QueryResult:
        return self.query("""
            SELECT s.name AS schema_name, t.name AS table_name,
                   c.name AS column_name, ic.key_ordinal AS key_position
            FROM sys.indexes AS i
            JOIN sys.tables AS t ON t.object_id = i.object_id
            JOIN sys.schemas AS s ON s.schema_id = t.schema_id
            JOIN sys.index_columns AS ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
            JOIN sys.columns AS c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
            WHERE i.is_primary_key = 1
            ORDER BY s.name, t.name, ic.key_ordinal
        """)

    def preview_table(self, table: str, schema: str = "dbo", limit: int = 10) -> QueryResult:
        if type(limit) is not int or not 1 <= limit <= self.settings.max_rows:
            raise ValueError("Límite de muestra inválido")
        if self.get_columns(table, schema).row_count == 0:
            raise ValueError("Tabla/vista inexistente o sin permisos de catálogo")
        def quote_identifier(name: str) -> str:
            return "[" + name.replace("]", "]]") + "]"
        # Validated integer and escaped catalog-resolved identifiers, never raw user SQL.
        sql = f"SELECT TOP ({limit + 1}) * FROM {quote_identifier(schema)}.{quote_identifier(table)}"
        return self.query(sql, max_rows=limit)
