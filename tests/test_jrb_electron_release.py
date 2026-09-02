from pathlib import Path
import subprocess

import pytest

from singlenode_mounts import parse_read_only_mounts


def test_parse_read_only_mounts():
    assert parse_read_only_mounts("/host/public:/data/jrb/public") == [
        ("/host/public", "/data/jrb/public")
    ]


def test_parse_read_only_mounts_rejects_malformed_value():
    with pytest.raises(ValueError, match="SOURCE:DESTINATION"):
        parse_read_only_mounts("/host/public")


def test_node_container_maps_explicit_mount_read_only():
    root = Path(__file__).resolve().parents[1]
    script = f'''\
source "{root}/singlenode/node_common.sh"
RUN_DIR=/unused/run
NODE_TEMPLATE=/unused/template
SPEC_TEMPLATE=/unused/spec.json
NODE_IMAGE=/unused/image.sif
EXTRA_RO_MOUNTS=/host/public:/data/jrb/public
apptainer() {{ printf '%s\\n' "$@"; }}
node_container /bin/true
'''
    result = subprocess.run(
        ["bash", "-c", script], check=True, capture_output=True, text=True
    )
    assert "/host/public:/data/jrb/public:ro" in result.stdout.splitlines()
