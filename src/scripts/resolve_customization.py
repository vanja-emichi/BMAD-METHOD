#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Resolve a skill's default, team, and user TOML customization layers."""

import argparse
import json
import os
import sys
from pathlib import Path

# Installed scripts are consumer files, not a location for interpreter caches.
sys.dont_write_bytecode = True

try:
    from config_utils import ConfigError, load_customization
except ModuleNotFoundError as error:
    if error.name != "tomllib":
        raise
    sys.stderr.write("error: Python 3.11+ is required (stdlib `tomllib` not found).\n")
    raise SystemExit(3) from None


_MISSING = object()


def find_project_root(start: Path) -> Path | None:
    # Hosts that know the project root explicitly (e.g. the Agent Zero plugin,
    # where skills are bundled under the plugin repo rather than the project)
    # can pass it via BMAD_PROJECT_ROOT. This takes precedence over the walk-up
    # heuristic, which would otherwise resolve to the plugin's own .git and
    # silently ignore team/user customization overrides.
    env_root = os.environ.get("BMAD_PROJECT_ROOT", "").strip()
    if env_root:
        root = Path(env_root).expanduser()
        if root.is_dir():
            return root.resolve()
    current = start.resolve()
    while True:
        if (current / "_bmad").exists() or (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def extract_key(data, dotted_key: str):
    current = data
    for part in dotted_key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def write_json_stdout(output) -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    sys.stdout.write(json.dumps(output, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve skill customization using three-layer TOML merge."
    )
    parser.add_argument(
        "--skill", "-s", required=True, help="Absolute path to the skill directory"
    )
    parser.add_argument(
        "--key",
        "-k",
        action="append",
        default=[],
        help="Dotted field path to resolve (repeatable). Omit for full dump.",
    )
    parser.add_argument(
        "--project-root", "-p",
        help="Absolute path to the project root (contains _bmad/). Use this when "
             "skills are bundled outside the project (e.g. the Agent Zero plugin); "
             "overrides the walk-up heuristic and the BMAD_PROJECT_ROOT env var. "
             "Mirrors resolve_config.py --project-root.",
    )
    args = parser.parse_args()

    skill_dir = Path(args.skill).resolve()
    # Project-root resolution: explicit --project-root (validated) > BMAD_PROJECT_ROOT
    # env var (inside find_project_root) > walk-up heuristic. The walk-up is unsafe
    # when skills live under a plugin repo (it hits the plugin's .git), which is
    # exactly why --project-root / the env var exist.
    if args.project_root:
        project_root = Path(args.project_root).expanduser().resolve()
        if not project_root.is_dir():
            sys.stderr.write(f"error: --project-root not a directory: {project_root}\n")
            return 1
    else:
        project_root = find_project_root(skill_dir) or find_project_root(Path.cwd())

    try:
        merged = load_customization(project_root, skill_dir)
    except ConfigError as error:
        sys.stderr.write(f"error: {error}\n")
        return 1

    output = merged
    if args.key:
        output = {}
        for key in args.key:
            value = extract_key(merged, key)
            if value is not _MISSING:
                output[key] = value
    write_json_stdout(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
