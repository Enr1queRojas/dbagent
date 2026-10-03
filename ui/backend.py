"""Adaptadores sin estado global para la interfaz local de Streamlit."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
import importlib
import tempfile
from typing import Any, MutableMapping

MAX_HISTORY = 20
VALID_STATUSES = {"planned", "clarification", "completed", "blocked", "error"}


class IntegrationUnavailable(RuntimeError):
    """The agreed application entry point is not installed yet."""


def create_backend() -> Any:
    """Load the application lazily so importing the UI never requires core.application."""
    try:
        module = importlib.import_module("core.application")
        factory = getattr(module, "create_application")
    except (ImportError, AttributeError) as exc:
        raise IntegrationUnavailable(
            "La integración con el motor de consultas todavía no está disponible."
        ) from exc
    return factory()


def _mapping(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return dict(response)
    if is_dataclass(response):
        return asdict(response)
    if hasattr(response, "model_dump"):
        return dict(response.model_dump())
    names = ("status", "message", "answer", "plan", "sql", "data", "rows",
             "columns", "truncated", "error")
    return {name: getattr(response, name) for name in names if hasattr(response, name)}


def normalize_response(response: Any) -> dict[str, Any]:
    """Convert common Response representations into the UI's safe view model."""
    raw = _mapping(response)
    status = str(raw.get("status", "error")).lower()
    if status not in VALID_STATUSES:
        status = "error"
    message = raw.get("message") or raw.get("answer")
    if not isinstance(message, str) or not message.strip():
        message = {
            "planned": "La consulta está preparada y pendiente de confirmación.",
            "clarification": "Necesito un poco más de información.",
            "completed": "Consulta completada.",
            "blocked": "No se puede continuar con esta solicitud.",
            "error": "No se pudo completar la solicitud.",
        }[status]
    data = raw.get("data")
    if is_dataclass(data):
        data = asdict(data)
    elif hasattr(data, "model_dump"):
        data = data.model_dump()
    if data is None and raw.get("rows") is not None:
        rows, columns = raw.get("rows"), raw.get("columns")
        data = [dict(zip(columns, row)) for row in rows] if columns else rows
    if not isinstance(data, (list, tuple, dict)) and data is not None:
        data = None
    if isinstance(data, dict) and "columns" in data and "rows" in data:
        data = {"columns": list(data["columns"]), "rows": list(data["rows"]),
                "truncated": bool(data.get("truncated", False))}
    truncated = bool(data.get("truncated")) if isinstance(data, dict) else bool(raw.get("truncated"))
    plan = raw.get("plan")
    if hasattr(plan, "model_dump"):
        plan = plan.model_dump()
    sql = raw.get("sql") or (plan.get("sql") if isinstance(plan, dict) else None)
    return {
        "status": status, "message": message, "plan": plan,
        "sql": sql, "data": data, "truncated": truncated,
    }


def init_session(state: MutableMapping[str, Any]) -> None:
    state.setdefault("messages", [])
    state.setdefault("backend", None)
    state.setdefault("pending_execution", None)
    state.setdefault("executed_tokens", set())
    if "artifact_dir" not in state:
        state["artifact_dir"] = tempfile.mkdtemp(prefix="dbagent-ui-")


def reset_conversation(state: MutableMapping[str, Any]) -> None:
    """Clear conversation data while retaining this session's backend and temp area."""
    state["messages"] = []
    state["pending_execution"] = None
    state["executed_tokens"] = set()


def append_message(state: MutableMapping[str, Any], message: dict[str, Any]) -> None:
    """Append a message while bounding memory retained by this browser session."""
    state["messages"].append(message)
    state["messages"] = state["messages"][-MAX_HISTORY:]


def history_for_request(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    history = []
    for item in messages[-MAX_HISTORY:]:
        if item.get("role") in {"user", "assistant"}:
            history.append({"role": item["role"], "content": str(item.get("content", ""))})
    return history


def ask(backend: Any, question: str, history: list[dict[str, Any]], *, execute: bool,
        prepared_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call the agreed backend contract. Exceptions are deliberately sanitized."""
    try:
        request = {"question": question, "history": history_for_request(history), "execute": execute}
        if prepared_plan is not None:
            request["prepared_plan"] = prepared_plan
        return normalize_response(backend.handle(request))
    except Exception:
        return normalize_response({"status": "error", "message":
            "Ocurrió un problema al procesar la solicitud. Revisa la configuración e inténtalo de nuevo."})


def load_report_tools(output_dir: str | Path | None = None) -> Any | None:
    """Return optional reporting support without making it a UI prerequisite."""
    try:
        cls = getattr(importlib.import_module("tools.report_tools"), "ReportTools")
        return cls(output_dir=output_dir)
    except (ImportError, AttributeError):
        return None


def artifact_path(state: MutableMapping[str, Any], filename: str) -> Path:
    """Create an app-controlled path; browser-supplied paths are never accepted."""
    root = Path(state["artifact_dir"]).resolve()
    safe_name = Path(filename).name
    return root / safe_name
