from pathlib import Path

import pytest

from benchmarks.JunoResBench.scripts.prepare_electron_single_site_release import (
    prepare,
)


def test_prepare_links_only_public(tmp_path):
    release = tmp_path / "release"
    (release / "public").mkdir(parents=True)
    (release / "private").mkdir()

    package = prepare(release)

    assert (package / "public").is_symlink()
    assert (package / "public").readlink() == Path("../public")
    assert (package / "public").resolve() == (release / "public").resolve()
    assert not (package / "private").exists()
    assert {path.name for path in (package / "evaluator").iterdir()} == {
        "evaluate.py",
        "scoring.py",
        "sparse_reader.py",
        "submission_api.py",
        "submission_worker.py",
    }


def test_prepare_refuses_nonempty_destination(tmp_path):
    release = tmp_path / "release"
    (release / "public").mkdir(parents=True)
    destination = release / "agent_package"
    destination.mkdir()
    (destination / "keep.txt").write_text("owned", encoding="utf-8")

    with pytest.raises(FileExistsError):
        prepare(release)


def test_prepare_supports_external_runtime_destination(tmp_path):
    release = tmp_path / "mounted" / "release"
    (release / "public").mkdir(parents=True)
    destination = tmp_path / "workspace" / "electron_single_site"

    package = prepare(release, output=destination)

    assert package == destination.resolve()
    assert (package / "public").is_symlink()
    assert (package / "public").resolve() == (release / "public").resolve()
