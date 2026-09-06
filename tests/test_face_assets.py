"""The face frontend assets (tauri-face V2) — packaging + self-containment pins.

The face is no-build vanilla: the assets ARE the source, shipped as xlii
package data and served by serve_face's static side (and by the Tauri
webview at its root). These pins keep that true: every file present, every
file packaged, nothing phoning out to a CDN.
"""

from __future__ import annotations

import json
import re
import socket
import time
from fnmatch import fnmatch
from pathlib import Path

import pytest

from xlii.serve_face import default_assets_dir

from tests.test_serve_face import (  # noqa: F401 — _cleanup_servers is autouse
    _cleanup_servers,
    _fake_state,
    _start_server,
)

ASSETS = default_assets_dir()

REQUIRED = [
    "index.html",
    "css/face.css",
    "js/app.js",
    "js/history_guard.js",
    "js/lock.js",
    "js/wire.js",
    "js/transcript.js",
    "js/input.js",
    "js/overlay_marks.js",
    "js/menubar.js",
    "js/maker_forms.js",
    "js/panes.js",
    "js/markdown.js",
    "vendor/marked.esm.js",
    "img/mark.png",
    "img/avatar.png",
    "img/mojo-talk.png",
    "img/mojo-lab.png",
    "img/xlii-mark.png",
    "img/mojo-mark.png",
    "img/stamp-steel.png",
    "img/stamp-steel-mirror.png",
    "img/stamp-steel@2x.png",
    "img/stamp-steel-mirror@2x.png",
    "skins/y2k/skin.toml",
    "skins/y2k/skin.css",
    "skins/y2k/btn.svg",
]


def _all_asset_files() -> list[Path]:
    return [p for p in ASSETS.rglob("*") if p.is_file()]


def test_required_files_exist():
    for rel in REQUIRED:
        assert (ASSETS / rel).is_file(), f"missing face asset: {rel}"


def test_no_auto_background_letter_watermarks():
    """Public Face load must not stamp mojo/xlii letter logos on the stream."""
    html = (ASSETS / "index.html").read_text()
    css = (ASSETS / "css/face.css").read_text()
    input_js = (ASSETS / "js/input.js").read_text()
    panes_js = (ASSETS / "js/panes.js").read_text()
    assert 'id="stream-mark"' not in html
    assert "stream-mark" not in css
    assert "bindStreamMark" not in input_js
    assert "_parkMark" not in panes_js
    assert "stamp-mojo-steel" not in html
    assert "stamp-xlii-steel" not in html
    assert "stamp-mojo-steel" not in css
    assert "stamp-xlii-steel" not in css
    assert not (ASSETS / "js/stream_mark.js").exists()
    for rel in (
        "img/stamp-mojo-steel.png",
        "img/stamp-mojo-steel-mirror.png",
        "img/stamp-xlii-steel.png",
        "img/stamp-xlii-steel-mirror.png",
    ):
        assert not (ASSETS / rel).exists(), rel
    # Talk/lab flip four-square stays — that is a control, not a background.
    assert 'id="flip-mark"' in html
    assert "img/stamp-steel.png" in html
    assert "img/stamp-steel-mirror.png" in input_js


def test_no_external_urls():
    """Self-contained pin: no src/href/import may reach for the network.
    License URLs in comments are fine; fetching at runtime is not."""
    # Attribute form (no spaces — our hand-written HTML style) + ESM URL
    # imports. Deliberately NOT `href \s*=\s*`: marked's autolinker contains
    # the JS assignment `href = 'http://' + …`, which is string-building,
    # not a fetch.
    net = re.compile(
        r"""(?:src|href)=["']https?://|from\s+["']https?://|import\(["']https?://""",
    )
    for p in _all_asset_files():
        text = p.read_text(errors="replace")
        assert not net.search(text), f"{p.name} references an external URL"


def test_every_asset_is_packaged():
    """Every file under face_assets/ must match a declared package-data glob —
    a new asset that isn't shipped breaks the installed face silently."""
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)
    patterns = [p for p in data["tool"]["setuptools"]["package-data"]["xlii"]
                if p.startswith("face_assets/")]
    assert patterns, "no face_assets package-data globs declared"
    pkg_root = ASSETS.parent
    for p in _all_asset_files():
        rel = p.relative_to(pkg_root).as_posix()
        # fnmatch has no globstar; mirror setuptools' recursive ** by also
        # matching against the flattened single-star form.
        variants = {pat for pat in patterns} | {
            pat.replace("**/", "") for pat in patterns
        }
        matched = any(
            fnmatch(rel, pat) or fnmatch(rel, pat.replace("**", "*/*"))
            or fnmatch(rel, pat.replace("**", "*"))
            for pat in variants
        )
        assert matched, f"{rel} not covered by package-data globs {patterns}"


