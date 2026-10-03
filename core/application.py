"""Application orchestration for the natural-language database workflow.

This module deliberately depends only on protocols expressed by duck typing.  The
concrete database/agent modules are imported by :func:`create_application`, which
keeps the orchestration independently testable while those modules evolve.
"""
from dataclasses import asdict, dataclass
import json
import logging
from typing import Any


LOGGER = logging.getLogger(__name__)
MAX_HISTORY_TURNS = 6
MAX_HISTORY_CHARS = 2_000


@dataclass
class Response:
    """Stable public response contract returned for every outcome."""

    status: str
    question: str
    answer: str | None = None
    clarification: str | None = None
    plan: Any = None
    data: Any = None
    warnings: list[str] | None = None
    error: str | None = None
    executed: bool = False

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _serializable(value: Any) -> Any:
    """Preserve typed internal contracts while exposing JSON/UI-friendly values."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value


def _warnings(value: Any) -> list[str]:
    warnings = _get(value, "warnings", default=[]) or []
    return [str(item) for item in warnings]


def _bounded_history(history: Any) -> tuple[list[Any], str, bool]:
    if history is None:
        return [], "", False
    if not isinstance(history, list):
        raise ValueError("El historial debe ser una lista.")

    selected = history[-MAX_HISTORY_TURNS:]
    lines = []
    for turn in selected:
        if isinstance(turn, str):
            text = turn
        elif isinstance(turn, dict):
            role = str(turn.get("role", "turno"))
            content = turn.get("content", turn.get("text", ""))
            if not isinstance(content, str):
                raise ValueError("Cada turno del historial debe contener texto.")
            text = f"{role}: {content}"
        else:
            raise ValueError("Cada turno del historial debe ser texto o un mapa.")
        if text.strip():
            lines.append(text.strip())

    summary = "\n".join(lines)
    truncated = len(history) > len(selected) or len(summary) > MAX_HISTORY_CHARS
    if len(summary) > MAX_HISTORY_CHARS:
        summary = summary[-MAX_HISTORY_CHARS:]
    return selected, summary, truncated


def _usable_context(result: Any) -> bool:
    context = _get(result, "context", default=result if isinstance(result, str) else "")
    selected = _get(result, "selected_objects")
    if selected is not None:
        return bool(selected) and bool(str(context).strip())
    if not isinstance(context, str) or not context.strip():
        return False
    try:
        payload = json.loads(context)
    except (TypeError, json.JSONDecodeError):
        return True
    return not isinstance(payload, dict) or bool(payload.get("objects", payload))


class Application:
    def __init__(self, retriever: Any, sql_agent: Any, validator: Any,
                 query_service: Any, answer_agent: Any):
        self.retriever = retriever
        self.sql_agent = sql_agent
        self.validator = validator
        self.query_service = query_service
        self.answer_agent = answer_agent

    def handle(self, request: dict) -> Response:
        """Validate and orchestrate one request without executing by default."""
        if not isinstance(request, dict):
            return Response("error", "", error="La solicitud debe ser un mapa.")
        question = request.get("question", "")
        if not isinstance(question, str) or not question.strip():
            return Response("error", "", error="Escribe una pregunta no vacía.")
        question = question.strip()
        execute = request.get("execute", False)
        if type(execute) is not bool:
            return Response("error", question, error="El campo execute debe ser booleano.")

        try:
            history, summary, history_truncated = _bounded_history(request.get("history"))
        except ValueError as exc:
            return Response("error", question, error=str(exc))

        warnings: list[str] = []
        try:
            if history_truncated:
                warnings.append("El historial se acotó a los turnos recientes pertinentes.")
            retrieval_question = question
            if summary:
                retrieval_question += "\n\nContexto de conversación reciente:\n" + summary
            retrieval = self.retriever.retrieve(retrieval_question)
            warnings.extend(_warnings(retrieval))
            if not _usable_context(retrieval):
                return Response("clarification", question,
                    clarification="No encontré contexto de esquema suficiente. Aclara las entidades o métricas.",
                    warnings=warnings)

            context = _get(retrieval, "context", default=retrieval)
            plan = request.get("prepared_plan") if execute else None
            if plan is None:
                plan = self.sql_agent.generate(question, context, history=history or None)
            elif not isinstance(plan, dict):
                return Response("error", question, error="El plan preparado debe ser un mapa.")
            warnings.extend(_warnings(plan))
            needs_clarification = bool(_get(plan, "requires_clarification", "needs_clarification", default=False))
            clarification = _get(plan, "clarification", "clarification_question")
            if needs_clarification or clarification:
                return Response("clarification", question, clarification=str(clarification or
                    "Necesito más información para preparar la consulta."), plan=plan, warnings=warnings)

            validation = self.validator.validate(plan, context)
            warnings.extend(_warnings(validation))
            approved = bool(_get(validation, "allowed", "approved", "valid", "is_valid", default=False))
            if not approved:
                reason = _get(validation, "public_reason", "reason", "message", "explanation")
                return Response("blocked", question,
                    answer=str(reason or "La consulta propuesta fue rechazada por la validación."),
                    plan=_serializable(plan), warnings=warnings)
            if not execute:
                return Response("planned", question, plan=_serializable(plan), warnings=warnings)

            data = self.query_service.execute(validation)
            warnings.extend(_warnings(data))
            if bool(_get(data, "truncated", "is_truncated", default=False)):
                warnings.append("Los resultados fueron truncados por el límite configurado.")
            plan_data, query_data = _serializable(plan), _serializable(data)
            answer = self.answer_agent.generate(question, plan_data, query_data)
            return Response("completed", question, answer=str(answer), plan=plan_data,
                            data=query_data, warnings=warnings, executed=True)
        except Exception as exc:  # never log exception text: it may contain SQL or credentials
            LOGGER.error("Application workflow failed; exception_type=%s", type(exc).__name__)
            return Response("error", question, warnings=warnings,
                            error="No se pudo completar la solicitud por un error técnico.")


def create_application() -> Application:
    """Assemble real implementations, importing optional integration modules lazily."""
    from agents.answer_agent import AnswerAgent
    from agents.sql_agent import SQLAgent
    from core.query_service import QueryService
    from core.schema_retriever import SchemaRetriever
    from core.sql_validator import SQLValidator

    return Application(SchemaRetriever(), SQLAgent(), SQLValidator(),
                       QueryService(), AnswerAgent())
