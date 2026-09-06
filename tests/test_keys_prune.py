"""Coverage for `xlii keys prune` candidate selection.

The risky part — *which* server keys get deleted — is the pure
select_prune_candidates(). These lock that the active pool is protected by id,
that a stale duplicate of a re-created label is NOT protected, and that the
--name / --older-than / --include-active filters behave. No network."""

from datetime import datetime, timezone, timedelta

from xlii.bootstrap import active_pool_sets as _active_pool_sets
from xlii.bootstrap import select_prune_candidates as _select_prune_candidates

NOW = datetime(2026, 6, 14, tzinfo=timezone.utc)


def _k(name, kid, days_old=100):
    # the management list endpoint returns ids under camelCase `apiKeyId` — the
    # fixtures must mirror that or active-pool protection isn't really tested
    return {
        "name": name,
        "apiKeyId": kid,
        "createTime": (NOW - timedelta(days=days_old)).isoformat().replace("+00:00", "Z"),
    }


def _names(ks):
    return [k["name"] for k in ks]


def _sel(server, **kw):
    base = dict(active_ids=set(), active_names=set(), name_ok=lambda n: True,
               older_than_days=None, now=NOW, include_active=False)
    base.update(kw)
    return _select_prune_candidates(server, **base)


def test_protects_active_pool_by_id():
    server = [_k("xlii-primary-1", "id-A"), _k("xlii-worker-1", "id-B"), _k("old-test", "id-X")]
    assert _names(_sel(server, active_ids={"id-A", "id-B"})) == ["old-test"]


def test_stale_duplicate_of_recreated_label_is_not_protected():
    # two keys both named xlii-worker-1: the live one (id in pool) and a stale
    # orphan (different id). Only the orphan should be a candidate.
    server = [_k("xlii-worker-1", "id-live"), _k("xlii-worker-1", "id-stale")]
    out = _sel(server, active_ids={"id-live"})
    assert [k["apiKeyId"] for k in out] == ["id-stale"]


def test_name_fallback_protects_idless_local_entries():
    server = [_k("xlii-legacy", "id-1")]
    assert _sel(server, active_names={"xlii-legacy"}) == []


def test_stale_local_id_protects_matching_provisioned_name():
    server = [_k("xlii-worker-1", "id-live")]
    active_ids, active_names = _active_pool_sets(
        [{"label": "worker-1", "api_key_id": "id-stale"}],
        server,
    )

    assert _sel(server, active_ids=active_ids, active_names=active_names) == []


def test_present_local_id_still_prunes_same_name_duplicates():
    server = [_k("xlii-worker-1", "id-live"), _k("xlii-worker-1", "id-stale")]
    active_ids, active_names = _active_pool_sets(
        [{"label": "worker-1", "api_key_id": "id-live"}],
        server,
    )

    out = _sel(server, active_ids=active_ids, active_names=active_names)

    assert [k["apiKeyId"] for k in out] == ["id-stale"]


def test_name_glob_filter_is_precise():
    import fnmatch
    server = [_k("xli-worker-1", "a"), _k("xlii-worker-1", "b"), _k("random", "c")]
    # 'xli-*' must match the legacy name but NOT 'xlii-...'
    name_ok = lambda n: fnmatch.fnmatch(n, "xli-*")
    assert _names(_sel(server, name_ok=name_ok)) == ["xli-worker-1"]


def test_default_scope_protects_foreign_keys():
    # the default name gate keeps prune to xlii-provisioned keys, so a shared
    # management key's OTHER keys (other projects/tools) are never candidates
    server = [_k("xlii-worker-1", "a"), _k("xli-worker-2", "b"),
              _k("VSCode Agent", "c"), _k("codex-test", "d")]
    name_ok = lambda n: n.startswith(("xlii-", "xli-"))
    assert _names(_sel(server, name_ok=name_ok)) == ["xlii-worker-1", "xli-worker-2"]


def test_older_than_filter():
    server = [_k("fresh", "a", days_old=2), _k("ancient", "b", days_old=90)]
    assert _names(_sel(server, older_than_days=30)) == ["ancient"]


def test_older_than_skips_keys_without_createtime():
    # conservative: if we can't tell a key's age, don't propose deleting it
    server = [{"name": "no-date", "apiKeyId": "z"}]
    assert _sel(server, older_than_days=10) == []


def test_include_active_overrides_protection():
    server = [_k("xlii-primary-1", "id-A")]
    assert _names(_sel(server, active_ids={"id-A"}, include_active=True)) == ["xlii-primary-1"]