def test_real_assets_served_by_face_server(tmp_path):
    """The shipped index + modules come back over the static side with the
    right content types (the bare-browser path)."""
    port, _t = _start_server(_fake_state(tmp_path), assets_dir=ASSETS)

    def _get(path):
        c = socket.create_connection(("127.0.0.1", port), timeout=5)
        c.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        time.sleep(0.15)
        chunks = []
        c.settimeout(2)
        try:
            while True:
                chunk = c.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        except TimeoutError:
            # The socket timeout is how this reader knows the response is complete.
            pass
        c.close()
        return b"".join(chunks)

    index = _get("/")
    assert b"200 OK" in index and b"text/html" in index
    assert b"<title>xlii</title>" in index
    css = _get("/css/face.css")
    assert b"200 OK" in css and b"text/css" in css
    js = _get("/js/app.js")
    assert b"200 OK" in js and b"text/javascript" in js
    vendor = _get("/vendor/marked.esm.js")
    assert b"200 OK" in vendor and b"text/javascript" in vendor


def test_vendored_marked_carries_license():
    head = (ASSETS / "vendor/marked.esm.js").read_text(errors="replace")[:400]
    assert "MIT" in head and "marked" in head


def test_m1_parity_chrome_in_index_and_css():
    """face-textual-parity M1: heartbeat + completions DOM; tab ribbon retired."""
    html = (ASSETS / "index.html").read_text()
    assert 'id="heartbeat"' in html
    assert 'id="completions"' in html
    assert 'id="panetabs"' in html and "hidden" in html
    # Side dock lives *inside* #workspace with the transcript (above input).
    # Input / completions are siblings *below* workspace so the prompt width
    # never jumps when a panel opens.
    assert 'id="workspace"' in html
    assert html.index('id="transcript"') < html.index('id="panedeck"')
    assert html.index('id="panedeck"') < html.index('id="inputbar"')
    assert html.index('id="completions"') < html.index('id="inputbar"')
    # panedeck must not wrap the input stack
    panedeck_block = html[html.index('id="panedeck"'):html.index('id="inputbar"')]
    assert 'id="inputbar"' not in panedeck_block
    css = (ASSETS / "css/face.css").read_text()
    assert "#heartbeat" in css
    assert "#completions" in css
    assert "mode-exclusive" in css
    assert "#workspace" in css
    assert "flex-direction: row" in css  # side panel beside transcript only
    assert "overscroll-behavior: contain" in css
    assert ".pane-k" in css and ".pane-v" in css
    assert ".pane-choice" in css
    assert 'op: "set"' in (ASSETS / "js/panes.js").read_text()
    assert "paintChoiceRow" in (ASSETS / "js/panes.js").read_text()
    assert "data-pane-side" in css
    assert 'html[data-pane-side="right"]' in css
    js_panes = (ASSETS / "js/panes.js").read_text()
    assert "paintKnobRow" in js_panes
    assert "paintMakerForm" in js_panes
    assert 'byId.set("home", "Home Hub")' in js_panes
    assert "_placeLocal" in js_panes
    assert "_effectiveSlots" in js_panes
    assert "_picksLocked" in js_panes
    assert "opt.disabled = true" in js_panes
    assert '["stream", ...peekIds, "home"' in js_panes
    menubar = (ASSETS / "js/menubar.js").read_text()
    assert "join:" in menubar
    assert "this._recent" in menubar
    assert "tools:browser" in menubar
    assert "open_browser" in menubar
    assert "pane:gigmake" in menubar
    assert "pane:remotemake" in menubar
    assert "pane:install" in menubar
    assert "pane:jidmake" in menubar
    lock = (ASSETS / "js/lock.js").read_text()
    assert "mouth_via" in lock
    assert "me@ has the mouth on" in lock
    assert 'posture === "phone"' in lock
    assert "Phone glass IS the mouth" in lock
    assert 'hint: "panel"' in menubar
    assert "seed:/gigwork " not in menubar
    assert "this.deck.togglePane(\"tasks\")" in menubar
    assert "PANEL_OWNED_ELSEWHERE" in menubar
    assert '"projects"' in menubar.split("PANEL_OWNED_ELSEWHERE")[1].split("];")[0]
    assert '"home"' in menubar.split("PANEL_OWNED_ELSEWHERE")[1].split("];")[0]
    assert "setVersion" in menubar
    assert "menu-version" in menubar
    assert "menu-version" in css
    assert "flex: 0 0 auto" in css
    assert "min(42vh, 16rem)" not in css
    assert ".menu-drop" in css
    makers = (ASSETS / "js/maker_forms.js").read_text()
    assert "Seed scaffold" in makers
    assert "/plugin new" in makers
    assert "html-place" in css
    assert "slot-html-frame" in css
    assert 'html-place' in js_panes
    assert "sandbox" in makers
    assert "srcdoc" in makers
    assert "themedSrcdoc" in makers
    assert "xlii-skin" in makers
    assert "embedCssForSkin" in (ASSETS / "js/skins.js").read_text()
    assert "allow-forms allow-scripts" in makers
    assert "allow-same-origin" not in makers
    assert "xlii-prefill" in makers
    assert "xlii-plugin-call" in makers
    assert "xlii-plugin-write" in makers
    # postMessage handlers must only trust this page's own slot frames —
    # a cross-origin page holding Face as window.opener must not reach the wire
    assert "_isOwnSlotFrame" in makers
    assert "if (!_isOwnSlotFrame(e.source)) return;" in makers
    assert "paintPluginForm" in makers
    assert "Do not remount" in makers
    assert "sameOcc" in (ASSETS / "js/panes.js").read_text()
    assert "e.detail > 1" in (ASSETS / "js/panes.js").read_text()
    assert "Don't tear the attribute" in (ASSETS / "js/pane_resize.js").read_text()
    assert "allow-same-origin" not in makers
    assert 'r.tone === "knob"' in js_panes
    js_resize = (ASSETS / "js/pane_resize.js").read_text()
    assert "applyPaneChrome" in js_resize
    assert "keep the last drag" in js_resize
    assert "restorePaneWidth" in js_resize
    assert 'setAttribute("data-pane-side"' in js_resize
    assert "flexDirection" in js_resize
    assert "--split-left" in js_resize
    assert "e.clientX - rect.left" in js_resize
    assert "startX - e.clientX" not in js_resize
    assert "xlii-face-split-left" in js_resize
    html = (ASSETS / "index.html").read_text()
    ws_html = html[html.index('id="workspace"'):html.index('id="stream-park"')]
    assert ws_html.index('id="pane-resize"') > ws_html.index('id="slot-a"')
    assert ws_html.index('id="pane-resize"') < ws_html.index('id="slot-b"')
    deck_html = html[html.index('id="panedeck"'):html.index('id="inputbar"')]
    assert 'id="pane-resize"' not in deck_html
    assert "split.hidden" in js_panes
    assert "--split-left" in css
    # Query-string cache-bust so Tauri/WebKit reloads after chrome edits.
    assert 'href="css/face.css?v=' in html
    assert 'src="js/app.js?v=' in html
    assert 'id="jump-latest"' in html
    assert "jumpToLatest" in (ASSETS / "js/transcript.js").read_text()
    assert "#jump-latest" in css
    assert 'id="hud-cwd"' in html
    assert 'id="hud-journal"' in html
    assert 'id="hud-browser"' in html
    assert 'id="hud-trust"' in html
    assert 'id="hud-tier"' in html
    assert 'id="hud-park"' in html
    assert "_parkHud" in (ASSETS / "js/panes.js").read_text()
    assert ".slot-chrome.has-hud" in css
    assert ".slot-chrome #hud" in css
    assert 'id="msgqueue"' in html
    assert "drainQueue" in (ASSETS / "js/input.js").read_text()
    assert "_enqueue" in (ASSETS / "js/input.js").read_text()
    assert 'data-trust' in css
    assert "#msgqueue" in css
    assert "slot-swap" in html
    assert 'id="slot-a"' in html and 'id="slot-b"' in html
    assert "slot-pick" in html
    assert "slot-here" in html
    assert "color-scheme" in (ASSETS / "css/face.css").read_text()
    assert 'data-pane-side="right"' in html
    assert "#pane-rows" in css
    assert '.pane-row.accent[data-kind="container"]::before' in css
    assert ".fileout-path" in css
    # Ribbon must not reappear as a flex strip for users.
    assert "display: none !important" in css
    js = (ASSETS / "js/input.js").read_text()
    assert "setCommandCatalog" in js
    assert "working" in js
    assert "chat-safe in [M]" in js
    assert "target === this.els.input" in js
    assert "target.isContentEditable" in js
    panes = (ASSETS / "js/panes.js").read_text()
    assert "Keep" in panes
    assert "openPane" in panes
    assert "_likelyPaneId" in panes
    assert "byId.has(id)" in panes
    assert 'byId.set("skins"' not in panes
    assert 'byId.set("about"' not in panes
    menu = (ASSETS / "js/menubar.js").read_text()
    # Plugins are a panel (plugins://), not a top-level menubar dropdown.
    assert '["plugins", "Plugins"]' in menu or '"plugins", "Plugins"' in menu
    assert "openPlugins" in menu
    assert '"Plugins", "Keep"' not in menu  # no top-level Plugins title
    assert "_workbenchCycleItem" not in menu
    assert "Panel workbench:" not in menu
    # Pack cycle is gone from chrome; `/workbench` still retargets the dock.
    opt_block = menu.split('case "Options":')[1].split('case "Keep":')[0]
    tools_block = menu.split('case "Tools":')[1].split('case "Commands":')[0]
    assert "opt:workbench:" not in opt_block
    assert "opt:screenshot" in menu
    assert "opt:bold" in opt_block
    assert "pane:bindmake" in opt_block
    assert "pane:bindmake" not in tools_block
    assert "tools:skills" in tools_block
    assert 'id: "pane:plugins"' in tools_block
    proj_block = menu.split('case "Project":')[1].split('case "Tools":')[0]
    assert 'id: "pane:git"' not in proj_block
    assert "PANEL_OWNED_ELSEWHERE" in menu
    assert 'id: "pane:canvas"' in menu
    assert "save_screenshot" in menu
    assert "captureShellShot" not in menu
    assert "pane:plugins" in (ASSETS / "js/app.js").read_text() or "openPlugins" in (
        ASSETS / "js/app.js"
    ).read_text()
    html = (ASSETS / "index.html").read_text()
    assert 'id="quickstrip"' in html
    assert "#quickstrip" in (ASSETS / "css/face.css").read_text()
    js_app = (ASSETS / "js/app.js").read_text()
    # Commander F-keys are stable verbs; pack doors are not the F-row.
    assert "COMMANDER_FKEYS" in js_app
    assert 'label: "Home Hub"' in js_app
    assert '"Keep"' in menu
    assert '"Panel Workbench"' not in menu.split("const TITLES")[1].split("];")[0]
    assert '"Attach"' not in menu.split("const TITLES")[1].split("];")[0]
    assert 'case "Help":' in menu
    assert "attach:files" in menu
    assert 'case "Attach"' not in menu
    assert "howto:" in menu
    assert "_openAbout" in menu
    assert "__xliiOpenAbout" in menu
    assert 'id="about-card"' in html
    assert "about-version" in html
    assert "about-facts" in html
    assert "about-signs" in html
    assert 'view === "about"' in panes
    assert "Say hello — hello@xlii.computer" in menu
    assert "paintFkeyBar" in js_app
    assert "paintStatusRail" in js_app
    assert "xlii.face.bold" in js_app
    assert "__xliiSetBoldType" in js_app
    assert 'data-bold="1"' in (ASSETS / "css/face.css").read_text()
    assert 'unseen === 1 ? "done"' not in js_app
    assert "jobs_active" in js_app
    assert "applySlots" in (ASSETS / "js/panes.js").read_text()
    panes_js = (ASSETS / "js/panes.js").read_text()
    assert "_localOpen" in panes_js
    assert "Client-only picker" in panes_js
    assert "lastQuickLaunch" not in js_app
    assert "paintPackDoors" not in js_app
    # Talk/lab flip is the only user-facing switch (not Home/Chat/Project doors).
    assert "hud-surface" in html
    assert 'id="flip-mark"' in html
    assert 'img/stamp-steel.png' in html
    assert 'img/stamp-steel@2x.png' in html
    input_js = (ASSETS / "js/input.js").read_text()
    assert 'img/stamp-steel.png' in input_js
    assert 'img/stamp-steel-mirror.png' in input_js
    assert "FLIP_MARK" in input_js
    assert 'img/mojo-talk.png' not in html
    assert 'img/mojo-lab.png' not in input_js
    assert "persona — talk vs lab" in (ASSETS / "js/app.js").read_text()
    assert "?/$" not in (ASSETS / "js/app.js").read_text()
    assert "talk — click for lab" in input_js
    assert "lab — click for talk" in input_js
    assert 'id="flip-talk"' not in html
    assert 'id="flip-lab"' not in html
    assert 'id="hud-meter"' in html
    assert 'id="hud-session"' in html
    assert html.index('id="statusbar"') < html.index('id="hud-meter"')
    assert html.index('id="hud-meter"') < html.index('id="conn"')
    assert 'id="flip-name"' in html
    assert "dataset.overlay" in (ASSETS / "js/input.js").read_text()
    assert "exit_overlay" in (ASSETS / "js/input.js").read_text()
    assert "overlayMark" in (ASSETS / "js/input.js").read_text()
    assert "howto" in (ASSETS / "js/overlay_marks.js").read_text()
    assert "#flip[data-overlay]" in (ASSETS / "css/face.css").read_text()
    assert "#flip[data-overlay] .flip-pair { display: none; }" in (
        ASSETS / "css/face.css"
    ).read_text()
    assert "width: 40px" in (ASSETS / "css/face.css").read_text()
    css = (ASSETS / "css/face.css").read_text()
    assert 'data-kind="slash"' in css
    assert 'data-kind="shell"' in css
    assert 'data-level="mode"' in css
    assert "_looksMarkdown" in (ASSETS / "js/transcript.js").read_text()
    assert "  sync(" in (ASSETS / "js/transcript.js").read_text()
    assert "stream_sync" in (ASSETS / "js/app.js").read_text()
    assert "tailnet_glass" in (ASSETS / "js/app.js").read_text()
    assert "lockEl.hidden = !tailnetGlass" in (ASSETS / "js/app.js").read_text()
    assert "_parkMark" not in (ASSETS / "js/panes.js").read_text()
    assert 'id="stream-mark"' not in (ASSETS / "index.html").read_text()
    assert "mojo-flip.webm" not in html
    assert "<video" not in html
    assert "bindStreamMark" not in (ASSETS / "js/input.js").read_text()
    css = (ASSETS / "css/face.css").read_text()
    assert "stamp-mojo-steel" not in css
    assert "stamp-xlii-steel" not in css
    assert 'url("../img/mojo-mark.png")' not in css
    assert 'url("../img/xlii-mark.png")' not in css
    menu = (ASSETS / "js/menubar.js").read_text()
    assert '_paintRootTitle' in menu
    assert 'btn.textContent = posture === "chat" ? "Mojo" : "Xlii"' in menu
    assert "position: absolute" in (ASSETS / "css/face.css").read_text()
    assert "stream_peek" in (ASSETS / "js/app.js").read_text()
    assert "focus_slot" in (ASSETS / "js/panes.js").read_text()
    assert "export function isStreamView" in (ASSETS / "js/panes.js").read_text()
    assert 'id="transcript-peek"' in html
    assert "dual-stream" in (ASSETS / "css/face.css").read_text()
    assert "#transcript-peek" in (ASSETS / "css/face.css").read_text()
    assert ".slot-chrome #hud-project" in (ASSETS / "css/face.css").read_text()
    assert "SURFACE_LABEL" not in (ASSETS / "js/app.js").read_text()
    assert "door:home" in (ASSETS / "js/menubar.js").read_text()
    assert "proj:collection" in (ASSETS / "js/menubar.js").read_text()
    assert "proj:mc" not in (ASSETS / "js/menubar.js").read_text()
    assert "tools:term" in (ASSETS / "js/menubar.js").read_text()
    assert "open_terminal" in (ASSETS / "js/menubar.js").read_text()
    menu_tools = (ASSETS / "js/menubar.js").read_text()
    assert menu_tools.index("tools:term") < menu_tools.index("tools:browser")
    assert 'label: `${tick(this._isHome)}Home Stream`' in (ASSETS / "js/menubar.js").read_text()
    assert 'id: "pane:home"' in (ASSETS / "js/menubar.js").read_text()
    assert "Home Hub" in (ASSETS / "js/menubar.js").read_text()
    assert "go_home" in (ASSETS / "js/menubar.js").read_text()
    assert 'text: "/cls"' in (ASSETS / "js/menubar.js").read_text()
    assert "no slash in talk" not in (ASSETS / "js/input.js").read_text()
    assert 'token === "cls"' in (ASSETS / "js/input.js").read_text()
    assert "clear_transcript" in (ASSETS / "js/app.js").read_text()
    assert "set_face_fkeys" in (ASSETS / "js/app.js").read_text()
    assert "set_face_bold" in (ASSETS / "js/app.js").read_text()
    assert "set_face_skin" in (ASSETS / "js/skins.js").read_text()
    assert "__xliiApplyFaceSkin" in (ASSETS / "js/skins.js").read_text()
    assert "ev.face_skin" in (ASSETS / "js/app.js").read_text()
    assert "hud-providers" not in html
    assert "hudProviders" not in (ASSETS / "js/app.js").read_text()
    assert "Talk" in (ASSETS / "js/menubar.js").read_text()
    assert "fabric_sync_projects" in (ASSETS / "js/menubar.js").read_text()
    assert "fabric_new" in (ASSETS / "js/menubar.js").read_text()
    assert "proj:remote:" in (ASSETS / "js/menubar.js").read_text()
    assert "flip-mark" in html
    assert "feed-view-copy" in (ASSETS / "js/transcript.js").read_text()
    # Home hides project pill (scratch is ~, not a named project)
    app_js = (ASSETS / "js/app.js").read_text()
    assert "isHome" in app_js or "scratch/" in app_js
    assert "hudProject.hidden" in app_js
    assert "window.__xliiRequestExit" in app_js
    assert 'wire.send({ type: "input", text: "/exit" })' in app_js
    assert "prepare_close" in app_js
    assert 'exitPhase = "ended"' in app_js
    assert 'exitPhase === "exiting"' in app_js
    # Busy/agent errors must not force-close while graceful exit is in flight.
    err_idx = app_js.index('case "error":')
    session_end_idx = app_js.index('case "session_end":')
    error_block = app_js[err_idx:err_idx + 500]
    assert "closeHostWindow()" not in error_block
    assert "closeHostWindow()" in app_js[session_end_idx:session_end_idx + 800]
    assert "_requestExit(win)" in (ASSETS / "js/menubar.js").read_text()
    # Exit is a window verb, not a slash command — available in talk and lab.
    menu_js = (ASSETS / "js/menubar.js").read_text()
    assert '{ id: "xlii:exit"' in menu_js
    assert "posture === \"code\" && !busy" not in menu_js
    input_js = (ASSETS / "js/input.js").read_text()
    assert r"/^\/(?:exit|quit)(?:\s|$)/i" in input_js
    assert 'token === "exit"' in input_js
    assert 'token === "quit"' in input_js


