"""Validated application configuration, independent of the working directory."""
import os
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[1]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OllamaSettings(StrictModel):
    base_url: str = "http://localhost:11434"
    model: str = Field(default="qwen3:30b-instruct", min_length=1)
    connect_timeout: float = Field(default=10, gt=0)
    read_timeout: float = Field(default=600, gt=0)
    keep_alive: str = "10m"
    num_ctx: int = Field(default=8192, ge=512)
    num_predict: int = Field(default=2048, gt=0)
    temperature: float = Field(default=0.2, ge=0, le=2)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, query or fragment")
        return value.rstrip("/")


class AgentSettings(StrictModel):
    system_prompt: str = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)


class Settings(StrictModel):
    ollama: OllamaSettings
    default_agent: str
    common_prompt: str = ""
    agents: dict[str, AgentSettings]

    @model_validator(mode="after")
    def validate_default(self):
        if self.default_agent not in self.agents:
            raise ValueError("default_agent must exist in agents")
        return self


def load_settings(path: Path | None = None) -> Settings:
    config_path = path or ROOT / "config" / "agents.yml"
    try:
        with config_path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML inválido en {config_path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("ollama"), dict):
        raise ValueError("YAML must contain an ollama mapping")
    for env_name, field in (("OLLAMA_BASE_URL", "base_url"), ("OLLAMA_MODEL", "model")):
        if value := os.getenv(env_name):
            data["ollama"][field] = value
    return Settings.model_validate(data)
