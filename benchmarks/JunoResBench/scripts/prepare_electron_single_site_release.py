#!/usr/bin/env python3
"""Assemble the public-only single-electron release package."""

import argparse
from pathlib import Path
import shutil


JRB_ROOT = Path(__file__).resolve().parents[1]
TASK_ROOT = JRB_ROOT / "tasks" / "electron_single_site"
EVALUATOR_FILES = (
    "evaluate.py",
    "scoring.py",
    "sparse_reader.py",
    "submission_api.py",
    "submission_worker.py",
)


def prepare(
    release_root: Path, package_name: str = "agent_package", output=None
) -> Path:
    """Create a package whose only dataset reference is ``../public``."""
    release_root = Path(release_root).resolve()
    public = release_root / "public"
    if not public.is_dir():
        raise ValueError(f"release has no public/ directory: {release_root}")

    package = Path(output).resolve() if output else release_root / package_name
    if package.exists() and any(package.iterdir()):
        raise FileExistsError(f"destination is not empty: {package}")
    package.mkdir(parents=True, exist_ok=True)
    link_target = "../public" if package.parent == release_root else public
    (package / "public").symlink_to(link_target, target_is_directory=True)
    shutil.copy2(TASK_ROOT / "TASK.md", package / "TASK.md")

    evaluator = package / "evaluator"
    evaluator.mkdir()
    for name in EVALUATOR_FILES:
        shutil.copy2(TASK_ROOT / "evaluator" / name, evaluator / name)

    (package / "README.md").write_text(
        "# JunoResBench single-electron package\n\n"
        "Read `TASK.md` for the reconstruction contract. Dataset assets are "
        "available through the read-only `public/` link. The standalone "
        "evaluator is frozen under `evaluator/`.\n",
        encoding="utf-8",
    )
    return package


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(prepare(args.release, output=args.output))


if __name__ == "__main__":
    main()