def test_skin_contract_builtins_pixel_equivalent():
    """K1: compiled skins stay color-only; image slots none; no UA scrollbar restyle.

    Exception: one phone-gated title-row scrollbar hide for horizontal scroll
    (Firefox uses scrollbar-width: none; WebKit needs the pseudo-element).
    Hide lives on .menu-strip, not #menubar — overflow on #menubar clips menus."""
    css = (ASSETS / "css/face.css").read_text()
    assert "--bg: #101014;" in css
    assert "--chrome-menubar-img: none;" in css
    assert "--chrome-btn-hover-img: none;" in css
    assert "--chrome-inputbar-img: none;" in css
    assert "--chrome-win-img: none;" in css
    assert "--texture-bg: none;" in css
    assert "--slice-menubar: none;" in css
    assert "--font-ui:" in css
    phone_strip_scrollbar = (
        'html[data-view="phone"] #menubar .menu-strip::-webkit-scrollbar { display: none; }'
    )
    assert css.count("::-webkit-scrollbar") == 1
    assert phone_strip_scrollbar in css
    js = (ASSETS / "js/skins.js").read_text()
    assert "/skins/catalog" in js
    assert "xlii-pack-css" in js
    assert "make your own" in js
    assert "--chrome-menubar-img" in js
    assert "--chrome-btn-active-img" in js
    assert "--chrome-inputbar-img" in js
    assert "--font-ui" in js
    assert "--slice-btn" in js


