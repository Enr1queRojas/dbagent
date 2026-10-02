import unittest
from unittest.mock import Mock

from core.ollama_client import ModelError, ModelResponse
from agents.sql_agent import SQLAgent


def response(content, reason="stop"):
    return ModelResponse(content, 0.1, 10, 10.0, reason)


CONTEXT = '{"objects":{"[dbo].[Orders]":{"columns":[{"column_name":"CustomerID"}]}}}'


class SQLAgentTests(unittest.TestCase):
    def test_valid_plan_and_prompt(self):
        client = Mock()
        client.chat.return_value = response('{"sql":"SELECT TOP (10) o.CustomerID FROM [dbo].[Orders] AS o WHERE o.CustomerID = ?","params":["ALFKI"]}')
        plan = SQLAgent(client).generate("pedidos", CONTEXT)
        self.assertEqual(plan.params, ["ALFKI"])
        self.assertIn('"context":', client.chat.call_args.args[0][1]["content"])

    def test_clarification(self):
        client = Mock()
        client.chat.return_value = response('{"sql":null,"params":[],"clarification":"¿Importe o unidades?"}')
        self.assertIn("Importe", SQLAgent(client).generate("ventas", CONTEXT).clarification)

    def test_invalid_json_and_bounded_repair(self):
        client = Mock()
        client.chat.side_effect = [response("not json"), response("still not json")]
        with self.assertRaisesRegex(ValueError, "tras una reparación"):
            SQLAgent(client).generate("pedidos", CONTEXT)
        self.assertEqual(client.chat.call_count, 2)

    def test_invalid_params(self):
        client = Mock()
        client.chat.side_effect = [response('{"sql":"SELECT * FROM [dbo].[Orders] WHERE CustomerID = ?","params":[]}'), response("bad")]
        with self.assertRaises(ValueError):
            SQLAgent(client).generate("pedidos", CONTEXT)

    def test_truncated_is_not_repaired(self):
        client = Mock()
        client.chat.return_value = response('{"sql":"SELECT', "length")
        with self.assertRaisesRegex(ValueError, "truncada"):
            SQLAgent(client).generate("pedidos", CONTEXT)
        client.chat.assert_called_once()

    def test_http_failure_propagates(self):
        client = Mock()
        client.chat.side_effect = ModelError("HTTP 500")
        with self.assertRaisesRegex(ModelError, "HTTP 500"):
            SQLAgent(client).generate("pedidos", CONTEXT)

    def test_one_markdown_wrapper_is_allowed(self):
        client = Mock()
        client.chat.return_value = response('```json\n{"sql":"SELECT * FROM [dbo].[Orders]","params":[]}\n```')
        self.assertIsNotNone(SQLAgent(client).generate("pedidos", CONTEXT).sql)


if __name__ == "__main__":
    unittest.main()
