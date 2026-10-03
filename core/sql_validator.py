"""Fail-closed, offline authorization for model-generated T-SQL.

This module only decides whether a query conforms to a local allow-list.  It
never executes SQL and is deliberately independent from ``DatabaseClient``.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import yaml
from sqlglot import exp, parse
from sqlglot.errors import ParseError, OptimizeError
from sqlglot.optimizer.qualify import qualify


DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "config" / "sql_policy.yml"


@dataclass(frozen=True)
class ValidationResult:
    """Result safe for presenting to an agent (no rewritten SQL is returned)."""

    allowed: bool
    errors: tuple[str, ...]
    sql: str | None = None
    params: tuple[Any, ...] = ()

    @property
    def explanation(self) -> str:
        return "Consulta autorizada." if self.allowed else " ".join(self.errors)


def _identifier_parts(value: str) -> tuple[str, str]:
    """Normalize a policy/context two-part name, including bracketed names."""
    try:
        nodes = parse(f"SELECT 1 FROM {value}", read="tsql")
    except ParseError as exc:
        raise ValueError(f"Nombre de objeto inválido en política: {value!r}") from exc
    tables = list(nodes[0].find_all(exp.Table)) if len(nodes) == 1 else []
    if len(tables) != 1:
        raise ValueError(f"Nombre de objeto inválido en política: {value!r}")
    table = tables[0]
    if table.catalog or not table.db or not table.name:
        raise ValueError(f"El objeto debe tener formato esquema.objeto: {value!r}")
    return table.db.casefold(), table.name.casefold()


class SQLValidator:
    """Validate a ``{'sql': str, 'params': sequence}`` plan without executing it."""

    def __init__(self, policy_path: str | Path | None = None):
        path = Path(policy_path) if policy_path is not None else DEFAULT_POLICY
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"No se pudo cargar la política SQL: {path}") from exc
        if not isinstance(raw, dict):
            raise ValueError("sql_policy.yml debe contener un mapa")
        required = {"enabled", "allowed_objects", "allow_views", "limits", "allowed_functions", "allowed_table_functions"}
        if set(raw) != required:
            raise ValueError(f"Claves de política inválidas; se requieren: {', '.join(sorted(required))}")
        if type(raw["enabled"]) is not bool or type(raw["allow_views"]) is not bool:
            raise ValueError("enabled y allow_views deben ser booleanos")
        if not isinstance(raw["allowed_objects"], list) or any(not isinstance(x, str) for x in raw["allowed_objects"]):
            raise ValueError("allowed_objects debe ser una lista de nombres")
        limits = raw["limits"]
        expected_limits = {"max_sql_chars", "max_ast_nodes", "max_joins", "max_subqueries", "max_ctes"}
        if not isinstance(limits, dict) or set(limits) != expected_limits or any(type(v) is not int or v < 0 for v in limits.values()):
            raise ValueError("limits contiene claves o valores inválidos")
        for key in ("allowed_functions", "allowed_table_functions"):
            if not isinstance(raw[key], list) or any(not isinstance(x, str) or not x for x in raw[key]):
                raise ValueError(f"{key} debe ser una lista de nombres")
        self.policy = raw
        self.allowed_objects = {_identifier_parts(x) for x in raw["allowed_objects"]}
        self.allowed_functions = {x.casefold() for x in raw["allowed_functions"]}
        self.allowed_table_functions = {x.casefold() for x in raw["allowed_table_functions"]}

    def _deny(self, *errors: str) -> ValidationResult:
        return ValidationResult(False, tuple(errors))

    def validate(self, plan: dict, context: str) -> ValidationResult:
        if not self.policy["enabled"]:
            return self._deny("La validación SQL está deshabilitada; habilítala y configura allowed_objects explícitamente.")
        if not self.allowed_objects:
            return self._deny("La política no autoriza ningún objeto (allowed_objects está vacío).")
        if hasattr(plan, "model_dump"):
            plan = plan.model_dump(exclude_none=True)
        if not isinstance(plan, dict) or set(plan) != {"sql", "params"}:
            return self._deny("El plan debe contener únicamente sql y params.")
        sql, params = plan["sql"], plan["params"]
        if not isinstance(sql, str) or not sql.strip():
            return self._deny("sql debe ser texto no vacío.")
        if isinstance(params, (str, bytes, dict)) or not isinstance(params, (list, tuple)):
            return self._deny("params debe ser una lista o tupla.")
        if len(sql) > self.policy["limits"]["max_sql_chars"]:
            return self._deny("La consulta excede max_sql_chars.")
        try:
            context_data = json.loads(context)
            objects = context_data["objects"]
            if not isinstance(objects, dict):
                raise TypeError
            metadata = self._metadata(objects)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return self._deny("El contexto de esquema JSON es inválido.")
        try:
            statements = parse(sql, read="tsql")
        except ParseError as exc:
            return self._deny(f"T-SQL no analizable: {exc}." )
        if len(statements) != 1:
            return self._deny("Se requiere exactamente una sentencia.")
        root = statements[0]
        if not isinstance(root, exp.Select):
            return self._deny("Solo se autoriza SELECT (posiblemente precedido por WITH).")
        errors: list[str] = []
        limits = self.policy["limits"]
        nodes = list(root.walk())
        if len(nodes) > limits["max_ast_nodes"]:
            errors.append("La consulta excede max_ast_nodes.")
        if len(list(root.find_all(exp.Join))) > limits["max_joins"]:
            errors.append("La consulta excede max_joins.")
        if len(list(root.find_all(exp.Subquery))) > limits["max_subqueries"]:
            errors.append("La consulta excede max_subqueries.")
        ctes = list(root.find_all(exp.CTE))
        if len(ctes) > limits["max_ctes"]:
            errors.append("La consulta excede max_ctes.")
        with_node = root.args.get("with_")
        if with_node is not None and with_node.args.get("recursive"):
            errors.append("Los CTE recursivos no están autorizados.")
        if root.args.get("into") is not None or any(isinstance(n, exp.Into) for n in nodes):
            errors.append("SELECT INTO no está autorizado.")
        if any(isinstance(n, (exp.DDL, exp.DML, exp.Command, exp.SetOperation)) for n in nodes):
            errors.append("La consulta contiene una operación o construcción no autorizada.")
        # T-SQL variable assignment is represented as PropertyEQ/Parameter nodes.
        if any(isinstance(n, (exp.Parameter, exp.PropertyEQ)) for n in nodes):
            errors.append("La asignación o uso de variables SQL no está autorizado.")
        placeholders = sum(isinstance(n, exp.Placeholder) for n in nodes)
        if placeholders != len(params):
            errors.append(f"Hay {placeholders} marcadores ? y {len(params)} parámetros.")
        for star in root.find_all(exp.Star):
            if not isinstance(star.parent, exp.Count):
                errors.append("SELECT * no está autorizado; enumera las columnas.")
                break
        self._validate_tables(root, metadata, errors)
        self._validate_functions(root, errors)
        # Qualification resolves aliases and nested/CTE scopes, and rejects both
        # unknown and ambiguous columns against the exact context schema.
        if not errors:
            try:
                qualify(root.copy(), dialect="tsql", schema=self._qualify_schema(metadata),
                        expand_stars=False, infer_schema=False, validate_qualify_columns=True,
                        quote_identifiers=False, identify=False)
            except (OptimizeError, ValueError, TypeError, KeyError) as exc:
                errors.append(f"No se pudieron resolver columnas y ámbitos: {exc}.")
        return (self._deny(*dict.fromkeys(errors)) if errors else
                ValidationResult(True, (), sql, tuple(params)))

    def _metadata(self, objects: dict[str, Any]) -> dict[tuple[str, str], tuple[str, set[str]]]:
        result = {}
        for name, item in objects.items():
            if not isinstance(name, str) or not isinstance(item, dict) or not isinstance(item.get("columns"), list):
                raise ValueError
            key = _identifier_parts(name)
            columns = item["columns"]
            names = {c["column_name"] for c in columns if isinstance(c, dict) and isinstance(c.get("column_name"), str)}
            if len(names) != len(columns):
                raise ValueError
            result[key] = (str(item.get("type", "")), names)
        return result

    def _validate_tables(self, root: exp.Expression, metadata, errors: list[str]) -> None:
        cte_names = {cte.alias_or_name.casefold() for cte in root.find_all(exp.CTE)}
        for table in root.find_all(exp.Table):
            if isinstance(table.this, exp.Func):
                name = self._function_name(table.this)
                if name not in self.allowed_table_functions:
                    errors.append(f"Función de tabla no autorizada: {name}.")
                continue
            if table.name.casefold() in cte_names and not table.db and not table.catalog:
                continue
            if table.catalog or not table.db:
                errors.append(f"Referencia externa o sin esquema no autorizada: {table.sql(dialect='tsql')}.")
                continue
            key = (table.db.casefold(), table.name.casefold())
            if table.name.startswith("#") or table.db.casefold() in {"sys", "information_schema"}:
                errors.append(f"Objeto temporal/de sistema no autorizado: {table.sql(dialect='tsql')}.")
            elif key not in self.allowed_objects:
                errors.append(f"Objeto fuera de allowed_objects: {table.sql(dialect='tsql')}.")
            elif key not in metadata:
                errors.append(f"Objeto ausente del contexto: {table.sql(dialect='tsql')}.")
            elif metadata[key][0].casefold() != "base table" and not self.policy["allow_views"]:
                errors.append(f"Las vistas no están autorizadas: {table.sql(dialect='tsql')}.")

    def _validate_functions(self, root: exp.Expression, errors: list[str]) -> None:
        for function in root.find_all(exp.Func):
            # Table-valued functions are handled as sources above.
            if isinstance(function.parent, exp.Table):
                continue
            name = self._function_name(function)
            if name not in self.allowed_functions:
                errors.append(f"Función no autorizada: {name}.")

    @staticmethod
    def _function_name(function: exp.Func) -> str:
        # User-defined/unknown calls are Anonymous and sql_name() would only
        # return "ANONYMOUS", losing the security-relevant function name.
        if isinstance(function, exp.Anonymous):
            return function.name.casefold()
        return function.sql_name().casefold()

    @staticmethod
    def _qualify_schema(metadata):
        schema: dict[str, dict[str, dict[str, str]]] = {}
        for (owner, table), (_, columns) in metadata.items():
            schema.setdefault(owner, {})[table] = {column: "UNKNOWN" for column in columns}
        return schema
