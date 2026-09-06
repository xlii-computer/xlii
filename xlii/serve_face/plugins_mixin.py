"""Plugin catalog, plugin-call, and plugin-maker save.

Mixin extracted from :mod:`xlii.serve_face.server`.
"""
from __future__ import annotations

import threading
from typing import Any

from xlii.turn_events import (
    PluginActionEntry,
    PluginCatalog,
    PluginEntry,
    PluginParamEntry,
)
from xlii.ws_protocol import serialize_event


class FacePluginsMixin:
    """Plugin catalog, plugin-call, and plugin-maker save."""

    def write_plugin(self, spec: dict) -> bool:
        """Plugin maker Save — write ``~/.config/xlii/plugins/<id>.md``."""
        from xlii.plugin_make_form import PluginSubscribeGated, write_plugin_spec

        try:
            xli = getattr(getattr(self.state, "project", None), "xli_dir", None)
            path = write_plugin_spec(spec or {}, xli_dir=xli, state=self.state)
        except PluginSubscribeGated as e:
            # The save succeeded; only the subscribe was refused by the
            # high-risk gate (same one plugin_subscribe enforces).
            self.send({"type": "meta_message", "level": "warn",
                       "text": f"wrote {e.path} — {e}"})
            try:
                self.deck._pluginmake_address = f"pluginmake://{e.path.stem}"
                self.deck.send_snapshot()
            except Exception:
                # Best-effort UI sync — the save already succeeded.
                pass
            return True
        except (ValueError, OSError) as e:
            self.send({"type": "meta_message", "level": "warn", "text": str(e)})
            return False
        except Exception as e:
            self.send({"type": "meta_message", "level": "error",
                       "text": f"could not write plugin: {type(e).__name__}: {e}"})
            return False
        name = str((spec or {}).get("id") or path.stem)
        if (spec or {}).get("subscribe"):
            extra = " · subscribed" if xli is not None else " · not subscribed (no project)"
        else:
            extra = ""
        self.send({"type": "meta_message", "level": "success",
                   "text": f"wrote {path}{extra}"})
        try:
            self.deck._pluginmake_address = f"pluginmake://{name}"
            self.deck.send_snapshot()
        except Exception:
            # Best-effort UI sync — the save already succeeded.
            pass
        return True

    def _bind_plugin_form_hook(self) -> None:
        """Agent ``plugin_call`` opens the face form instead of asking in chat."""
        try:
            sess = getattr(getattr(self.state, "agent", None), "session", None)
            if sess is not None:
                sess.open_plugin_form = self._open_plugin_form
        except Exception:  # noqa: BLE001
            pass

    def _open_plugin_form(self, spec: dict[str, Any]) -> None:
        plugin = str((spec or {}).get("plugin") or "").strip()
        action = str((spec or {}).get("action") or "").strip()
        if not plugin or not action:
            return
        seed: dict[str, Any] = {}
        raw_seed = (spec or {}).get("seed")
        if isinstance(raw_seed, dict):
            seed = dict(raw_seed)
        for field in (spec or {}).get("fields") or []:
            if not isinstance(field, dict):
                continue
            if field.get("kind") == "secret":
                continue
            name = field.get("name")
            val = field.get("value")
            if name and val:
                seed[str(name)] = val
        if getattr(self, "deck", None) is not None:
            self.deck.open_plugin_form(plugin, action, seed=seed)
        self.send({
            "type": "meta_message",
            "level": "info",
            "text": f"{plugin}.{action} — fill the form. Secrets stay off the agent.",
        })

    def _note_plugin_form(
        self, plugin_id: str, action_id: str,
        params: dict[str, Any], result: Any,
    ) -> None:
        """Keep non-secret seed; tell the open form whether the run landed."""
        try:
            p = __import__("xlii.plugin", fromlist=["Plugin"]).Plugin(id=plugin_id)
            act = p.manifest().get_action(action_id) if p.exists() and p.manifest() else None
        except Exception:
            act = None
        seed = {}
        if act is not None:
            secret = {s.name for s in act.params.values() if s.secret}
            seed = {
                k: v for k, v in (params or {}).items()
                if k not in secret and v is not None and str(v).strip()
            }
        elif params:
            seed = {k: v for k, v in params.items() if v is not None}
        if getattr(self, "_deck", None) is not None:
            self._deck._form_seed = seed
        text = (getattr(result, "user_text", None) or getattr(result, "error", None) or "")
        text = str(text).strip()
        from xlii.plugin_form import (
            looks_like_secret_payload,
            redact_plugin_text,
            stored_secret_wire_receipt,
            withhold_stored_secret_body,
        )

        stored = list(getattr(result, "stored", None) or [])
        raw_body = str(getattr(result, "body", None) or "")
        if looks_like_secret_payload(text) or withhold_stored_secret_body(
            text, stored=stored, raw_body=raw_body,
        ):
            # Credential-shaped bodies (login JSON, JWTs) must not ride the WS
            # or the iframe — the form only needs landed/failed.
            text = stored_secret_wire_receipt(stored) if stored else ""
        else:
            # Redact before truncating: a cut breaks JSON and defeats redaction.
            text = redact_plugin_text(text)
            if len(text) > 400:
                text = text[:400] + "…"
        if not text:
            text = "done" if getattr(result, "ok", False) else "failed"
        ok = bool(getattr(result, "ok", False))
        self.send({
            "type": "plugin_form_status",
            "ok": ok,
            "close": ok,
            "plugin": plugin_id,
            "action": action_id,
            "text": text,
        })
        if ok and getattr(self, "_deck", None) is not None:
            try:
                self._deck.close_plugin_form()
            except Exception:  # noqa: BLE001 — receipt already on the stream
                pass

    def plugin_catalog(self) -> dict[str, Any]:
        """Installed plugins + actions for the face **Plugins** menu (M2.1).

        Chat power path: menu / ``plugin_call`` wire — not slash. Best-effort:
        a broken plugin file is skipped rather than killing the catalog."""
        import os

        from xlii.plugin import list_plugins, load_subscriptions

        project = getattr(self.state, "project", None)
        xli = getattr(project, "xli_dir", None) if project is not None else None
        try:
            subs = set(load_subscriptions(xli)) if xli is not None else set()
        except Exception:
            subs = set()
        entries: list[PluginEntry] = []
        for p in list_plugins():
            try:
                effect, trust = p.effect_trust()
                actions: list[PluginActionEntry] = []
                m = p.manifest()
                if m is not None:
                    for a in m.actions:
                        if a.is_exec:
                            continue  # face runner is HTTP-only for now
                        params = [
                            PluginParamEntry(
                                name=spec.name,
                                description=spec.description or "",
                                required=bool(spec.required),
                                default="" if spec.default is None else str(spec.default),
                                form=bool(spec.form or spec.secret),
                                secret=bool(spec.secret),
                            )
                            for spec in a.params.values()
                            if spec.const is None
                        ]
                        actions.append(PluginActionEntry(
                            id=a.id, description=a.description or "", params=params))
                auth_vars = p.auth_env_vars()
                ready = all(os.environ.get(v, "").strip() for v in auth_vars) if auth_vars else True
                entries.append(PluginEntry(
                    id=p.id, name=p.name() or p.id,
                    description=(p.description() or "").strip(),
                    effect=effect, trust=trust,
                    subscribed=p.id in subs, ready=ready, actions=actions))
            except Exception:
                continue
        entries.sort(key=lambda e: (not e.subscribed, e.id))
        return serialize_event(PluginCatalog(plugins=entries))

    def _exec_plugin_call(self, plugin_id: str, action_id: str,
                          params: dict[str, Any]) -> bool:
        """Run one action and emit the result. No busy / turn_done.

        Used inline from the face worker (typed ``/plugin call`` already
        holds ``_busy``) and from the dedicated plugin thread.
        """
        from xlii.plugin import Plugin, load_subscriptions
        from xlii.plugin_call import invoke_action
        from xlii.repl_cmds.knowledge import _plugin_env

        ok = True
        try:
            project = getattr(self.state, "project", None)
            xli = getattr(project, "xli_dir", None) if project is not None else None
            if xli is None:
                self.send({"type": "meta_message",
                           "text": "no project — cannot run plugins", "level": "error"})
                return False
            if plugin_id not in set(load_subscriptions(xli)):
                self.send({"type": "meta_message",
                           "text": f"{plugin_id!r} is not subscribed — "
                                   f"Plugins menu → subscribe, or "
                                   f"/plugin subscribe {plugin_id} in [$]",
                           "level": "warn"})
                return False
            p = Plugin(id=plugin_id)
            if not p.exists():
                self.send({"type": "meta_message",
                           "text": f"plugin {plugin_id!r} missing on disk",
                           "level": "error"})
                return False
            if p.is_high_risk():
                effect, trust = p.effect_trust()
                prompt = f"run {plugin_id}.{action_id}? [{effect} · {trust}]"
                answer = self.confirm.ask(prompt)
                if str(answer).strip().lower() != "y":
                    self.send({"type": "meta_message", "text": "plugin call cancelled",
                               "level": "info"})
                    return False
            try:
                result = invoke_action(
                    plugin_id, p.read_raw(), action_id, params, env=_plugin_env(p))
            except ValueError as e:
                self.send({"type": "meta_message", "text": str(e), "level": "error"})
                return False
            if (not result.ok) and result.error and "missing required" in result.error:
                from xlii.plugin_form import action_needs_form

                try:
                    act = p.manifest().get_action(action_id) if p.manifest() else None
                except Exception:
                    act = None
                if act is not None and action_needs_form(act, params):
                    if getattr(self, "deck", None) is not None:
                        self.deck.open_plugin_form(plugin_id, action_id, seed=params)
                    self.send({
                        "type": "meta_message",
                        "text": f"{plugin_id}.{action_id} — fill the form.",
                        "level": "info",
                    })
                    return False
                self.send({"type": "meta_message", "text": result.error, "level": "error"})
                return False
            status = f"HTTP {result.code}" if result.code else "no response"
            level = "success" if result.ok else "error"
            head = f"{plugin_id}.{action_id}  {status} · {result.mode}"
            self.send({"type": "meta_message", "text": head, "level": level})
            from xlii.plugin_form import (
                looks_like_secret_payload,
                redact_plugin_text,
                stored_secret_wire_receipt,
                withhold_stored_secret_body,
            )

            body = (result.user_text or result.error or "").rstrip()
            self._note_plugin_form(plugin_id, action_id, params, result)
            if body:
                stored = list(result.stored or [])
                truncated = False
                if withhold_stored_secret_body(
                    body, stored=stored, raw_body=result.body or "",
                ):
                    body = stored_secret_wire_receipt(stored)
                else:
                    # Redact before truncating: a cut breaks JSON and defeats redaction.
                    body = redact_plugin_text(body)
                    # Cap a runaway payload so one plugin can't flood the transcript.
                    if len(body) > 12000:
                        body = body[:12000] + "\n… (truncated)"
                        truncated = True
                secrets = bool(stored) or looks_like_secret_payload(result.body or "")
                if result.ok and not secrets:
                    # Schema-aware lists render as markdown — feed card so
                    # links are clickable, not a gray meta dump.
                    self._send_feed_view({
                        "type": "feed_view",
                        "kind": "plugin",
                        "address": f"plugins://{plugin_id}/{action_id}",
                        "title": f"{plugin_id}.{action_id}",
                        "text": body,
                        "truncated": truncated,
                        "lines": body.count("\n") + 1,
                    })
                else:
                    self.send({
                        "type": "meta_message",
                        "text": body,
                        "level": "success" if result.ok else "error",
                    })
        except Exception as e:  # noqa: BLE001 — keep the face serving
            self.send({"type": "meta_message",
                       "text": f"{type(e).__name__}: {e}", "level": "error"})
            ok = False
        return ok

    def _run_plugin_call(self, plugin_id: str, action_id: str,
                         params: dict[str, Any]) -> None:
        """Worker-thread path for client ``plugin_call`` (menu / form)."""
        self._busy.set()
        ok = True
        try:
            ok = self._exec_plugin_call(plugin_id, action_id, params)
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message",
                       "text": f"{type(e).__name__}: {e}", "level": "error"})
            ok = False
        finally:
            self._busy.clear()
            self.send(self.mode_state())
            self.send(self.chrome_state())
            try:
                self.send(self.plugin_catalog())
            except Exception:  # noqa: BLE001
                pass
            self.send({"type": "turn_done", "ok": ok, "exit_code": 0 if ok else 1})

    def _start_plugin_call(self, plugin_id: str, action_id: str,
                           params: dict[str, Any]) -> bool:
        """Start a plugin menu call after synchronously claiming hard-busy.

        The plugin worker clears ``_busy`` in its ``finally`` block. Claiming
        before ``Thread.start`` keeps duplicate menu clicks from launching
        multiple side-effecting HTTP actions before the worker runs.
        """
        if self._busy.is_set():
            return False
        self._busy.set()
        try:
            threading.Thread(
                target=self._run_plugin_call,
                args=(plugin_id, action_id, params),
                name="face-plugin-call", daemon=True,
            ).start()
        except Exception:
            self._busy.clear()
            raise
        return True
