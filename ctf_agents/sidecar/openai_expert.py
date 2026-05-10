"""Optional OpenAI-compatible expert sidecar for offline challenge analysis.

This module deliberately does not integrate with the contest
supervisor.  It is a P2, operator-triggered helper that reads one
challenge artifact directory and writes advisory files that must still
pass the existing sidecar validator and FlagGuard before any submit.
"""
from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from ctf_agents.sidecar.codex_validator import validate_codex_candidate


DEFAULT_ALLOWED_CATEGORIES = ("misc", "forensics", "crypto", "reverse", "web")
DEFAULT_DISALLOWED_PATHS = (
    ".env",
    ".secrets",
    "state",
    "logs",
)
TEXT_PREVIEW_EXTENSIONS = {
    ".asm",
    ".c",
    ".cfg",
    ".conf",
    ".cpp",
    ".csv",
    ".h",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".log",
    ".md",
    ".php",
    ".py",
    ".sage",
    ".sh",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


@dataclasses.dataclass(frozen=True)
class ExpertSidecarConfig:
    enabled: bool = False
    provider: str = "openai"
    default_model: str = ""
    hard_model: str = ""
    api_base_url: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    reasoning_effort: str = "high"
    max_calls_total: int = 8
    max_calls_per_challenge: int = 1
    timeout_s: float = 600.0
    max_input_files: int = 20
    max_attachment_mb: float = 20.0
    max_preview_chars: int = 4000
    budget_usd_soft_limit: float = 30.0
    allowed_categories: tuple[str, ...] = DEFAULT_ALLOWED_CATEGORIES
    disallowed_paths: tuple[str, ...] = DEFAULT_DISALLOWED_PATHS

    @classmethod
    def from_dict(cls, payload: Optional[dict[str, Any]]) -> "ExpertSidecarConfig":
        data = dict(payload or {})
        if "allowed_categories" in data:
            data["allowed_categories"] = tuple(str(x).lower() for x in data["allowed_categories"])
        if "disallowed_paths" in data:
            data["disallowed_paths"] = tuple(str(x) for x in data["disallowed_paths"])
        for key in (
            "provider",
            "default_model",
            "hard_model",
            "api_base_url",
            "api_key_env",
            "reasoning_effort",
        ):
            if key in data and data[key] is not None:
                data[key] = str(data[key]).strip()
        return cls(**data)


@dataclasses.dataclass(frozen=True)
class ChallengeManifest:
    challenge_id: str
    challenge_dir: str
    files: list[str]
    file_entries: list[dict[str, Any]]
    total_bytes: int
    truncated: bool

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class ExpertRunResult:
    ran: bool
    status: str
    api_key_status: str
    model: str
    manifest: Optional[dict[str, Any]] = None
    notes_path: str = ""
    candidates_path: str = ""
    valid_candidates: int = 0
    validation_errors: list[str] = dataclasses.field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def api_key_status(env: Optional[dict[str, str]] = None, *, key_name: str = "OPENAI_API_KEY") -> str:
    source = env if env is not None else os.environ
    label = str(key_name or "OPENAI_API_KEY")
    return f"{label}=SET" if source.get(label) else f"{label}=UNSET"


def _resolve_project_root(project_root: str | Path) -> Path:
    return Path(project_root).resolve()


def _artifact_root(project_root: Path) -> Path:
    return (project_root / "artifacts" / "challenges").resolve()


def _relative_artifact_path(path: Path, project_root: Path) -> str:
    return path.resolve().relative_to(project_root).as_posix()


def _ensure_challenge_dir(challenge_dir: str | Path, challenge_id: str, project_root: Path) -> Path:
    root = _artifact_root(project_root)
    path = Path(challenge_dir).resolve()
    expected = (root / str(challenge_id)).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("challenge_dir must be under artifacts/challenges/<id>/") from exc
    if path != expected:
        raise ValueError(f"challenge_dir must be exactly {expected}")
    if not path.exists() or not path.is_dir():
        raise ValueError(f"challenge_dir does not exist: {path}")
    return path


def _path_has_disallowed_token(path: Path, config: ExpertSidecarConfig, project_root: Path) -> bool:
    rel = _relative_artifact_path(path, project_root).lower()
    parts = {p.lower() for p in Path(rel).parts}
    for token in config.disallowed_paths:
        token_lower = token.lower().strip("/")
        if not token_lower:
            continue
        if token_lower in parts or token_lower in rel:
            return True
    return False


def build_challenge_manifest(
    challenge_dir: str | Path,
    *,
    challenge_id: str,
    config: ExpertSidecarConfig,
    project_root: str | Path,
) -> ChallengeManifest:
    project = _resolve_project_root(project_root)
    base = _ensure_challenge_dir(challenge_dir, challenge_id, project)
    max_bytes_per_file = int(float(config.max_attachment_mb) * 1024 * 1024)

    files: list[str] = []
    entries: list[dict[str, Any]] = []
    total = 0
    truncated = False
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.name in {"expert_notes.md", "expert_candidates.json"}:
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(base)
        except ValueError as exc:
            raise ValueError(f"file escaped challenge artifact root: {path}") from exc
        if _path_has_disallowed_token(resolved, config, project):
            raise ValueError(f"disallowed path inside challenge artifact root: {path}")
        size = resolved.stat().st_size
        if size > max_bytes_per_file:
            raise ValueError(f"input file exceeds max_attachment_mb: {resolved.name}")
        if len(files) >= int(config.max_input_files):
            truncated = True
            continue
        rel = _relative_artifact_path(resolved, project)
        files.append(rel)
        entries.append(_file_entry(resolved, rel, size, config))
        total += size

    return ChallengeManifest(
        challenge_id=str(challenge_id),
        challenge_dir=_relative_artifact_path(base, project),
        files=files,
        file_entries=entries,
        total_bytes=total,
        truncated=truncated,
    )


def _file_entry(path: Path, rel: str, size: int, config: ExpertSidecarConfig) -> dict[str, Any]:
    preview, kind, truncated = _file_preview(path, max_chars=int(config.max_preview_chars))
    return {
        "path": rel,
        "size_bytes": size,
        "preview_kind": kind,
        "preview_truncated": truncated,
        "preview": preview,
    }


def _file_preview(path: Path, *, max_chars: int) -> tuple[str, str, bool]:
    if max_chars <= 0:
        return "", "none", True

    raw = path.read_bytes()[: max_chars + 1]
    truncated = len(raw) > max_chars
    sample = raw[:max_chars]
    if _looks_text(path, sample):
        return sample.decode("utf-8", errors="replace"), "text", truncated
    hex_chars = max(0, min(max_chars, 512))
    return sample[: max(1, hex_chars // 2)].hex(), "hex", truncated


def _looks_text(path: Path, sample: bytes) -> bool:
    if path.suffix.lower() in TEXT_PREVIEW_EXTENSIONS:
        return True
    if b"\x00" in sample:
        return False
    if not sample:
        return True
    printable = sum(1 for b in sample if b in b"\n\r\t" or 32 <= b <= 126)
    return printable / len(sample) >= 0.85


def _coerce_candidate(candidate: dict[str, Any], *, challenge_id: str, category: str) -> dict[str, Any]:
    out = dict(candidate)
    out.setdefault("challenge_id", str(challenge_id))
    out.setdefault("category", category)
    out.setdefault("submit_recommendation", "never_direct_submit")
    out.setdefault("notes", "")
    confidence = out.get("confidence")
    if isinstance(confidence, str):
        conf = confidence.strip().lower()
        if conf in {"low", "medium", "high"}:
            out["confidence"] = conf
        else:
            try:
                confidence = float(conf)
            except ValueError:
                pass
            else:
                out["confidence"] = "high" if confidence >= 0.75 else "medium" if confidence >= 0.4 else "low"
    elif isinstance(confidence, (int, float)):
        score = float(confidence)
        out["confidence"] = "high" if score >= 0.75 else "medium" if score >= 0.4 else "low"
    return out


def _normalize_mock_response(response: dict[str, Any], *, challenge_id: str, category: str) -> tuple[str, list[dict[str, Any]]]:
    notes = str(response.get("notes", ""))
    raw_candidates = response.get("candidates", [])
    if raw_candidates is None:
        raw_candidates = []
    if not isinstance(raw_candidates, list):
        raise ValueError("mock_response.candidates must be a list")
    candidates = [
        _coerce_candidate(c, challenge_id=challenge_id, category=category)
        for c in raw_candidates
        if isinstance(c, dict)
    ]
    return notes, candidates


def _write_outputs(
    challenge_dir: Path,
    *,
    project_root: Path,
    notes: str,
    candidates: list[dict[str, Any]],
) -> tuple[str, str]:
    notes_path = _safe_output_path(challenge_dir, "expert_notes.md")
    candidates_path = _safe_output_path(challenge_dir, "expert_candidates.json")
    notes_path.write_text(notes, encoding="utf-8")
    candidates_path.write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return (
        _relative_artifact_path(notes_path, project_root),
        _relative_artifact_path(candidates_path, project_root),
    )


def _safe_output_path(challenge_dir: Path, filename: str) -> Path:
    path = challenge_dir / filename
    if path.is_symlink():
        raise ValueError(f"refusing to write through symlink output path: {filename}")
    if path.exists():
        resolved = path.resolve()
        try:
            resolved.relative_to(challenge_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"output path escapes challenge artifact root: {filename}") from exc
    return path


def _expert_prompt(challenge_id: str, category: str, manifest: dict[str, Any]) -> str:
    return (
        "You are solving one offline CTF challenge.\n\n"
        "Hard constraints:\n"
        "1. Use only the files listed in the artifact manifest.\n"
        "2. Do not assume platform access.\n"
        "3. Do not submit anything.\n"
        "4. Do not guess. If evidence is insufficient, return no candidate.\n"
        "5. A candidate must be backed by concrete file paths and derivation.\n"
        "6. Return strict JSON only with keys: notes, candidates.\n"
        "7. Each candidate confidence must be one of low, medium, high.\n\n"
        f"challenge_id: {challenge_id}\n"
        f"category: {category}\n"
        f"manifest: {json.dumps(manifest, ensure_ascii=False)}\n\n"
        "Each candidate must include challenge_id, candidate, confidence, "
        "evidence_paths, submit_recommendation='never_direct_submit', notes."
    )


def _parse_openai_response(data: dict[str, Any]) -> dict[str, Any]:
    text = data.get("output_text")
    if not text:
        if isinstance(data.get("choices"), list):
            for choice in data["choices"]:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    text = content
                    break
                if isinstance(content, list):
                    parts: list[str] = []
                    for item in content:
                        if isinstance(item, dict) and isinstance(item.get("text"), str):
                            parts.append(item["text"])
                    if parts:
                        text = "\n".join(parts)
                        break
        if not text:
            chunks: list[str] = []
            for item in data.get("output", []) if isinstance(data.get("output"), list) else []:
                if not isinstance(item, dict):
                    continue
                for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
                    if isinstance(content, dict) and isinstance(content.get("text"), str):
                        chunks.append(content["text"])
            text = "\n".join(chunks)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("OpenAI response did not contain output_text")
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("OpenAI response JSON must be an object")
    return parsed


def _call_openai_responses(
    *,
    manifest: dict[str, Any],
    model: str,
    reasoning_effort: str,
    timeout_s: float,
    challenge_id: str,
    category: str,
    provider: str,
    api_base_url: str,
    api_key_env: str,
) -> dict[str, Any]:
    key = os.environ.get(api_key_env, "")
    if not key:
        raise RuntimeError(f"{api_key_env} is unset")
    url, headers, payload = _build_provider_request(
        provider=provider,
        api_base_url=api_base_url,
        api_key=key,
        model=model,
        reasoning_effort=reasoning_effort,
        challenge_id=challenge_id,
        category=category,
        manifest=manifest,
    )
    resp = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=float(timeout_s),
    )
    resp.raise_for_status()
    return _parse_openai_response(resp.json())


def _join_url(base: str, suffix: str) -> str:
    return f"{base.rstrip('/')}/{suffix.lstrip('/')}"


def _build_provider_request(
    *,
    provider: str,
    api_base_url: str,
    api_key: str,
    model: str,
    reasoning_effort: str,
    challenge_id: str,
    category: str,
    manifest: dict[str, Any],
) -> tuple[str, dict[str, str], dict[str, Any]]:
    if not api_base_url:
        raise ValueError("api_base_url is not configured")

    provider_key = provider.strip().lower()
    prompt = _expert_prompt(challenge_id, category, manifest)
    payload: dict[str, Any] = {
        "input": prompt,
        "reasoning": {"effort": reasoning_effort},
        "max_output_tokens": 2000,
    }

    if provider_key == "openai":
        payload["model"] = model
        return (
            _join_url(api_base_url, "responses"),
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            payload,
        )

    if provider_key == "azure_openai":
        payload["model"] = model
        return (
            _join_url(api_base_url, "responses"),
            {
                "api-key": api_key,
                "Content-Type": "application/json",
            },
            payload,
        )

    if provider_key == "deepseek":
        return (
            _join_url(api_base_url, "chat/completions"),
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            {
                "model": model,
                "messages": [
                    {"role": "user", "content": prompt},
                ],
                "reasoning_effort": reasoning_effort,
                "thinking": {"type": "enabled"},
                "max_tokens": 2000,
                "temperature": 0,
            },
        )

    raise ValueError(f"unsupported provider: {provider}")


def _select_model(config: ExpertSidecarConfig, category: str) -> str:
    if category.lower() in {"reverse", "web"} and config.hard_model:
        return config.hard_model
    return config.default_model


def run_expert(
    challenge_dir: str | Path,
    *,
    challenge_id: str,
    category: str,
    config: ExpertSidecarConfig,
    project_root: str | Path,
    mock_response: Optional[dict[str, Any]] = None,
    openai_call: Optional[Callable[..., dict[str, Any]]] = None,
    calls_total_used: int = 0,
    calls_for_challenge: int = 0,
    budget_spent_usd: float = 0.0,
) -> ExpertRunResult:
    key_state = api_key_status(key_name=config.api_key_env)
    provider = config.provider.strip().lower()
    model = _select_model(config, category)
    if not config.enabled:
        return ExpertRunResult(False, "disabled", key_state, model, message="expert_sidecar disabled")
    if category.lower() not in {c.lower() for c in config.allowed_categories}:
        return ExpertRunResult(False, "category_not_allowed", key_state, model)
    if provider not in {"openai", "azure_openai", "deepseek"}:
        return ExpertRunResult(False, "unsupported_provider", key_state, model)
    if int(config.max_calls_total) >= 0 and int(calls_total_used) >= int(config.max_calls_total):
        return ExpertRunResult(False, "call_budget_exhausted", key_state, model)
    if (
        int(config.max_calls_per_challenge) >= 0
        and int(calls_for_challenge) >= int(config.max_calls_per_challenge)
    ):
        return ExpertRunResult(False, "challenge_call_budget_exhausted", key_state, model)
    if (
        float(config.budget_usd_soft_limit) >= 0
        and float(budget_spent_usd) >= float(config.budget_usd_soft_limit)
    ):
        return ExpertRunResult(False, "cost_budget_exhausted", key_state, model)
    if mock_response is None and not model:
        return ExpertRunResult(False, "missing_model", key_state, model)
    if mock_response is None and not config.api_base_url:
        return ExpertRunResult(False, "missing_api_base_url", key_state, model)
    if mock_response is None and key_state != f"{config.api_key_env}=SET":
        return ExpertRunResult(False, "missing_api_key", key_state, model)

    project = _resolve_project_root(project_root)
    manifest = build_challenge_manifest(
        challenge_dir,
        challenge_id=challenge_id,
        config=config,
        project_root=project,
    )
    base = _ensure_challenge_dir(challenge_dir, challenge_id, project)

    if mock_response is not None:
        response = mock_response
    else:
        call = openai_call or _call_openai_responses
        response = call(
            manifest=manifest.to_dict(),
            model=model,
            reasoning_effort=config.reasoning_effort,
            timeout_s=config.timeout_s,
            challenge_id=str(challenge_id),
            category=category,
            provider=provider,
            api_base_url=config.api_base_url,
            api_key_env=config.api_key_env,
        )

    notes, candidates = _normalize_mock_response(
        response,
        challenge_id=challenge_id,
        category=category,
    )
    notes_rel, candidates_rel = _write_outputs(
        base,
        project_root=project,
        notes=notes,
        candidates=candidates,
    )

    errors: list[str] = []
    valid = 0
    for idx, candidate in enumerate(candidates):
        candidate_errors = validate_codex_candidate(
            candidate,
            expected_challenge_id=str(challenge_id),
        )
        if candidate_errors:
            errors.extend([f"candidate[{idx}]: {err}" for err in candidate_errors])
        else:
            valid += 1

    status = "ok" if not errors else "invalid_candidates"
    return ExpertRunResult(
        True,
        status,
        key_state,
        model,
        manifest=manifest.to_dict(),
        notes_path=notes_rel,
        candidates_path=candidates_rel,
        valid_candidates=valid,
        validation_errors=errors,
    )


__all__ = [
    "ExpertSidecarConfig",
    "ChallengeManifest",
    "ExpertRunResult",
    "api_key_status",
    "build_challenge_manifest",
    "run_expert",
]
