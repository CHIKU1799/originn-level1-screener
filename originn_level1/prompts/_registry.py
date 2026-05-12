"""
Prompt registry.

Every LLM-call site loads its system prompt by prompt_id from here. Files are
versioned in their filename (e.g. `level1_extract.v1.md`); the registry maps
prompt_id → current version and tracks the SHA256 of the on-disk content.

To bump a prompt:
  1. Add the new file (e.g. `level1_extract.v2.md`).
  2. Update the version + filename in _REGISTRY below.
  3. Update any snapshot tests with the new hash.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from string import Template


PROMPTS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class PromptSpec:
    prompt_id: str
    version: str
    filename: str
    has_template_vars: bool = False


_REGISTRY: dict[str, PromptSpec] = {
    "level1_extract": PromptSpec(
        prompt_id="level1_extract",
        version="v1",
        filename="level1_extract.v1.md",
    ),
    "level1_narrate": PromptSpec(
        prompt_id="level1_narrate",
        version="v1",
        filename="level1_narrate.v1.md",
    ),
}


_CACHE: dict[str, tuple[str, str]] = {}


def get(prompt_id: str) -> PromptSpec:
    if prompt_id not in _REGISTRY:
        raise KeyError(
            f"Unknown prompt_id {prompt_id!r}. Registered: {list(_REGISTRY)}"
        )
    return _REGISTRY[prompt_id]


def _load(prompt_id: str) -> tuple[str, str]:
    """Return (content, sha256). Cached after first call."""
    if prompt_id in _CACHE:
        return _CACHE[prompt_id]
    spec = get(prompt_id)
    path = PROMPTS_DIR / spec.filename
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt file missing: {path} (prompt_id={prompt_id})"
        )
    content = path.read_text()
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    _CACHE[prompt_id] = (content, digest)
    return content, digest


def render(prompt_id: str, **vars: str) -> str:
    """
    Return the prompt body. If the prompt is templated, substitute `vars`
    using string.Template (`$var` syntax). Raises if a templated prompt is
    called without all required vars.
    """
    content, _ = _load(prompt_id)
    spec = get(prompt_id)
    if not spec.has_template_vars:
        if vars:
            raise ValueError(
                f"Prompt {prompt_id!r} has no template vars but got: "
                f"{list(vars)}"
            )
        return content
    return Template(content).substitute(**vars)


def hash_of(prompt_id: str) -> str:
    _, digest = _load(prompt_id)
    return digest


def list_prompts() -> list[PromptSpec]:
    return list(_REGISTRY.values())
