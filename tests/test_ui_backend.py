from dataclasses import dataclass
import sys
from types import ModuleType

import pytest

from ui.backend import (MAX_HISTORY, append_message, ask, artifact_path,
                        history_for_request, init_session, normalize_response,
                        reset_conversation, load_report_tools)


class FakeBackend:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def handle(self, request):
        self.requests.append(request)
        return self.response


@pytest.mark.parametrize("status", ["planned", "clarification", "completed", "blocked", "error"])
def test_all_response_states(status):
    assert normalize_response({"status": status})["status"] == status


def test_request_contract_and_history():
    fake = FakeBackend({"status": "planned", "sql": "SELECT 1"})
    history = [{"role": "user", "content": "antes"}, {"role": "assistant", "content": "respuesta"}]
    result = ask(fake, "pregunta", history, execute=False)
    assert result["status"] == "planned"
    assert fake.requests == [{"question": "pregunta", "history": history, "execute": False}]


def test_history_is_bounded_and_reset_isolated():
    messages = [{"role": "user", "content": str(i)} for i in range(MAX_HISTORY + 4)]
    assert len(history_for_request(messages)) == MAX_HISTORY
    state = {"messages": messages, "backend": object(), "artifact_dir": "/tmp/own"}
    backend = state["backend"]
    reset_conversation(state)
    assert state["messages"] == [] and state["backend"] is backend


def test_stored_messages_are_bounded():
    state = {}
    init_session(state)
    for number in range(MAX_HISTORY + 3):
        append_message(state, {"role": "user", "content": str(number)})
    assert len(state["messages"]) == MAX_HISTORY


def test_session_has_own_artifact_directory_and_sanitizes_name():
    one, two = {}, {}
    init_session(one); init_session(two)
    assert one["artifact_dir"] != two["artifact_dir"]
    assert artifact_path(one, "../../secret.csv").name == "secret.csv"


def test_errors_are_sanitized():
    class Broken:
        def handle(self, request):
            raise RuntimeError("password=secret")
    result = ask(Broken(), "x", [], execute=False)
    assert result["status"] == "error"
    assert "secret" not in result["message"]


def test_execution_only_occurs_on_explicit_second_call():
    fake = FakeBackend({"status": "completed"})
    ask(fake, "q", [], execute=False)
    assert len(fake.requests) == 1
    # A Streamlit rerun does not call this function; the confirmation action does.
    ask(fake, "q", [], execute=True)
    assert [request["execute"] for request in fake.requests] == [False, True]


def test_execution_reuses_the_reviewed_plan():
    fake = FakeBackend({"status": "completed"})
    plan = {"sql": "SELECT 1 AS value", "params": []}
    ask(fake, "q", [], execute=True, prepared_plan=plan)
    assert fake.requests[0]["prepared_plan"] is plan


@dataclass
class Response:
    status: str
    rows: list
    columns: list
    truncated: bool = True


def test_dataclass_results_and_optional_artifact_fields():
    result = normalize_response(Response("completed", [(1, "A")], ["id", "name"]))
    assert result["data"] == [{"id": 1, "name": "A"}]
    assert result["truncated"] is True


def test_optional_report_tools_can_be_present(monkeypatch):
    module = ModuleType("tools.report_tools")
    sentinel = object()
    module.ReportTools = lambda output_dir=None: sentinel
    monkeypatch.setitem(sys.modules, "tools.report_tools", module)
    assert load_report_tools() is sentinel


def test_optional_report_tools_can_be_absent(monkeypatch):
    real_import = __import__("importlib").import_module

    def missing(name):
        if name == "tools.report_tools":
            raise ModuleNotFoundError(name)
        return real_import(name)

    monkeypatch.setattr("ui.backend.importlib.import_module", missing)
    assert load_report_tools() is None
