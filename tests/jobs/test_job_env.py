"""Tests for the shared worker environment (simpleevo.jobs.job_env)."""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from simpleevo.jobs import job_env


def test_package_parent_points_at_the_repo():
    # simpleevo/scientist/supervisor all live beside the simpleevo package.
    parent = job_env.package_parent()
    assert (parent / "simpleevo").is_dir()
    assert (parent / "scientist").is_dir()
    assert (parent / "supervisor").is_dir()
    assert (parent / "simpleevo" / "assistant").is_dir()


def test_worker_environment_sets_pythonpath_and_forwarded_env():
    base = {
        "PATH": "/usr/bin",
        "HEPAI_API_KEY": "secret-hepai",
        "ANTHROPIC_API_KEY": "secret-anthropic",
        "HTTP_PROXY": "http://proxy",
        "SOME_UNRELATED": "x",
    }
    env = job_env.worker_environment(base)
    # Full host env is preserved...
    assert env["PATH"] == "/usr/bin"
    assert env["SOME_UNRELATED"] == "x"
    # ...plus PYTHONPATH pointing at the packages...
    assert str(job_env.package_parent()) in env["PYTHONPATH"]
    # ...plus the forwarded API-key/proxy vars.
    assert env["HEPAI_API_KEY"] == "secret-hepai"
    assert env["ANTHROPIC_API_KEY"] == "secret-anthropic"
    assert env["HTTP_PROXY"] == "http://proxy"


def test_worker_environment_appends_existing_pythonpath():
    base = {"PYTHONPATH": "/some/existing"}
    env = job_env.worker_environment(base)
    assert env["PYTHONPATH"].startswith(str(job_env.package_parent()))
    assert "/some/existing" in env["PYTHONPATH"]


def test_write_job_env_renders_only_forwarded_vars(tmp_path):
    base = {
        "PATH": "/usr/bin",
        "HEPAI_API_KEY": "secret-hepai",
        "ANTHROPIC_API_KEY": "secret-anthropic",
        "SSL_CERT_FILE": "/etc/ssl/cert.pem",
        "SOME_UNRELATED": "x",
    }
    path = job_env.write_job_env(tmp_path / "job_env.sh", base)
    assert path.exists()
    assert os.stat(path).st_mode & 0o777 == 0o600
    text = path.read_text(encoding="utf-8")
    assert "export HEPAI_API_KEY=secret-hepai" in text
    assert "export ANTHROPIC_API_KEY=secret-anthropic" in text
    assert "export SSL_CERT_FILE=/etc/ssl/cert.pem" in text
    # PATH / unrelated vars must NOT leak onto execute nodes.
    assert "export PATH=" not in text
    assert "SOME_UNRELATED" not in text
    # And the package path is exported for the worker import.
    assert "export PYTHONPATH=" in text
    assert shlex.quote(str(job_env.package_parent())) in text


def test_job_env_is_sourceable_bash():
    # The generated file must be valid bash (sources cleanly and sets vars).
    base = {"HEPAI_API_KEY": "k", "PATH": "/usr/bin"}
    path = job_env.write_job_env("/tmp/sev_job_env_test.sh", base)
    try:
        out = subprocess.run(
            ["bash", "-c", f"source {shlex.quote(str(path))}; echo $HEPAI_API_KEY"],
            capture_output=True, text=True,
        )
        assert out.returncode == 0
        assert out.stdout.strip() == "k"
    finally:
        path.unlink(missing_ok=True)
