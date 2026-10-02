"""Application entry point: user request and orchestration belong here."""
import argparse
import logging
from pathlib import Path

from agents.base import Agent
from core.config import load_settings
from core.ollama_client import OllamaClient, ModelError

USER_MESSAGE = "Quiero explorar mis ventas y entender qué productos están creciendo. ¿Cómo empezaríamos?"


def main() -> int:
    parser = argparse.ArgumentParser(description="Asistente local de análisis de datos")
    parser.add_argument("message", nargs="?", default=USER_MESSAGE)
    parser.add_argument("--agent", help="Agente definido en config/agents.yml")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--pipeline", action="store_true", help="Demostración: coordinator -> analyst -> reporter")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings(args.config)
        with OllamaClient(settings.ollama) as client:
            if args.pipeline:
                previous = ""
                for name in ("coordinator", "analyst", "reporter"):
                    request = args.message
                    if previous:
                        request += "\n\nBorrador del agente anterior (no es evidencia verificada):\n" + previous
                    result = Agent(name, settings, client).run(request)
                    previous = result.content
                    print(f"\n--- {name} ---\n{result.content}")
            else:
                agent = Agent(args.agent or settings.default_agent, settings, client)
                result = agent.run(args.message)
                print(result.content)
        return 0
    except (ModelError, ValueError, OSError) as exc:
        logging.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
