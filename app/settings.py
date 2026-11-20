"""Runtime configuration. Entra endpoints are validated before sending credentials."""

import os
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    azure_openai_base_url: str = ""
    foundry_endpoint: str = ""
    azure_token_scope: Literal["https://cognitiveservices.azure.com/.default"] = (
        "https://cognitiveservices.azure.com/.default"
    )
    frontier_deployment: str = "gpt-6.1-sol"
    small_deployment: str = "gpt-4.1-mini"
    router_deployment: str = "model-router"
    decision_deployment: str = "decision-1.1"
    embedding_deployment: str = "text-embedding-3-small"
    prompt_cache_mode: Literal["explicit", "auto"] = "explicit"
    prompt_cache_key: str = "clouddesk:policies:v1"
    default_request_count: int = 5
    min_request_count: int = Field(default=5, ge=5, le=10)
    max_request_count: int = Field(default=10, ge=5, le=10)
    decision_confidence_min: float = Field(default=0.70, ge=0, le=1)
    naive_similarity_threshold: float = Field(default=0.80, ge=0, le=1)
    retry_limit: int = Field(default=2, ge=0, le=5)
    safety_ceiling: int = Field(default=6, ge=1, le=6)
    doc_token_budget: int = Field(default=8000, ge=1)
    tight_kill_switch_usd: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    demo_access_code: SecretStr = SecretStr("")
    max_spend_per_hour_usd: Decimal = Field(default=Decimal("2.00"), gt=0, allow_inf_nan=False)
    foundry_mode: Literal["live", "fake"] = "live"
    replay_only: bool = False
    data_dir: Path = Field(default_factory=lambda: Path("/home/data") if os.getenv("WEBSITE_SITE_NAME")
                                  else Path(".data"))
    replay_dir: Path | None = None
    pricing_path: Path = ROOT / "config" / "pricing.json"
    pricing_override_json: str = ""
    applicationinsights_connection_string: SecretStr = SecretStr("")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @model_validator(mode="before")
    @classmethod
    def handle_empty_optionals(cls, values: object) -> object:
        if isinstance(values, dict):
            return {k: v for k, v in values.items()
                    if not (k in {"tight_kill_switch_usd", "replay_dir"} and v == "")}
        return values

    @model_validator(mode="after")
    def validate_counts(self) -> "Settings":
        if not self.min_request_count <= self.default_request_count <= self.max_request_count:
            raise ValueError("Request counts must be ordered within 5–10.")
        return self

    @property
    def source(self) -> str:
        return "replay-only" if self.replay_only else self.foundry_mode

    @property
    def recordings(self) -> Path:
        return self.replay_dir or self.data_dir / "replays"

    @property
    def deployments(self) -> dict[str, str]:
        return {
            "frontier": self.frontier_deployment, "small": self.small_deployment,
            "router": self.router_deployment, "decision": self.decision_deployment,
            "embedding": self.embedding_deployment,
        }

    def live_problem(self) -> str | None:
        for field, url, suffixes, path in (
            ("AZURE_OPENAI_BASE_URL", self.azure_openai_base_url,
             (".openai.azure.com", ".services.ai.azure.com"), "/openai/v1"),
            ("FOUNDRY_ENDPOINT", self.foundry_endpoint, (".services.ai.azure.com",), ""),
        ):
            try:
                target = urlsplit(url)
                valid = (target.scheme == "https" and target.hostname
                         and target.hostname.endswith(suffixes) and target.port in (None, 443)
                         and target.username is None and target.password is None
                         and not target.query and not target.fragment and target.path.rstrip("/") == path)
            except ValueError:
                valid = False
            if not valid:
                return f"Set {field} to the approved Azure resource endpoint."
        if not all(name for role, name in self.deployments.items() if role != "decision"):
            return "Configure all required model deployment names."
        if self.router_deployment.lower() == "auto":
            return "Configure a dedicated model-router deployment; do not use auto."
        if not self.access_code_configured:
            return "Set DEMO_ACCESS_CODE before using live inference."
        return None

    @property
    def access_code_configured(self) -> bool:
        code = self.demo_access_code.get_secret_value()
        return bool(code and code != "change-me-locally" and not code.startswith("@Microsoft.KeyVault("))
