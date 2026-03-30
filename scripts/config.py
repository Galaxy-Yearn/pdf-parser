from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from project_paths import workspace_root


PROVIDER_PREFIX = {
    "qwen": "QWEN",
    "kimi": "KIMI",
    "deepseek": "DEEPSEEK",
    "minimax": "MINIMAX",
    "glm": "GLM",
}

DEFAULT_PROVIDER_ENV = {
    "chat": "DEFAULT_CHAT_PROVIDER",
    "embeddings": "DEFAULT_EMBEDDINGS_PROVIDER",
    "ocr": "DEFAULT_OCR_PROVIDER",
    "rerank": "DEFAULT_RERANK_PROVIDER",
    # Dedicated route for permission-gating decisions (should not affect normal chat routing).
    "permission": "DEFAULT_PERMISSION_PROVIDER",
}

DEFAULT_MODEL_ENV = {
    "chat": "DEFAULT_CHAT_MODEL",
    "embeddings": "DEFAULT_EMBEDDINGS_MODEL",
    "ocr": "DEFAULT_OCR_MODEL",
    "rerank": "DEFAULT_RERANK_MODEL",
    # Dedicated model for permission-gating decisions (should not affect normal chat routing).
    "permission": "DEFAULT_PERMISSION_MODEL",
}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    chat_model: str
    embed_model: str
    ocr_model: str
    responses_base_url: str

def load_dotenv(path: str | Path | None = None) -> Path | None:
    env_path = Path(path) if path else workspace_root() / ".env"
    if not env_path.exists():
        return None

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value

    return env_path


def get_default_provider(kind: str) -> str:
    kind = "chat" if kind == "vision" else kind
    env_key = DEFAULT_PROVIDER_ENV.get(kind)
    if not env_key:
        raise ValueError(f"Unknown provider kind: {kind}")
    value = os.getenv(env_key, "").strip().lower()
    return value


def get_default_model(kind: str) -> str:
    kind = "chat" if kind == "vision" else kind
    env_key = DEFAULT_MODEL_ENV.get(kind)
    if not env_key:
        raise ValueError(f"Unknown model kind: {kind}")
    value = os.getenv(env_key, "").strip()
    return value


def get_provider_config(name: str) -> ProviderConfig:
    prefix = PROVIDER_PREFIX.get(name.lower())
    if not prefix:
        raise ValueError(f"Unknown provider: {name}")

    def _env(suffix: str) -> str:
        return os.getenv(f"{prefix}_{suffix}", "").strip()

    base_url = _env("BASE_URL")
    responses_base_url = _env("RESPONSES_BASE_URL") or base_url
    if (
        name.lower() == "qwen"
        and responses_base_url == base_url
        and "dashscope.aliyuncs.com/compatible-mode/v1" in base_url
    ):
        responses_base_url = (
            "https://dashscope.aliyuncs.com/api/v2/apps/protocols/compatible-mode/v1"
        )

    chat_model = _env("CHAT_MODEL")
    return ProviderConfig(
        name=name.lower(),
        base_url=base_url,
        api_key=_env("API_KEY"),
        chat_model=chat_model,
        embed_model=_env("EMBED_MODEL"),
        ocr_model=_env("OCR_MODEL"),
        responses_base_url=responses_base_url,
    )