def test_phone_glass_css_gated_on_data_view():
    """Desk stays pixel-identical: phone layout rules require data-view=phone."""
    html = (ASSETS / "index.html").read_text()
    assert "viewport-fit=cover" in html
    assert "interactive-widget=resizes-content" in html
    assert 'rel="manifest"' in html
    js = (ASSETS / "js/app.js").read_text()
    assert "requestPhoneFullscreen" in js
    assert "phoneFullscreenFromGesture" in js
    assert "visualViewport" in js
    assert "phoneVisualViewport" in js
    assert "--vvt" in js
    assert '"display": "fullscreen"' in (ASSETS / "manifest.webmanifest").read_text()
    css = (ASSETS / "css/face.css").read_text()
    assert "html[data-view=\"phone\"]" in css
    assert "#thumbbar" in css
    assert "html[data-view=\"phone\"] #glass," in css
    assert "html[data-view=\"phone\"] #glass-lock," in css
    assert "html[data-view=\"phone\"] #menubar {" in css
    assert "html[data-view=\"phone\"] #menubar .menu-strip {" in css
    assert "html[data-view=\"phone\"] .win-controls," in css
    assert 'id="voice"' in html
    assert 'id="input-actions"' in html
    assert "html[data-view=\"phone\"] #input-actions" in css
    assert "setPhoneCompose" in js
    assert "dataset.compose" in js
    assert "installPhoneVoice" in js
    assert "SpeechRecognition" in js
    assert 'html[data-view="phone"][data-compose="1"] #input' in css
    assert "position: fixed" not in css.split('data-compose="1"')[1][:400]
    assert "100dvh" in css
    # No unscoped phone layout that would restyle the desk.
    for needle in ("100dvh", "#thumbbar button", "order: 3", "flex: 1 1 0"):
        idx = css.find(needle)
        assert idx > 0, needle
        window = css[max(0, idx - 500):idx]
        assert "data-view=\"phone\"" in window, needle


