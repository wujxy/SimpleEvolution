from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

import pytest

from scientist.ui.reader import RunLayout
from scientist.ui.server import Observatory, encode_sse, make_server


@contextmanager
def running_server(observatory):
    server = make_server(observatory, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def get_response(url):
    return urllib.request.urlopen(url, timeout=2)


def get_json(url):
    with get_response(url) as response:
        return json.loads(response.read())


def get_text(url):
    with get_response(url) as response:
        return response.read().decode("utf-8"), response.headers


def test_snapshot_is_redacted_and_mutating_methods_are_rejected(run_fixture):
    run_dir, scientist = run_fixture
    (scientist / "session" / "wire.jsonl").write_text(
        '{"role":"user","content":"begin"}\n', encoding="utf-8")
    observatory = Observatory(RunLayout.discover(run_dir))
    observatory.poll_once()

    with running_server(observatory) as base_url:
        with get_response(base_url + "/api/snapshot") as response:
            body = response.read()
            snapshot = json.loads(body)
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
        rendered = json.dumps(snapshot)
        assert snapshot["run"]["metadata"]["goal"] == (
            "make reconstruction faster")
        assert "TOP-SECRET" not in rendered
        assert "SECRET-TOKEN" not in rendered
        request = urllib.request.Request(
            base_url + "/api/snapshot", data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=2)
        assert error.value.code == 405
        assert error.value.headers["Allow"] == "GET"


def test_detail_route_only_reads_registered_opaque_id(run_fixture):
    run_dir, scientist = run_fixture
    (scientist / "session" / "wire.jsonl").write_text(
        '{"role":"user","content":"begin"}\n', encoding="utf-8")
    observatory = Observatory(RunLayout.discover(run_dir))
    observatory.poll_once()
    detail_id = observatory.reader.detail_index.ids()[0]

    with running_server(observatory) as base_url:
        detail = get_json(
            base_url + "/api/details/" + urllib.parse.quote(
                detail_id, safe=""))
        assert detail["detail_id"] == detail_id
        assert detail["content"]["content"] == "begin"
        with pytest.raises(urllib.error.HTTPError) as unknown:
            get_response(base_url + "/api/details/detail%3Aunknown")
        assert unknown.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as escaped:
            get_response(base_url + "/api/details/..%2F..%2Fprivate-key")
        assert escaped.value.code == 404


def test_events_endpoint_replays_only_after_cursor(run_fixture):
    run_dir, _ = run_fixture
    observatory = Observatory(RunLayout.discover(run_dir))
    first = observatory.poll_once()
    assert first
    cursor = first[-1]["id"]
    (run_dir / "run.log").write_text(
        "[scientist 20:00:00] step 1/20: thinking\n", encoding="utf-8")
    second = observatory.poll_once()
    assert second

    with running_server(observatory) as base_url:
        replay = get_json(
            base_url + "/api/events?after="
            + urllib.parse.quote(cursor, safe=""))

    assert [item["id"] for item in replay] == [
        item["id"] for item in second]


def test_encode_sse_is_valid_event_stream_frame():
    assert encode_sse({
        "id": "delta-2",
        "type": "seat_updated",
        "data": {"x": 1},
    }) == (
        b'id: delta-2\n'
        b'event: seat_updated\n'
        b'data: {"x": 1}\n\n'
    )


def test_cursor_from_previous_server_requires_fresh_snapshot(run_fixture):
    run_dir, _ = run_fixture
    observatory = Observatory(RunLayout.discover(run_dir))
    observatory.poll_once()

    replay = observatory.events_after("delta-999")

    assert replay[0]["type"] == "snapshot_required"


def test_frontend_has_required_regions_and_safe_rendering_contract(
        run_fixture):
    run_dir, _ = run_fixture
    observatory = Observatory(RunLayout.discover(run_dir))
    with running_server(observatory) as base_url:
        html, headers = get_text(base_url + "/")
        app, _ = get_text(base_url + "/static/app.js")
        css, _ = get_text(base_url + "/static/style.css")

    assert 'id="run-status"' in html
    assert 'id="timeline"' in html
    assert 'id="seats"' in html
    assert 'id="details"' in html
    assert "new EventSource('/api/stream')" in app
    assert ".textContent" in app
    assert ".innerHTML" not in app
    assert "@media (max-width: 900px)" in css
    assert headers["Content-Security-Policy"] == (
        "default-src 'self'; connect-src 'self'; script-src 'self'; "
        "style-src 'self'; object-src 'none'; base-uri 'none'")
