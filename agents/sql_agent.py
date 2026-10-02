"""Generate a validated, read-only SQL Server query plan from schema context."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from core.config import ROOT, load_settings
from core.ollama_client import ModelResponse, OllamaClient


class QueryPlan(BaseModel):
    """The only accepted output contract for the SQL generator."""

    model_config = ConfigDict(extra="forbid", strict=True)

    sql: str | None = None
    params: list[str | int | float | bool | None] = Field(default_factory=list)
    clarification: str | None = None

    @field_validator("sql", "clarification")
    @classmethod
    def non_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("params")
    @classmethod
    def finite_numbers(cls, values: list[Any]) -> list[Any]:
        if any(isinstance(value, float) and not math.isfinite(value) for value in values):
            raise ValueError("numeric params must be finite")
        return values

    @model_validator(mode="after")
    def validate_plan(self) -> "QueryPlan":
        if (self.sql is None) == (self.clarification is None):
            raise ValueError("provide exactly one of sql or clarification")
        if self.clarification is not None:
            if self.params:
                raise ValueError("a clarification cannot have params")
            return self

        sql = self.sql.strip().rstrip(";").strip()  # type: ignore[union-attr]
        # This is a safety boundary, not a full SQL parser. SQL is never executed here.
        if not re.match(r"^(?:WITH\b[\s\S]+?\bSELECT\b|SELECT\b)", sql, re.IGNORECASE):
            raise ValueError("sql must be one SELECT statement")
        scrubbed = _without_literals_and_comments(sql)
        forbidden = re.compile(
            r"\b(?:INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|EXEC(?:UTE)?|"
            r"GRANT|REVOKE|DENY|USE|SET|SELECT\s+INTO)\b",
            re.IGNORECASE,
        )
        if forbidden.search(scrubbed) or ";" in scrubbed:
            raise ValueError("sql must be a single read-only query")
        if re.search(r"\bLIMIT\b", scrubbed, re.IGNORECASE):
            raise ValueError("LIMIT is not valid T-SQL; use TOP or OFFSET/FETCH")
        if scrubbed.count("?") != len(self.params):
            raise ValueError("the number of placeholders must equal the number of params")
        self.sql = sql
        return self


def _without_literals_and_comments(sql: str) -> str:
    """Remove regions whose contents must not affect statement validation."""
    value = re.sub(r"/\*[\s\S]*?\*/|--[^\r\n]*", " ", sql)
    value = re.sub(r"N?'(?:''|[^'])*'", "''", value, flags=re.IGNORECASE)
    value = re.sub(r'"(?:""|[^"])*"', '""', value)
    value = re.sub(r"\[(?:\]\]|[^]])*]", "[]", value)
    return value


class SQLAgent:
    """Ask Ollama for one QueryPlan, optionally repairing malformed JSON once."""

    def __init__(self, client=None, config_path=None):
        self.client = client
        path = Path(config_path) if config_path else ROOT / "config" / "sql_agent.yml"
        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"No se pudo cargar la configuración SQL: {path}") from exc
        if not isinstance(config, dict):
            raise ValueError("sql_agent.yml debe contener un mapa")
        allowed = {"system_prompt", "repair_prompt", "max_format_repairs"}
        if set(config) - allowed:
            raise ValueError("Configuración SQL contiene claves desconocidas")
        self.system_prompt = config.get("system_prompt")
        self.repair_prompt = config.get("repair_prompt")
        self.max_repairs = config.get("max_format_repairs", 1)
        if not isinstance(self.system_prompt, str) or not self.system_prompt.strip():
            raise ValueError("system_prompt debe ser texto no vacío")
        if not isinstance(self.repair_prompt, str) or not self.repair_prompt.strip():
            raise ValueError("repair_prompt debe ser texto no vacío")
        if type(self.max_repairs) is not int or self.max_repairs not in (0, 1):
            raise ValueError("max_format_repairs debe ser 0 o 1")

    def generate(self, question: str, context: str, history: list | None = None) -> QueryPlan:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question debe ser texto no vacío")
        if not isinstance(context, str):
            raise ValueError("context debe ser JSON compacto")
        try:
            parsed_context = _strict_json_loads(context)
        except json.JSONDecodeError as exc:
            raise ValueError("context debe ser JSON válido") from exc
        if not isinstance(parsed_context, dict):
            raise ValueError("context debe ser un objeto JSON")
        history = [] if history is None else history
        if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
            raise ValueError("history debe ser una lista de objetos")

        request = json.dumps(
            {"question": question, "context": parsed_context, "history": history},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": request},
        ]
        if self.client is not None:
            return self._generate_with(self.client, messages)
        settings = load_settings()
        with OllamaClient(settings.ollama) as client:
            return self._generate_with(client, messages)

    def _generate_with(self, client, messages: list[dict[str, str]]) -> QueryPlan:
        response = client.chat(messages)
        try:
            return self._parse_response(response)
        except (ValueError, ValidationError) as first_error:
            if not self.max_repairs or _is_truncated(response):
                raise ValueError(f"Respuesta SQL inválida: {first_error}") from first_error
            repair_messages = messages + [
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": self.repair_prompt},
            ]
            repaired = client.chat(repair_messages)
            try:
                return self._parse_response(repaired)
            except (ValueError, ValidationError) as exc:
                raise ValueError(f"Respuesta SQL inválida tras una reparación: {exc}") from exc

    @staticmethod
    def _parse_response(response: ModelResponse) -> QueryPlan:
        if _is_truncated(response):
            raise ValueError("la respuesta fue truncada")
        text = response.content.strip()
        match = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if match:
            text = match.group(1).strip()
        try:
            value = _strict_json_loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("el contenido no es un único objeto JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("la raíz debe ser un objeto JSON")
        return QueryPlan.model_validate(value)


def _is_truncated(response: ModelResponse) -> bool:
    return response.done_reason.lower() in {"length", "max_tokens"}


def _strict_json_loads(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"clave JSON duplicada: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"constante JSON no válida: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