def _css_rule_body(css: str, selector: str) -> str:
    idx = css.find(selector)
    assert idx >= 0, selector
    start = css.find("{", idx)
    end = css.find("}", start)
    assert start > 0 and end > start, selector
    return css[start : end + 1]


def test_phone_menubar_overflow_does_not_clip_dropdowns():
    """Phone titles scroll; #menubar itself must not clip .menu-drop.

    overflow-x:auto + overflow-y:hidden on the same box that owns the
    absolutely positioned dropdown clips File/Edit/… so taps look dead.
    """
    css = (ASSETS / "css/face.css").read_text()
    bar = _css_rule_body(css, 'html[data-view="phone"] #menubar {')
    assert "overflow-x: auto" not in bar
    assert "overflow-y: hidden" not in bar
    assert "overflow: visible" in bar
    assert "z-index: 40" in bar
    strip = _css_rule_body(css, 'html[data-view="phone"] #menubar .menu-strip {')
    assert "overflow-x: auto" in strip
    assert "overflow-y: hidden" in strip
    assert ".menu-strip { display: contents; }" in css
    menu = (ASSETS / "js/menubar.js").read_text()
    assert 'className = "menu-strip"' in menu
    assert "this._strip.appendChild(b)" in menu
    assert "this.root.appendChild(drop)" in menu
    assert "_placeDrop" in menu
    assert "getBoundingClientRect" in menu
    # Desk host still closes on outside click via #menubar (drop stays in root).
    assert 'e.target.closest("#menubar")' in menu


