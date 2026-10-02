"""Synchronous Ollama transport. No agent prompts or database logic here."""
import logging
from dataclasses import dataclass
from time import perf_counter

import requests

from core.config import OllamaSettings

logger = logging.getLogger(__name__)


class ModelError(RuntimeError):
    """Actionable transport or model response failure."""


@dataclass(frozen=True)
class ModelResponse:
    content: str
    elapsed_seconds: float
    output_tokens: int
    tokens_per_second: float | None
    done_reason: str


class OllamaClient:
    def __init__(self, settings: OllamaSettings):
        self.settings = settings
        self.session = requests.Session()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.session.close()

    def chat(self, messages: list[dict[str, str]]) -> ModelResponse:
        if not messages:
            raise ValueError("messages cannot be empty")
        config = self.settings
        payload = {
            "model": config.model,
            "messages": messages,
            "stream": False,
            "keep_alive": config.keep_alive,
            "options": {
                "num_ctx": config.num_ctx,
                "num_predict": config.num_predict,
                "temperature": config.temperature,
            },
        }
        started = perf_counter()
        try:
            response = self.session.post(
                f"{config.base_url}/api/chat",
                json=payload,
                timeout=(config.connect_timeout, config.read_timeout),
            )
            if response.status_code == 404:
                raise ModelError(
                    f"Endpoint o modelo no encontrado. Comprueba base_url y ejecuta: "
                    f"ollama pull {config.model}"
                )
            response.raise_for_status()
            data = response.json()
        except requests.Timeout as exc:
            raise ModelError("Ollama excedió el tiempo de espera. Reduce el contexto o aumenta read_timeout.") from exc
        except requests.ConnectionError as exc:
            raise ModelError("No se pudo conectar a Ollama. Inicia la aplicación y revisa base_url.") from exc
        except requests.HTTPError as exc:
            raise ModelError(f"Ollama devolvió HTTP {exc.response.status_code}; revisa sus logs.") from exc
        except requests.RequestException as exc:
            raise ModelError("Solicitud o respuesta JSON inválida de Ollama.") from exc
        except ValueError as exc:
            raise ModelError("Ollama no devolvió JSON válido.") from exc
        if not isinstance(data, dict) or data.get("error") or data.get("done") is not True:
            raise ModelError("Respuesta de Ollama incompleta o con error.")
        message = data.get("message")
        if not isinstance(message, dict):
            raise ModelError("Respuesta sin message válido.")
        if message.get("tool_calls"):
            raise ModelError("El modelo solicitó herramientas; aún no hay ejecutor configurado.")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ModelError("El modelo devolvió una respuesta vacía.")
        count = data.get("eval_count", 0)
        duration = data.get("eval_duration", 0)
        if not isinstance(count, int) or not isinstance(duration, (int, float)):
            raise ModelError("Métricas inválidas en la respuesta de Ollama.")
        elapsed = perf_counter() - started
        reason = str(data.get("done_reason", "unknown"))
        logger.info("model=%s elapsed=%.2fs output_tokens=%s reason=%s", config.model, elapsed, count, reason)
        if reason == "length":
            logger.warning("Respuesta truncada por num_predict; aumenta ese valor si es necesario.")
        return ModelResponse(content, elapsed, count, count / (duration / 1e9) if duration > 0 else None, reason)
