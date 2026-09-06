# xlii-desktop — the desktop face

The Tauri v2 shell around the face: an undecorated native window rendering
`xlii/face_assets` (the same page the browser gets) over a dev-mode sidecar it
spawns itself — `xlii serve --face --handshake`, resolved from PATH, cwd =
wherever the app was launched. `xlii code --tauri` positions that at a project
root and execs the installed app. The OS title bar is off; the face menubar
is the title bar (drag, double-click maximize, min/max/close). Window edges
still resize.

Graduated from `lab/tauri-shell/app/` (the T1 spike; gate findings in
`proposals/tauri-shell.md`). The host stays logic-free: spawn → read the
one-line JSON handshake from stdout → hand `{port, token}` to the webview
(`handshake` command; `spawn_error` when xlii isn't on PATH) → kill the child
on exit. Stdin-close is the sidecar's kill backstop, so a SIGKILL'd host
leaves no orphan.

## Build

Rust is deliberately outside the Python CI gates. Linux deps + build:

```bash
sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev patchelf
cargo install tauri-cli --locked
cd desktop/src-tauri
cargo tauri build --bundles deb     # → target/release/bundle/deb/*.deb
sudo dpkg -i target/release/bundle/deb/xlii-desktop_*.deb
```

Then from any xlii project: `xlii code --tauri` (or launch `xlii-desktop`
directly — the sidecar resolves the most-recent project from $HOME).

Dev loop without bundling: `cargo tauri dev` in `src-tauri/` (frontendDist
points straight at `../../xlii/face_assets`; edit assets, reload the window).

## Manual gate (per release)

1. `xlii code --tauri` in a project → window opens, lands in `[M]` (iXaac),
   token round-trip works, `[$]` flips to code.
2. Quit the window → `pgrep -f "serve --face"` is empty (no orphan).
3. `xlii-desktop` with xlii NOT on PATH → the page shows the dry
   could-not-start message, no crash.

The frozen-binary sidecar (PyInstaller `externalBin`, signed installers,
updater) is the DISTRIBUTION story — deferred; the proven freeze recipe lives
in `lab/tauri-shell/sidecar/` and the T0 appendix of the proposal.