def test_phone_fullscreen_gesture_skips_menus():
    """Chrome needs a real pointer activation; menu taps must not take it.

    #471 moved requestFullscreen to input/mic focus. Chrome rejects that
    (focus is not a user activation); Firefox is looser. Restore a bubble
    pointerdown — never capture-phase on every tap, and never on menu chrome.
    """
    js = (ASSETS / "js/app.js").read_text()
    arm = js.split("function armPhoneChrome()", 1)[1].split("function installPhoneVoice", 1)[0]
    assert "phoneFullscreenFromGesture" in arm
    assert 'addEventListener("pointerdown", phoneFullscreenFromGesture)' in arm
    assert "capture: true" not in arm
    skip = js.split("const PHONE_FS_SKIP", 1)[1].split(";", 1)[0]
    for needle in ("#menubar", ".menu-drop", ".menu-title", ".menu-item", "#inputbar"):
        assert needle in skip, needle
    # focus/mic paths stay (Firefox + mic gesture); they are not the Chrome path.
    assert "requestPhoneFullscreen()" in js.split("const onKb", 1)[1][:200]
    voice = js.split("function installPhoneVoice", 1)[1]
    assert "requestPhoneFullscreen()" in voice.split("btn.addEventListener(\"click\"", 1)[1][:200]


