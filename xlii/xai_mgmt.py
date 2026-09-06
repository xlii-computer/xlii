"""Read-only xAI Management + Billing API client — the account-hub data layer.

Thin GETs (one POST for usage) over ``management-api.x.ai`` for account status,
API-key inventory, and billing (prepaid balance / postpaid spend + limits).

READ-ONLY by design. Nothing here mutates the account: key CRUD lives in
``bootstrap.py``, and anything that *spends money* (prepaid top-up) or changes
limits/payment is deliberately absent — those must go through an explicit,
human-confirmed path and must never be reachable by the agent. See
``proposals/xai-account.md`` and the verified endpoint map.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

MANAGEMENT_HOST = "https://management-api.x.ai"


class AccountError(RuntimeError):
    """A management/billing request failed (network or HTTP >= 400)."""


def _request(method: str, path: str, mgmt_key: str, *, json_body: Optional[dict] = None) -> Any:
    try:
        resp = httpx.request(
            method,
            f"{MANAGEMENT_HOST}{path}",
            headers={"Authorization": f"Bearer {mgmt_key}", "Accept": "application/json"},
            json=json_body,
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        raise AccountError(f"request failed: {e}") from e
    if resp.status_code >= 400:
        raise AccountError(f"{method} {path} → {resp.status_code}: {resp.text[:200]}")
    if not resp.content:
        return None
    try:
        return resp.json()
    except ValueError:
        return resp.text


def _get(path: str, mgmt_key: str) -> Any:
    return _request("GET", path, mgmt_key)


def money_usd(val: Any) -> Optional[float]:
    """Parse a money value to dollars. The API returns USD *cents* as a string,
    bare or wrapped: ``{"val": "500000"}`` → 5000.0. Returns None if unparseable."""
    if isinstance(val, dict):
        if "val" in val:
            val = val.get("val")
        elif "amount" in val:
            return money_usd(val.get("amount"))
        elif "total" in val:
            return money_usd(val.get("total"))
        else:
            return None
    try:
        return int(val) / 100.0
    except (TypeError, ValueError):
        return None


def pick_usd(data: Any, *keys: str) -> Optional[float]:
    """First parseable money field among *keys* (nested dicts ok)."""
    if not isinstance(data, dict):
        return None
    for key in keys:
        got = money_usd(data.get(key))
        if got is not None:
            return got
    return None


def prepaid_total_usd(mgmt_key: str, team_id: str) -> Optional[float]:
    """Prepaid credits on the team. Live probe: body field is ``total`` (cents)."""
    data = prepaid_balance(mgmt_key, team_id)
    return pick_usd(data, "total", "balance", "amount", "available", "remaining")


def invoice_preview_usd(mgmt_key: str, team_id: str) -> Optional[float]:
    """Current postpaid cycle total from invoice preview."""
    data = invoice_preview(mgmt_key, team_id)
    got = pick_usd(data, "total", "amountDue", "amount", "totalUsd", "usd")
    if got is not None:
        return got
    totals = data.get("totals") if isinstance(data, dict) else None
    return pick_usd(totals or {}, "total", "amountDue", "amount", "usd")


# --- account / keys (management-api.x.ai/auth) ----------------------------- #

def list_teams(mgmt_key: str) -> list[dict]:
    data = _get("/auth/teams", mgmt_key)
    return data.get("teams", []) if isinstance(data, dict) else []


def resolve_active_team(mgmt_key: str, preferred: Optional[str] = None) -> Optional[str]:
    """The team this management key actually controls.

    ``preferred`` (cfg.team_id) wins if set. Otherwise: the single team, or — when
    there are several — the one whose api-keys endpoint accepts this key (keys are
    team-bound, so the others 403); the first team as a last resort.
    """
    teams = list_teams(mgmt_key)
    if not teams:
        return None
    if preferred:
        return preferred
    if len(teams) == 1:
        return teams[0].get("teamId")
    for t in teams:
        tid = t.get("teamId")
        try:
            list_keys(mgmt_key, tid)
            return tid
        except AccountError:
            continue
    return teams[0].get("teamId")


def team_status(mgmt_key: str, team_id: str) -> dict:
    """The team object (tier, ACLs, rate-limit overrides, MFA, ZDR, blocked…)."""
    for t in list_teams(mgmt_key):
        if t.get("teamId") == team_id:
            return t
    return {}


def list_keys(mgmt_key: str, team_id: str) -> list[dict]:
    """API-key inventory for the team (name, qps/qpm, aclStrings, disabled, expiry).

    Note: the management key is bound to one team; calling this with a different
    team_id returns 403 (raised as AccountError)."""
    data = _get(f"/auth/teams/{team_id}/api-keys", mgmt_key)
    if isinstance(data, dict):
        return data.get("apiKeys") or data.get("keys") or []
    return data if isinstance(data, list) else []


# --- billing (management-api.x.ai/v1/billing) ------------------------------ #

def spending_limits(mgmt_key: str, team_id: str) -> dict:
    data = _get(f"/v1/billing/teams/{team_id}/postpaid/spending-limits", mgmt_key)
    return (data.get("spendingLimits") if isinstance(data, dict) else None) or {}


def invoice_preview(mgmt_key: str, team_id: str) -> dict:
    """Current-cycle charges (line items by model, totals)."""
    data = _get(f"/v1/billing/teams/{team_id}/postpaid/invoice/preview", mgmt_key)
    return data if isinstance(data, dict) else {}


def list_invoices(mgmt_key: str, team_id: str) -> list[dict]:
    """Past invoices: invoice_number, createTime, invoiceStatus, total."""
    data = _get(f"/v1/billing/teams/{team_id}/invoices", mgmt_key)
    return (data.get("invoices") if isinstance(data, dict) else None) or []


def transactions(mgmt_key: str, team_id: str) -> list[dict]:
    """Prepaid balance changes: top-ups / spends / refunds
    (changeOrigin, amount, topupStatus, createTime)."""
    return prepaid_balance(mgmt_key, team_id).get("changes") or []


def prepaid_balance(mgmt_key: str, team_id: str) -> dict:
    data = _get(f"/v1/billing/teams/{team_id}/prepaid/balance", mgmt_key)
    return data if isinstance(data, dict) else {}


def usage_by_description(
    mgmt_key: str,
    team_id: str,
    *,
    start: str,
    end: str,
    time_unit: str = "TIME_UNIT_MONTH",
) -> list[tuple[str, float]]:
    """USD spend grouped by line-item description (model / feature) over [start, end].

    Returns ``[(label, usd), …]`` sorted by spend descending. ``start``/``end`` are
    ``"YYYY-MM-DD HH:MM:SS"`` strings. NOTE: the usage endpoint reports values
    already in **dollars** (unlike limits/balance, which are cents)."""
    body = {
        "analyticsRequest": {
            "timeRange": {"startTime": start, "endTime": end, "timezone": "Etc/GMT"},
            "timeUnit": time_unit,
            "values": [{"name": "usd", "aggregation": "AGGREGATION_SUM"}],
            "groupBy": ["description"],
            "filters": [],
        }
    }
    data = _request("POST", f"/v1/billing/teams/{team_id}/usage", mgmt_key, json_body=body)
    out: list[tuple[str, float]] = []
    for series in (data.get("timeSeries", []) if isinstance(data, dict) else []):
        label = (series.get("groupLabels") or series.get("group") or ["—"])[0]
        total = 0.0
        for dp in series.get("dataPoints", []):
            vals = dp.get("values") or []
            if vals:
                try:
                    total += float(vals[0])
                except (TypeError, ValueError):
                    # A non-numeric datapoint contributes nothing to the total.
                    pass
        out.append((str(label), total))
    out.sort(key=lambda x: x[1], reverse=True)
    return out
