"""Assemble one run's spec from its template (world-assembly layer).

Deployment knowledge lives here — beside node_common.sh, not inside
the standalone scientist package: the credential layout (which files
on this deployment hold the live keys) is a property of the world we
put the scientist into, not of the scientist. The only assembly step
is credential injection into the template's FILL_BEFORE_RUNNING
placeholders; every other config value is already black-on-white in
the task's spec template. The frozen product in the run directory is
the run's complete record.

    python singlenode/specfill.py TEMPLATE OUT BASE_SHA
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TIDE_SPEC = _REPO_ROOT / "runs" / "tide-demo-1" / "spec.json"
_DS_BACKUP = Path.home() / ".claude" / "settings_ds.json.backup"

_PLACEHOLDERS = (None, "", "FILL_BEFORE_RUNNING")


def fill(template: Path, out: Path, base_sha: str) -> dict:
    spec = json.loads(template.read_text(encoding="utf-8"))
    tide = json.loads(_TIDE_SPEC.read_text(encoding="utf-8"))
    ds = json.loads(_DS_BACKUP.read_text(encoding="utf-8"))["env"]
    spec["base_sha"] = base_sha
    model = spec.setdefault("model", {})
    if model.get("api_key") in _PLACEHOLDERS:
        model["api_key"] = tide["model"]["api_key"]
    env = spec.setdefault("assistant", {}).setdefault("env", {})
    for key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        if env.get(key) in _PLACEHOLDERS:
            env[key] = ds[key]
    out.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    return spec


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m scientist.specfill "
              "TEMPLATE OUT BASE_SHA", file=sys.stderr)
        return 2
    fill(Path(argv[0]), Path(argv[1]), argv[2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