def test_phone_history_guard_holds_first_back():
    """Android Back from live public Face must not reveal the spent pair page.

    First Back re-pushes Face while the sitting is live (#468 linger / WS
    reattach). A second Back in a short window, or menu Exit, is the escape.
    Desk / Tauri without data-view=phone must not install the trap.
    """
    guard = (ASSETS / "js/history_guard.js").read_text()
    assert "hist.replaceState" in guard or "history.replaceState" in guard
    assert "hist.pushState" in guard or "history.pushState" in guard
    assert "popstate" in guard
    assert 'dataset.view === "phone"' in guard
    assert "shouldHoldBack" in guard
    assert "leaveArmed" in guard
    assert "sittingLive" in guard
    assert "xlii-face-leave" in guard
    # Escape: second Back shortly after the first is allowed to leave.
    assert "LEAVE_WINDOW" in guard or "leaveWindowMs" in guard
    # Do not fight #472 fullscreen — the guard only touches history.
    assert "requestFullscreen" not in guard
    assert "webkitRequestFullscreen" not in guard

    js = (ASSETS / "js/app.js").read_text()
    assert "history_guard" in js
    assert "installFaceHistoryGuard" in js
    arm = js.split("function armPhoneChrome()", 1)[1]
    assert "installFaceHistoryGuard" in arm.split("function ", 1)[0]
    # Menu Exit remains the intentional quit; do not hook it through history.back.
    assert "history.back" not in js
    menubar = (ASSETS / "js/menubar.js").read_text()
    assert 'case "xlii:exit"' in menubar
    assert 'text: "/exit"' in menubar


