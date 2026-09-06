"""plugin_search / /get scoring — typos of a subscribed id still hit."""

from __future__ import annotations

from types import SimpleNamespace

from xlii.plugin import _edit_distance, _typo_hit, search_plugins


class _P:
    def __init__(self, pid, name="", desc="", cats=(), actions=()):
        self.id = pid
        self._meta = {"name": name, "description": desc, "categories": list(cats)}
        self._actions = actions

    def metadata(self):
        return self._meta

    def manifest(self):
        if not self._actions:
            return None
        acts = [SimpleNamespace(id=a, description="", response_shape="", params={})
                for a in self._actions]
        return SimpleNamespace(actions=acts)


def test_edit_distance_and_typo_hit():
    assert _edit_distance("hackernews", "haclernews", 2) == 1
    assert _typo_hit("haclernews", "hackernews")
    assert _typo_hit("hackrnews", "hackernews")
    assert not _typo_hit("get", "gdelt")
    assert not _typo_hit("weather", "hackernews")


def test_search_matches_misspelled_plugin_id():
    hn = _P("hackernews", name="Hacker News", desc="HN stories", cats=("tech", "news"))
    wiki = _P("wikipedia", name="Wikipedia", desc="article lookup", cats=("reference",))
    hits = search_plugins("get haclernews on nvidia", [hn, wiki], limit=5)
    assert hits
    assert hits[0][0].id == "hackernews"
    assert hits[0][1] > 0


def test_search_exact_id_still_wins():
    hn = _P("hackernews", name="Hacker News")
    hits = search_plugins("hackernews nvidia", [hn])
    assert hits[0][0].id == "hackernews"
    assert hits[0][1] >= 3.0
