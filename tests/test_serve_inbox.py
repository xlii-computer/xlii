"""xlii serve-inbox webhook — cursor-workflows.md B2.1.

The request core (`handle_post`) and the enqueue/sanitization helpers are pure
and tested directly; one live round-trip over an ephemeral localhost port proves
the HTTP wiring + the drain hand-off.
"""

from __future__ import annotations

import http.client
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

from xlii.cmds.serve import handle_post, make_handler
from xlii.inbox import enqueue_inbox, list_inbox, safe_stem


# --------------------------------------------------------------------------- #
#  enqueue + sanitization
# --------------------------------------------------------------------------- #

def test_safe_stem_rejects_traversal_and_junk():
    assert safe_stem("../../etc/passwd") == "etc-passwd"
    assert safe_stem("My Goal!! #1") == "my-goal-1"
    assert safe_stem("") == "webhook"
    assert safe_stem(None) == "webhook"
    assert len(safe_stem("x" * 200)) <= 60


def test_enqueue_clobber_safe(tmp_path):
    xli = tmp_path / ".xlii"
    p1 = enqueue_inbox(xli, "goal: one\n", stem="deploy")
    p2 = enqueue_inbox(xli, "goal: two\n", stem="deploy")
    assert p1.name == "deploy.md" and p2.name == "deploy-2.md"
    assert {p.name for p in list_inbox(xli)} == {"deploy.md", "deploy-2.md"}


def test_enqueue_clobber_safe_for_concurrent_same_stem(tmp_path):
    xli = tmp_path / ".xlii"
    count = 32

    with ThreadPoolExecutor(max_workers=count) as pool:
        paths = list(pool.map(
            lambda i: enqueue_inbox(xli, f"goal: {i}\n", stem="deploy"),
            range(count),
        ))

    files = list_inbox(xli)
    assert len({p.name for p in paths}) == count
    assert len(files) == count
    assert {p.read_text(encoding="utf-8") for p in files} == {
        f"goal: {i}\n" for i in range(count)
    }


# --------------------------------------------------------------------------- #
#  handle_post (pure request core)
# --------------------------------------------------------------------------- #

def test_handle_post_ok_queues_file(tmp_path):
    xli = tmp_path / ".xlii"
    status, text = handle_post(xli, "make the tests pass\n")
    assert status == 201 and "queued" in text
    assert [p.name for p in list_inbox(xli)] == ["webhook.md"]
    queued = (xli / "inbox" / "webhook.md").read_text()
    assert "make the tests pass" in queued
    assert "source: webhook" in queued


def test_handle_post_empty_body_rejected(tmp_path):
    status, _ = handle_post(tmp_path / ".xlii", "   \n")
    assert status == 400
    assert list_inbox(tmp_path / ".xlii") == []


def test_handle_post_token_gate(tmp_path):
    xli = tmp_path / ".xlii"
    # wrong/missing token → 403, nothing queued
    assert handle_post(xli, "x", token_required="s3cret", provided_token=None)[0] == 403
    assert handle_post(xli, "x", token_required="s3cret", provided_token="nope")[0] == 403
    assert list_inbox(xli) == []
    # correct token → queued
    assert handle_post(xli, "x", token_required="s3cret", provided_token="s3cret")[0] == 201
    assert len(list_inbox(xli)) == 1


def test_handle_post_name_hint_sanitized(tmp_path):
    xli = tmp_path / ".xlii"
    handle_post(xli, "body", name_hint="../escape attempt")
    assert [p.name for p in list_inbox(xli)] == ["escape-attempt.md"]


# --------------------------------------------------------------------------- #
#  live round-trip over an ephemeral localhost port
# --------------------------------------------------------------------------- #

def test_live_post_queues_into_inbox(tmp_path):
    xli = tmp_path / ".xlii"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(xli, token="tok"))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/", body="goal: ship it\n",
                     headers={"X-XLII-Token": "tok", "X-XLII-Name": "ship"})
        resp = conn.getresponse()
        assert resp.status == 201
        # a POST without the token is refused
        conn.request("POST", "/", body="goal: sneak\n")
        assert conn.getresponse().status == 403
    finally:
        httpd.shutdown()
        httpd.server_close()
    names = [p.name for p in list_inbox(xli)]
    assert names == ["ship.md"]
    assert "goal: ship it" in (xli / "inbox" / "ship.md").read_text()
    assert "source: webhook" in (xli / "inbox" / "ship.md").read_text()


def test_serve_inbox_refuses_insecure_no_token_off_loopback(tmp_path, capsys):
    from xlii.cmds.serve import serve

    project = type("P", (), {"xli_dir": tmp_path / ".xlii"})()
    assert serve(
        project, host="100.64.0.7", expose=True, insecure_no_token=True,
    ) == 1
    assert "loopback-only" in capsys.readouterr().err
