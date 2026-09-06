# PyInstaller spec — T0 frozen-sidecar spike (proposals/tauri-shell.md).
#
# Freezes HEADLESS CORE xlii only (no [tui]/[daemon]/[files] extras —
# tauri-shell decision #2). Build via lab/tauri-shell/sidecar/build.sh,
# which creates the venv this spec expects and runs pyinstaller from a
# scratch directory so build/ and dist/ never land in the repo.
#
# Data-file doctrine: xlii loads its bundled assets (prompts/, help/,
# stock_plugins/, stock_roles/, stock_skills/, stock_personas/) via
# Path(__file__)-relative lookups. PyInstaller points frozen modules'
# __file__ under sys._MEIPASS, so collect_data_files('xlii') mirroring
# the installed package layout makes those lookups work unmodified.

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("xlii")

hiddenimports = (
    # keyring discovers backends through entry points, which freezing
    # breaks; pull the backend modules in explicitly.
    collect_submodules("keyring.backends")
)

a = Analysis(
    ["sidecar_entry.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Extras deliberately not in the sidecar (decision #2) — keep the
        # freeze honest if the build venv accidentally has them.
        "textual",
        "textual_serve",
        "textual_image",
        "slixmpp",
        "tkinter",
        "pypdf",
        "paramiko",
        "smbprotocol",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="xlii-sidecar",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

# onedir, not onefile: faster cold start (no self-extraction) and easier
# to inspect while the spike iterates. Revisit at T3 — Tauri's externalBin
# prefers a single file, but onedir can ship as a resource dir instead.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="xlii-sidecar",
)
