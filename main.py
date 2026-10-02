"""Application entry point: user request and orchestration belong here."""
import argparse
import json
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
    parser.add_argument("--execute", action="store_true", help="Ejecuta la consulta después de validarla")
    parser.add_argument("--debug", action="store_true", help="Activa diagnóstico seguro (sin credenciales ni filas)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        if not args.agent and not args.pipeline:
            from core.application import create_application

            if args.debug:
                logging.debug("Ruta application; execute=%s", args.execute)
            response = create_application().handle({"question": args.message, "execute": args.execute})
            print(json.dumps(response.to_dict(), ensure_ascii=False, default=str))
            return 0 if response.status != "error" else 1

        # Compatibility route is explicit: --agent and --pipeline retain the old LLM-only mode.
        if args.execute:
            parser.error("--execute no se puede combinar con el modo antiguo --agent/--pipeline")
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
    except Exception as exc:
        # Integration failures may contain SQL, rows, or credentials in their text.
        logging.error("No se pudo completar la solicitud; exception_type=%s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
