from functools import lru_cache
from pathlib import Path


def _prompts_root() -> Path:
    return Path(__file__).resolve().parents[4] / "prompts"


@lru_cache(maxsize=16)
def load_prompt(name: str, version: str = "v1") -> str:
    path = _prompts_root() / name / f"{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"缺少提示词文件: {path}")
    return path.read_text(encoding="utf-8")


def prompt_version(name: str, version: str = "v1") -> str:
    return f"{name}-{version}"
