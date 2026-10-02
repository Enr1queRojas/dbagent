import unittest
from unittest.mock import Mock

import requests

from core.config import OllamaSettings, load_settings
from core.ollama_client import ModelError, OllamaClient
from agents.base import Agent


class ClientTests(unittest.TestCase):
    def test_success_and_agent_prompt(self):
        settings = load_settings()
        with OllamaClient(settings.ollama) as client:
            response = Mock(status_code=200)
            response.json.return_value = {
                "done": True, "message": {"content": "Respuesta"},
                "eval_count": 20, "eval_duration": 2_000_000_000,
            }
            client.session.post = Mock(return_value=response)
            result = Agent("analyst", settings, client).run("Analiza ventas")
            self.assertEqual(result.tokens_per_second, 10)
            payload = client.session.post.call_args.kwargs["json"]
            self.assertEqual(payload["model"], "qwen3:30b-instruct")
            self.assertNotIn("think", payload)
            self.assertNotIn("tools", payload)
            self.assertIn("Flujo de análisis", payload["messages"][0]["content"])
            self.assertEqual(payload["messages"][1]["content"], "Analiza ventas")

    def test_connection_failure(self):
        with OllamaClient(OllamaSettings()) as client:
            client.session.post = Mock(side_effect=requests.ConnectionError())
            with self.assertRaisesRegex(ModelError, "No se pudo conectar"):
                client.chat([{"role": "user", "content": "Hi"}])

    def test_unexpected_tools_are_not_executed(self):
        with OllamaClient(OllamaSettings()) as client:
            response = Mock(status_code=200)
            response.json.return_value = {"done": True, "message": {"tool_calls": [{"function": {"name": "sql"}}]}}
            client.session.post = Mock(return_value=response)
            with self.assertRaisesRegex(ModelError, "herramientas"):
                client.chat([{"role": "user", "content": "Hi"}])

    def test_missing_model(self):
        with OllamaClient(OllamaSettings()) as client:
            client.session.post = Mock(return_value=Mock(status_code=404))
            with self.assertRaisesRegex(ModelError, "ollama pull"):
                client.chat([{"role": "user", "content": "Hi"}])


if __name__ == "__main__":
    unittest.main()
