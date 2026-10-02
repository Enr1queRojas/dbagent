"""Prompt-based agents sharing one model client; histories are request-local."""
from pathlib import Path

from core.config import Settings, ROOT
from core.ollama_client import OllamaClient, ModelResponse


class Agent:
    def __init__(self, name: str, settings: Settings, client: OllamaClient):
        if name not in settings.agents:
            raise ValueError(f"Unknown agent: {name}. Available: {', '.join(settings.agents)}")
        config = settings.agents[name]
        parts = [settings.common_prompt, config.system_prompt]
        skill_root = (ROOT / "skills").resolve()
        for name in config.skills:
            path = (skill_root / f"{name}.md").resolve()
            if not path.is_relative_to(skill_root):
                raise ValueError("Skill path must stay inside skills/")
            parts.append(path.read_text(encoding="utf-8"))
        self.system_prompt = "\n\n".join(parts)
        self.client = client

    def run(self, message: str) -> ModelResponse:
        if not message.strip():
            raise ValueError("El mensaje no puede estar vacío")
        return self.client.chat([
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": message},
        ])
