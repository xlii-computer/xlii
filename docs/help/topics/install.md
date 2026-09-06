# Install & first-time setup

## Prerequisites

- Python **3.11+** (Linux or macOS).
- An **xAI management API key** (`XAI_MANAGEMENT_API_KEY` in your shell — xlii
  never writes this to disk).
- Optional: Tailscale + XMPP only if you want the multi-machine fabric later.

## Install xlii

```bash
git clone <your-repo-url> xlii
cd xlii
python3 -m venv venv
./venv/bin/pip install -e .
```

Optional PATH symlink:

```bash
sudo ln -s "$(pwd)/venv/bin/xlii" /usr/local/bin/xlii
```

Optional extras (only if you need them):

```bash
pip install -e ".[mcp]"    # Grok Build / DeepContexts MCP bridge
pip install -e ".[tui]"    # full-screen TUI (`xlii code --tui`)
pip install -e ".[dev]"    # pytest + ruff + Textual ([dev] already pulls [tui])
```

TUI / Textual tests (dozens of modules under `tests/` that `importorskip("textual")`)
need the `[tui]` extra. The `[dev]` extra already depends on `xlii[tui]`, so
`pip install -e ".[dev]"` is enough. `pip install -e ".[dev,tui]"` is equivalent.

## Health check

```bash
xlii doctor
xlii doctor --online   # also probes Collection reachability
```

Fix anything `doctor` prints before continuing.

## First-time provisioning

Export the management key, then run setup once:

```bash
export XAI_MANAGEMENT_API_KEY=xai-...
xlii setup
```

`xlii setup` writes `~/.config/xlii/config.json` (mode 600), caches `team_id`,
creates **1 primary + 8 worker** chat keys (tune with `--workers N`), sets 180-day
expiry, and auto-detects orchestrator/worker models. Re-run safely; use `--force`
to re-provision.

Verify:

```bash
xlii status
```

You should see: management key found, team_id, chat keys in the pool, models set.

If model auto-detection fails, set manually:

```bash
xlii models set --orchestrator <name> --worker <name>
```

## Optional pricing (for `/cost`)

Add a `pricing` map to `~/.config/xlii/config.json` with USD-per-million-token
rates from your xAI dashboard. Without it, token counts still show; cost is omitted.

## Next step

Run `/howto first-session` or `xlii init` + `xlii code` in your project directory.