def test_history_guard_hold_back_decisions(tmp_path):
    """Executable pin: first Back holds, second Back in the window leaves."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    script = tmp_path / "hold.mjs"
    script.write_text(
        "import { shouldHoldBack, installFaceHistoryGuard, "
        "FACE_LEAVE_FLAG, FACE_LEAVE_WINDOW_MS } from "
        + json.dumps(str((ASSETS / "js/history_guard.js").resolve()))
        + """;
if (!shouldHoldBack({ sittingLive: true, leaveArmed: false })) process.exit(2);
if (shouldHoldBack({ sittingLive: true, leaveArmed: true })) process.exit(3);
if (shouldHoldBack({ sittingLive: false, leaveArmed: false })) process.exit(4);
const pushes = [];
const hist = {
  state: null,
  replaceState(s) { this.state = s; },
  pushState(s) { this.state = s; pushes.push(s); },
};
let sitting = true;
let t = 0;
const store = { data: {}, setItem(k, v) { this.data[k] = v; } };
const g = installFaceHistoryGuard({
  history: hist,
  addEventListener() {},
  locationHref: () => "https://xlii-code.dev/face/",
  isPhone: () => true,
  isSittingLive: () => sitting,
  leaveWindowMs: FACE_LEAVE_WINDOW_MS,
  now: () => t,
  sessionStorage: store,
});
if (!g.installed) process.exit(5);
if (pushes.length !== 1) process.exit(6);
g.onPop();
if (pushes.length !== 2) process.exit(7);
t = 500;
g.onPop();
if (pushes.length !== 2) process.exit(8);
if (store.data[FACE_LEAVE_FLAG] !== "1") process.exit(12);
sitting = true;
t = 3000;
g.onPop();
if (pushes.length !== 3) process.exit(9);
sitting = false;
t = 6000;
g.onPop();
if (pushes.length !== 3) process.exit(10);
const desk = installFaceHistoryGuard({
  history: hist,
  addEventListener() {},
  isPhone: () => false,
});
if (desk.installed) process.exit(11);
console.log("ok");
""",
        encoding="utf-8",
    )
    r = subprocess.run([node, str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
