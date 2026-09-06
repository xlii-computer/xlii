# lab/tauri-shell — off-spine spikes for proposals/tauri-shell.md

Per proposals rule #4, nothing here is packaged (setuptools includes
`xlii*` only) or CI-gated. Findings are written back into the proposal,
not left here.

- `sidecar/` — **T0**: freeze headless core xlii with PyInstaller.
  `./sidecar/build.sh [scratch-dir]` builds into a scratch directory
  (never the repo) and leaves `dist/xlii-sidecar/xlii-sidecar`.
  First run: 2026-07-12, findings in the proposal's T0 appendix.
- `app/` — **GRADUATED to `desktop/`** (2026-07-24, the tauri-face program):
  the T1 scaffold became the real desktop face — dev-mode sidecar
  (`xlii serve --face --handshake` from PATH), webview = `xlii/face_assets`.
  The stub sidecar/page were spike-only and are gone; `desktop/README.md`
  has the build + manual gate. T1 findings remain in the proposal's appendix.
