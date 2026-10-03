from dataclasses import dataclass, field
import unittest

from core.application import Application, MAX_HISTORY_CHARS, MAX_HISTORY_TURNS


@dataclass
class Result:
    context: str = "schema"
    selected_objects: list = field(default_factory=lambda: ["table"])
    warnings: list = field(default_factory=list)
    requires_clarification: bool = False
    clarification: str | None = None
    approved: bool = True
    truncated: bool = False


class Fake:
    def __init__(self, calls, name, result=None, error=None):
        self.calls, self.name, self.result, self.error = calls, name, result, error

    def _call(self, *args, **kwargs):
        self.calls.append((self.name, args, kwargs))
        if self.error:
            raise self.error
        return self.result

    retrieve = _call
    generate = _call
    validate = _call
    execute = _call


class ApplicationTests(unittest.TestCase):
    def app(self, *, retrieval=None, plan=None, validation=None, data=None,
            generation_error=None):
        self.calls = []
        return Application(
            Fake(self.calls, "retrieve", retrieval or Result()),
            Fake(self.calls, "sql", plan or Result(), generation_error),
            Fake(self.calls, "validate", validation or Result()),
            Fake(self.calls, "execute", data or Result()),
            Fake(self.calls, "answer", "respuesta"),
        )

    def test_plan_orders_calls_and_never_executes(self):
        response = self.app().handle({"question": "ventas"})
        self.assertEqual(response.status, "planned")
        self.assertEqual([call[0] for call in self.calls], ["retrieve", "sql", "validate"])
        self.assertFalse(response.executed)

    def test_execute_orders_complete_flow(self):
        response = self.app().handle({"question": "ventas", "execute": True})
        self.assertEqual(response.status, "completed")
        self.assertEqual([call[0] for call in self.calls],
                         ["retrieve", "sql", "validate", "execute", "answer"])

    def test_validation_blocks_before_execution(self):
        response = self.app(validation=Result(approved=False)).handle(
            {"question": "borra todo", "execute": True})
        self.assertEqual(response.status, "blocked")
        self.assertNotIn("execute", [call[0] for call in self.calls])

    def test_empty_context_and_plan_clarification(self):
        response = self.app(retrieval=Result(context="{}", selected_objects=[])).handle(
            {"question": "algo", "execute": True})
        self.assertEqual(response.status, "clarification")
        self.assertEqual([call[0] for call in self.calls], ["retrieve"])

        response = self.app(plan=Result(requires_clarification=True,
                                        clarification="¿Qué periodo?")).handle({"question": "ventas"})
        self.assertEqual(response.clarification, "¿Qué periodo?")
        self.assertEqual([call[0] for call in self.calls], ["retrieve", "sql"])

    def test_generation_error_is_sanitized(self):
        secret = "password=super-secret"
        response = self.app(generation_error=RuntimeError(secret)).handle({"question": "ventas"})
        self.assertEqual(response.status, "error")
        self.assertNotIn(secret, response.error)
        self.assertIsNone(response.plan)

    def test_warnings_and_truncated_results_are_propagated(self):
        response = self.app(
            retrieval=Result(warnings=["retrieval"]),
            validation=Result(warnings=["validation"]),
            data=Result(warnings=["data"], truncated=True),
        ).handle({"question": "ventas", "execute": True})
        self.assertEqual(response.warnings[:3], ["retrieval", "validation", "data"])
        self.assertTrue(any("truncados" in warning for warning in response.warnings))

    def test_history_is_bounded_for_retrieval_and_agent(self):
        history = [{"role": "user", "content": "x" * 600} for _ in range(10)]
        self.app().handle({"question": "ventas", "history": history})
        retrieval_text = self.calls[0][1][0]
        passed_history = self.calls[1][2]["history"]
        self.assertLessEqual(len(retrieval_text) - len("ventas\n\nContexto de conversación reciente:\n"),
                             MAX_HISTORY_CHARS)
        self.assertEqual(len(passed_history), MAX_HISTORY_TURNS)

    def test_response_always_has_full_contract(self):
        response = self.app().handle({"question": ""})
        self.assertEqual(set(response.to_dict()), {"status", "question", "answer",
            "clarification", "plan", "data", "warnings", "error", "executed"})


if __name__ == "__main__":
    unittest.main()
