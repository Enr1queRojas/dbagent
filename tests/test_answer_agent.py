import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from agents.answer_agent import AnswerAgent


class AnswerAgentTests(unittest.TestCase):
    def config(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
        f.write("system_prompt: faithful\nmax_rows: 1\nmax_chars: 500\n")
        f.close()
        self.addCleanup(Path(f.name).unlink)
        return f.name

    def test_prompt_is_bounded_and_values_are_exact(self):
        client = Mock(); client.chat.return_value.content = "respuesta"
        data = {"columns": ["x", "day"], "rows": [(Decimal("1.2300"), date(2024, 1, 2)), (9, None)], "truncated": True}
        answer = AnswerAgent(client, self.config()).generate("q", {"explanation": "p"}, data)
        self.assertTrue(answer.startswith("respuesta"))
        self.assertIn("muestra", answer)
        prompt = client.chat.call_args.args[0][1]["content"]
        self.assertIn('1.2300', prompt); self.assertNotIn('(9,', prompt)
        self.assertIn("muestra", prompt); self.assertIn("truncado", prompt)
        self.assertEqual(len(data["rows"]), 2)

    def test_zero_rows_and_failure(self):
        client = Mock()
        answer = AnswerAgent(client, self.config()).generate("q", {}, {"columns": [], "rows": [], "truncated": False})
        self.assertEqual(answer, "La consulta no devolvió filas.")
        client.chat.assert_not_called()

    def test_model_failure_has_deterministic_summary(self):
        client = Mock(); client.chat.side_effect = RuntimeError("offline")
        data = {"columns": ["x"], "rows": [(1,), (2,)], "row_count": 8, "truncated": True}
        answer = AnswerAgent(client, self.config()).generate("q", {}, data)
        self.assertIn("8 filas", answer)
        self.assertIn("truncado", answer)
        self.assertIn("muestra", answer)


if __name__ == "__main__": unittest.main()
