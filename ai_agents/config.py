import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  
    load_dotenv = None  

try:
    from ai_agents import settings as _settings
except Exception:  
    _settings = None  


def _load_env_file() -> None:
    
    if load_dotenv is None:
        return
    project_root = Path(__file__).resolve().parents[1]
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)


_load_env_file()


def get_ollama_base_url() -> str:
    
    
    return os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").rstrip("/")


def get_ollama_model() -> str:
    
    
    return os.getenv("OLLAMA_MODEL", "llama3.2:3b")


def get_ollama_timeout() -> float:
    
    default = 300.0
    if _settings is not None and hasattr(_settings, "DEFAULT_OLLAMA_TIMEOUT"):
        try:
            default = float(getattr(_settings, "DEFAULT_OLLAMA_TIMEOUT"))
        except Exception:
            default = 300.0
    raw = os.getenv("OLLAMA_TIMEOUT", str(default)).strip()
    try:
        return float(raw)
    except Exception:
        return float(default)


def get_debate_max_rounds() -> int:
    
    default = 4
    if _settings is not None and hasattr(_settings, "DEFAULT_DEBATE_MAX_ROUNDS"):
        try:
            default = int(getattr(_settings, "DEFAULT_DEBATE_MAX_ROUNDS"))
        except Exception:
            default = 4
    raw = os.getenv("DEBATE_MAX_ROUNDS", str(default)).strip()
    try:
        v = int(raw)
        return max(1, min(8, v))
    except Exception:
        return int(default)


def get_summary_format_guard_enabled() -> bool:
    
    default = True
    if _settings is not None and hasattr(_settings, "DEFAULT_SUMMARY_FORMAT_GUARD"):
        try:
            default = bool(getattr(_settings, "DEFAULT_SUMMARY_FORMAT_GUARD"))
        except Exception:
            default = True
    raw = os.getenv("SUMMARY_FORMAT_GUARD", "1" if default else "0").strip().lower()
    return raw not in ("0", "false", "no", "off")


def get_summary_similarity_guard_enabled() -> bool:
    
    default = True
    if _settings is not None and hasattr(_settings, "DEFAULT_SUMMARY_SIMILARITY_GUARD"):
        try:
            default = bool(getattr(_settings, "DEFAULT_SUMMARY_SIMILARITY_GUARD"))
        except Exception:
            default = True
    raw = os.getenv("SUMMARY_SIMILARITY_GUARD", "1" if default else "0").strip().lower()
    return raw not in ("0", "false", "no", "off")


def get_datadog_site() -> str:
    
    return os.getenv("DD_SITE", "ap1").lower()


def get_datadog_keys() -> tuple[str | None, str | None]:
    
    
    return os.getenv("DD_API_KEY"), os.getenv("DD_APP_KEY")

