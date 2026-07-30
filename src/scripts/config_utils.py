"""Shared strict TOML loading and structural merge support."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Iterable


class ConfigError(ValueError):
    """Raised when a present configuration layer cannot be used safely."""


_KEYED_MERGE_FIELDS = ("code", "id")


def _yaml_scalar(text: str) -> Any:
    """Coerce a plain YAML scalar (string/number/bool/null) for the subset we read."""
    s = text.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes", "on"): return True
    if low in ("false", "no", "off"): return False
    if low in ("null", "~", ""): return None
    try: return int(s)
    except ValueError: pass
    try: return float(s)
    except ValueError: pass
    return s


def _load_yaml_subset(path: Path) -> dict[str, Any]:
    """Minimal stdlib fallback for BMAD's config.yaml (nested maps + scalars only).

    Returns {} on any structural surprise — callers treat YAML as a best-effort
    legacy layer, so a parse issue must never break the (authoritative) TOML read.
    """
    try:
        root: dict[str, Any] = {}
        stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            line = raw.strip()
            if ":" not in line:
                return {}  # sequences / multiline / anchors → outside our subset
            key, _, val = line.partition(":")
            key = key.strip().strip('"').strip("'")
            val = val.split(" #", 1)[0].strip()  # strip inline comment
            while stack and indent <= stack[-1][0]:
                stack.pop()
            if not stack:
                return {}
            parent = stack[-1][1]
            if val == "":
                node: dict[str, Any] = {}
                parent[key] = node
                stack.append((indent, node))
            else:
                parent[key] = _yaml_scalar(val)
        return root
    except Exception:
        return {}


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a legacy config.yaml as a best-effort layer ({} if absent/unreadable).

    Prefers PyYAML when available; otherwise a stdlib subset parser. Never raises —
    config.yaml is a legacy layer and must not break the authoritative TOML config.
    """
    if not path.is_file():
        return {}
    try:
        import yaml  # type: ignore
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except ImportError:
        return _load_yaml_subset(path)
    except Exception:
        return {}


def load_toml(path: Path, *, required: bool = False) -> dict[str, Any]:
    """Load a TOML table, allowing absence only for optional layers."""
    if not path.exists():
        if required:
            raise ConfigError(f"required TOML file not found: {path}")
        return {}
    if not path.is_file():
        raise ConfigError(f"TOML layer is not a file: {path}")
    try:
        with path.open("rb") as stream:
            parsed = tomllib.load(stream)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"failed to parse {path}: {error}") from error
    except OSError as error:
        raise ConfigError(f"failed to read {path}: {error}") from error
    if not isinstance(parsed, dict):
        raise ConfigError(f"TOML layer did not parse to a table: {path}")
    return parsed


def _detect_keyed_merge_field(items: list[Any]) -> str | None:
    if not items or not all(isinstance(item, dict) for item in items):
        return None
    for candidate in _KEYED_MERGE_FIELDS:
        if all(candidate in item for item in items):
            for item in items:
                value = item[candidate]
                if not isinstance(value, str):
                    raise ConfigError(
                        f"keyed array identifier `{candidate}` must be a string, "
                        f"got {type(value).__name__}"
                    )
                if not value:
                    raise ConfigError(
                        f"keyed array identifier `{candidate}` must not be empty"
                    )
            return candidate
    return None


def _merge_arrays(base: list[Any], override: list[Any]) -> list[Any]:
    keyed_field = _detect_keyed_merge_field(base + override)
    if keyed_field is None:
        return list(base) + list(override)

    result: list[Any] = []
    index_by_key: dict[str, int] = {}
    for item in base:
        copied = dict(item)
        index_by_key[copied[keyed_field]] = len(result)
        result.append(copied)
    for item in override:
        copied = dict(item)
        key = copied[keyed_field]
        if key in index_by_key:
            result[index_by_key[key]] = copied
        else:
            index_by_key[key] = len(result)
            result.append(copied)
    return result


def structural_merge(base: Any, override: Any) -> Any:
    """Merge tables recursively, keyed table arrays by identity, and append other arrays."""
    if isinstance(base, dict) and isinstance(override, dict):
        result = dict(base)
        for key, value in override.items():
            result[key] = structural_merge(result[key], value) if key in result else value
        return result
    if isinstance(base, list) and isinstance(override, list):
        return _merge_arrays(base, override)
    return override


def merge_layers(layers: Iterable[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for layer in layers:
        merged = structural_merge(merged, layer)
    return merged


def load_central_config(project_root: Path) -> dict[str, Any]:
    bmad_dir = project_root / "_bmad"
    return merge_layers(
        (
            # Legacy YAML first (lowest precedence): bmad is mid-migration from
            # config.yaml → config.toml, and many installed skills/scripts still
            # write config.yaml. Reading it as a base layer keeps those values
            # resolvable; config.toml (authoritative) overrides it.
            load_yaml(bmad_dir / "config.yaml"),
            load_yaml(bmad_dir / "config.user.yaml"),
            load_toml(bmad_dir / "config.toml", required=True),
            load_toml(bmad_dir / "config.user.toml"),
            load_toml(bmad_dir / "custom" / "config.toml"),
            load_toml(bmad_dir / "custom" / "config.user.toml"),
        )
    )


def load_customization(project_root: Path | None, skill_dir: Path) -> dict[str, Any]:
    skill_name = skill_dir.name
    custom_dir = project_root / "_bmad" / "custom" if project_root else None
    return merge_layers(
        (
            load_toml(skill_dir / "customize.toml", required=True),
            load_toml(custom_dir / f"{skill_name}.toml") if custom_dir else {},
            load_toml(custom_dir / f"{skill_name}.user.toml") if custom_dir else {},
        )
    )
