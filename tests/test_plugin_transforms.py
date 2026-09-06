"""Plugin render-prep transforms + the numeric-format-spec fix (the-fold Vector B
follow-up — stock plugins → deterministic `output: schema`).

Covers the closed transform vocabulary, the `_Field` format-spec fix, the RSS
input adapter, and two end-to-end stock-plugin conversions (hackernews,
open-meteo) rendered over realistic RAW upstream bodies — the proof that a
converted stock plugin renders deterministically without the model in the loop.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import xlii
from xlii.plugin_call import _apply_template, render_schema_output
from xlii.plugin_manifest import parse_manifest
from xlii.plugin_transforms import _get, apply_transforms, parse_rss, unwrap_article_url

STOCK = Path(xlii.__file__).parent / "stock_plugins"


# --------------------------------------------------------------------------- #
#  Path addressing — dotted string OR explicit segment list
# --------------------------------------------------------------------------- #

def test_path_dotted_and_segment_list():
    body = {"a": {"b": [{"c": 1}]}, "Global Quote": {"05. price": "190.50"}}
    assert _get(body, "a.b.0.c") == 1
    assert _get(body, ["Global Quote", "05. price"]) == "190.50"  # literal-dot key
    assert _get(body, "a.b.9.c") is None


# --------------------------------------------------------------------------- #
#  The _Field numeric-format-spec fix (the load-bearing renderer bug)
# --------------------------------------------------------------------------- #

def test_numeric_spec_renders():
    assert _apply_template("${price:,.2f}", {"price": 65000.5}) == "$65,000.50"


def test_numeric_spec_on_string_number_coerces():
    assert _apply_template("{price:,.2f}", {"price": "65000.5"}) == "65,000.50"


def test_missing_field_degrades_per_field_not_whole_template():
    # The old bug: a numeric spec on a missing field raised ValueError and dropped
    # the ENTIRE template to its literal form. Now it degrades that one field only.
    assert _apply_template("{name}: {n:,.0f}", {"name": "x"}) == "x: "


def test_field_types_bool_none_container():
    assert _apply_template("{a}/{b}/{c}", {"a": True, "b": None, "c": {"k": 1}}) == 'true//{"k": 1}'


# --------------------------------------------------------------------------- #
#  Transform ops
# --------------------------------------------------------------------------- #

def test_rename_in_list():
    body = {"hits": [{"num_comments": 5}, {"num_comments": 0}]}
    apply_transforms(body, [{"op": "rename", "list": "hits", "from": "num_comments", "to": "comments"}])
    assert [h["comments"] for h in body["hits"]] == [5, 0]


def test_derive_templates_new_field_per_item():
    body = {"hits": [{"objectID": "42"}]}
    apply_transforms(body, [{"op": "derive", "list": "hits", "to": "hn_url",
                             "template": "https://news.ycombinator.com/item?id={objectID}"}])
    assert body["hits"][0]["hn_url"] == "https://news.ycombinator.com/item?id=42"


def test_lift_nested_to_root():
    body = {"current_weather": {"temperature": 12.0, "windspeed": 5}, "timezone": "UTC"}
    apply_transforms(body, [{"op": "lift", "from": "current_weather"}])
    assert body["temperature"] == 12.0 and body["windspeed"] == 5 and body["timezone"] == "UTC"


def test_lift_indexed():
    body = {"results": [{"name": "London", "latitude": 51.5}]}
    apply_transforms(body, [{"op": "lift", "from": "results.0"}])
    assert body["name"] == "London" and body["latitude"] == 51.5


def test_zip_parallel_arrays():
    body = {"daily": {"time": ["d1", "d2"], "tmax": [10, 11]}}
    apply_transforms(body, [{"op": "zip", "from": ["daily.time", "daily.tmax"],
                             "names": ["date", "temp"], "into": "days"}])
    assert body["days"] == [{"date": "d1", "temp": 10}, {"date": "d2", "temp": 11}]


def test_zip_on_root_list_returns_wrapper():
    body = ["q", ["A", "B"], ["dA", "dB"]]
    out = apply_transforms(body, [{"op": "zip", "from": ["1", "2"],
                                   "names": ["title", "desc"], "into": "matches"}])
    assert out == {"matches": [{"title": "A", "desc": "dA"}, {"title": "B", "desc": "dB"}]}


def test_flatten_map():
    body = {"bitcoin": {"usd": 65000}, "ethereum": {"usd": 3200}}
    out = apply_transforms(body, [{"op": "flatten_map", "into": "prices",
                                   "key_as": "coin", "value_as": "quote"}])
    assert out["prices"] == [{"coin": "bitcoin", "quote": {"usd": 65000}},
                             {"coin": "ethereum", "quote": {"usd": 3200}}]


def test_cast_strips_and_coerces():
    body = {"row": [{"pct": "12.5%", "n": "1,234"}]}
    apply_transforms(body, [{"op": "cast", "list": "row", "fields": ["pct", "n"],
                             "to": "number", "strip": ["%", ","]}])
    assert body["row"][0]["pct"] == 12.5 and body["row"][0]["n"] == 1234


def test_concat():
    body = {"j": [{"first": "Ada", "last": "Lovelace"}]}
    apply_transforms(body, [{"op": "concat", "list": "j", "from": ["first", "last"],
                             "to": "name", "sep": " "}])
    assert body["j"][0]["name"] == "Ada Lovelace"


def test_first_of_skips_empty():
    body = {"links": {"homepage": ["", "https://x.org"]}}
    apply_transforms(body, [{"op": "first_of",
                             "from": ["links.homepage.0", "links.homepage.1"], "to": "homepage"}])
    assert body["homepage"] == "https://x.org"


def test_code_map_int_key_and_default():
    body = {"weathercode": 2}
    apply_transforms(body, [{"op": "code_map", "field": "weathercode", "to": "desc",
                             "table": {0: "Clear", 2: "Partly cloudy"}, "default": "?"}])
    assert body["desc"] == "Partly cloudy"
    miss = {"weathercode": 999}
    apply_transforms(miss, [{"op": "code_map", "field": "weathercode", "to": "desc",
                             "table": {0: "Clear"}, "default": "Unknown"}])
    assert miss["desc"] == "Unknown"


def test_unknown_or_bad_op_skipped_fail_safe():
    body = {"a": 1}
    out = apply_transforms(body, [{"op": "nope"}, {"not": "a dict-op"},
                                  {"op": "rename", "from": "a", "to": "b"}])
    assert out == {"a": 1, "b": 1}


# --------------------------------------------------------------------------- #
#  RSS input adapter (the one non-JSON stock case)
# --------------------------------------------------------------------------- #

def test_parse_rss():
    xml = ("<rss><channel>"
           "<item><title>Headline A</title><link>http://a</link><pubDate>Mon</pubDate></item>"
           "<item><title>Headline B</title><link>http://b</link></item>"
           "</channel></rss>")
    out = parse_rss(xml)
    assert out["items"][0]["title"] == "Headline A" and out["items"][0]["link"] == "http://a"
    assert out["items"][0]["published"] == "Mon"
    assert out["items"][1]["title"] == "Headline B"


def test_parse_rss_bad_xml_returns_none():
    assert parse_rss("not xml <<<") is None


def test_unwrap_google_news_url():
    import base64

    inner = b"padhttps://chicago.suntimes.com/story/foo"
    blob = base64.urlsafe_b64encode(inner).decode().rstrip("=")
    url = f"https://news.google.com/rss/articles/{blob}?oc=5"
    assert unwrap_article_url(url) == "https://chicago.suntimes.com/story/foo"
    assert unwrap_article_url("https://bbc.com/x") == "https://bbc.com/x"


def test_parse_rss_strips_title_suffix_and_unwraps():
    import base64

    inner = b"xxhttps://example.com/article"
    blob = base64.urlsafe_b64encode(inner).decode().rstrip("=")
    xml = (
        "<rss><channel>"
        f"<item><title>Big News - BBC</title>"
        f"<link>https://news.google.com/rss/articles/{blob}</link>"
        "<source>BBC</source><pubDate>Mon</pubDate></item>"
        "</channel></rss>"
    )
    out = parse_rss(xml)
    assert out["items"][0]["title"] == "Big News"
    assert out["items"][0]["link"] == "https://example.com/article"
    assert out["items"][0]["source"] == "BBC"


def test_message_list_renders_markdown_links():
    from xlii.plugin_call import _render_message_list

    body = {"items": [
        {"title": "Hello", "link": "https://ex.com/a", "source": "Ex", "published": "Mon"},
    ]}
    out = _render_message_list(body, {"header": "Google News", "list_key": "items"})
    assert out.startswith("### Google News")
    assert "[Hello](https://ex.com/a)" in out
    assert "Ex" in out


# --------------------------------------------------------------------------- #
#  End-to-end: real stock manifests render on realistic RAW bodies
# --------------------------------------------------------------------------- #

def test_hackernews_renders_on_raw_algolia_body():
    m = parse_manifest((STOCK / "hackernews.md").read_text())
    a = m.get_action("search")
    assert a.is_schema and a.is_deterministic  # opted in → /get fast-path eligible
    raw = json.dumps({"hits": [
        {"objectID": "111", "title": "Rust is nice", "url": "http://x",
         "author": "pg", "points": 128, "num_comments": 42},
        {"objectID": "222", "title": "Ask HN: cats?", "url": None,
         "author": "dang", "points": 7, "num_comments": 3},
    ]})
    out = render_schema_output(a, raw)
    assert "Rust is nice" in out and "128" in out and "42" in out   # points + renamed comments
    assert "news.ycombinator.com/item?id=111" in out                # hn_url derived from objectID
    assert "num_comments" not in out                                # raw key not surfaced


def test_open_meteo_current_weather_renders():
    a = parse_manifest((STOCK / "open-meteo.md").read_text()).get_action("current_weather")
    assert a.is_schema
    raw = json.dumps({"timezone": "America/Chicago", "current_weather": {
        "temperature": 12.4, "windspeed": 15.0, "winddirection": 270,
        "weathercode": 3, "time": "2026-07-05T12:00"}})
    out = render_schema_output(a, raw)
    assert "12.4" in out and "15.0" in out and "270" in out
    assert "Overcast" in out                        # weathercode 3 → code_map
    assert "America/Chicago" in out


def test_open_meteo_daily_zip_renders():
    a = parse_manifest((STOCK / "open-meteo.md").read_text()).get_action("daily_forecast")
    raw = json.dumps({"timezone": "UTC", "daily": {
        "time": ["2026-07-05", "2026-07-06"], "temperature_2m_max": [28.0, 30.0],
        "temperature_2m_min": [18.0, 19.0], "precipitation_sum": [0.0, 2.5],
        "wind_speed_10m_max": [12.0, 9.0]}})
    out = render_schema_output(a, raw)
    assert "2026-07-05" in out and "28" in out and "2026-07-06" in out and "30" in out


def test_open_meteo_geocode_lift_renders():
    a = parse_manifest((STOCK / "open-meteo.md").read_text()).get_action("geocode")
    raw = json.dumps({"results": [{"name": "London", "country": "United Kingdom",
                                   "latitude": 51.5, "longitude": -0.13,
                                   "timezone": "Europe/London"}]})
    out = render_schema_output(a, raw)
    assert "London" in out and "United Kingdom" in out and "51.5" in out and "Europe/London" in out


# --------------------------------------------------------------------------- #
#  scale op (Phase 2 addition — fraction → percent for Alpha Vantage)
# --------------------------------------------------------------------------- #

def test_scale_op_coerces_and_multiplies():
    body = {"y": "0.0044"}
    apply_transforms(body, [{"op": "scale", "fields": ["y"], "by": 100}])
    assert abs(body["y"] - 0.44) < 1e-9


# --------------------------------------------------------------------------- #
#  Phase 2 — every converted stock plugin renders on a realistic RAW body.
#  Each (plugin, action, raw, expected-substrings). RSS bodies pass is_json=False.
# --------------------------------------------------------------------------- #

_RSS = ("<rss><channel><item><title>Big News - BBC</title><link>http://g/x</link>"
        "<source>BBC</source><pubDate>Sat, 05 Jul 2026</pubDate></item></channel></rss>")

_PHASE2_CASES = [
    ("gdelt", "search_articles",
     {"articles": [{"title": "War", "domain": "bbc.com", "sourcecountry": "UK",
                    "seendate": "20260705", "url": "http://x"}]},
     ["War", "UK", "bbc.com"], True),
    ("wikipedia", "title_search",
     ["py", ["Python", "Pytest"], ["lang", "tool"], ["http://a", "http://b"]],
     ["Python", "http://a"], True),
    ("wikipedia", "fulltext_search",
     {"query": {"search": [{"title": "Python", "snippet": "a lang", "wordcount": 1200}]}},
     ["Python", "1200"], True),
    ("wikipedia", "page_summary",
     {"title": "Python", "description": "language", "extract": "Python is a language."},
     ["Python", "language"], True),
    ("coingecko", "search",
     {"coins": [{"id": "bitcoin", "name": "Bitcoin", "symbol": "BTC", "market_cap_rank": 1}]},
     ["bitcoin", "BTC"], True),
    ("coingecko", "coin_detail",
     {"name": "Bitcoin", "symbol": "BTC", "market_cap_rank": 1,
      "market_data": {"current_price": {"usd": 65000.5}, "market_cap": {"usd": 1.2e12},
                      "total_volume": {"usd": 3e10}, "price_change_percentage_24h": 2.5,
                      "ath": {"usd": 73000.0}, "atl": {"usd": 67.81}},
      "links": {"homepage": ["", "https://bitcoin.org"]}},
     ["Bitcoin", "65,000.50", "+2.50%", "bitcoin.org"], True),
    ("coingecko", "trending",
     {"coins": [{"item": {"name": "Pepe", "symbol": "PEPE", "id": "pepe", "market_cap_rank": 42}}]},
     ["Pepe", "42"], True),
    ("coingecko", "global",
     {"data": {"active_cryptocurrencies": 12000, "markets": 900,
               "total_market_cap": {"usd": 2.4e12}, "total_volume": {"usd": 8e10},
               "market_cap_percentage": {"btc": 52.3, "eth": 17.1},
               "market_cap_change_percentage_24h_usd": -1.2}},
     ["52.30%", "12,000"], True),
    ("courtlistener", "search_opinions",
     {"results": [{"id": 5, "caseName": "Roe v Wade", "court": "scotus",
                   "dateFiled": "1973-01-22", "citation": "410 US 113", "snippet": "."}]},
     ["Roe v Wade", "1973-01-22", "id=5"], True),
    ("courtlistener", "search_judges",
     {"results": [{"id": 9, "name_first": "Ruth", "name_middle": "Bader",
                   "name_last": "Ginsburg", "gender": "f",
                   "date_dob": "1933-03-15", "date_dod": "2020-09-18"}]},
     ["Ruth Bader Ginsburg", "2020-09-18"], True),
    ("xtwitter", "recent_search",
     {"data": [{"id": "9", "text": "hello world", "created_at": "2026",
                "public_metrics": {"like_count": 1200, "retweet_count": 50, "reply_count": 8}}]},
     ["hello world", "1,200", "status/9"], True),
    ("xtwitter", "user_by_handle",
     {"data": {"id": "5", "name": "Ada", "username": "ada", "description": "eng",
               "public_metrics": {"followers_count": 9000, "following_count": 100, "tweet_count": 42},
               "verified": True, "created_at": "2020"}},
     ["Ada", "@ada", "9,000", "true"], True),
    ("xtwitter", "get_tweet",
     {"data": {"id": "7", "text": "tweet body", "created_at": "2026",
               "public_metrics": {"like_count": 3, "retweet_count": 1, "reply_count": 0}},
      "includes": {"users": [{"username": "bob"}]}},
     ["@bob", "tweet body", "status/7"], True),
    ("flightaware", "flight_status",
     {"flights": [{"ident": "UA1", "status": "En Route",
                   "origin": {"code_iata": "SFO", "name": "San Francisco"},
                   "destination": {"code_iata": "JFK", "name": "Kennedy"},
                   "scheduled_off": "08:00", "scheduled_on": "16:00", "aircraft_type": "B788"}]},
     ["UA1", "SFO — San Francisco", "JFK — Kennedy", "B788"], True),
    ("flightaware", "airport_info",
     {"name": "San Francisco Intl", "code_iata": "SFO", "code_icao": "KSFO",
      "city": "San Francisco", "state": "CA", "country_code": "US", "latitude": 37.6,
      "longitude": -122.4, "timezone": "America/Los_Angeles", "elevation": 13},
     ["San Francisco, CA", "US", "13 ft"], True),
    ("flightaware", "operator_info",
     {"name": "United", "icao": "UAL", "iata": "UA", "callsign": "UNITED",
      "country": "US", "location": "Chicago"},
     ["United", "UAL", "UNITED"], True),
    ("alpha-vantage", "quote",
     {"Global Quote": {"01. symbol": "AAPL", "05. price": "190.50", "02. open": "188.00",
                       "03. high": "191.20", "04. low": "187.50", "06. volume": "52000000",
                       "09. change": "2.50", "10. change percent": "1.33%",
                       "07. latest trading day": "2026-07-05"}},
     ["AAPL", "190.50", "+1.33%", "52,000,000"], True),
    ("alpha-vantage", "company_overview",
     {"Symbol": "AAPL", "Name": "Apple Inc", "Sector": "Tech", "Industry": "Consumer Electronics",
      "MarketCapitalization": "3400000000000", "PERatio": "32.5", "EPS": "6.13",
      "DividendYield": "0.0044", "Beta": "1.28", "52WeekHigh": "237.23", "52WeekLow": "164.08"},
     ["Apple Inc", "3,400,000,000,000", "0.44%"], True),  # dividend fraction → percent (scale)
    ("alpha-vantage", "fx_rate",
     {"Realtime Currency Exchange Rate": {"1. From_Currency Code": "USD",
      "2. From_Currency Name": "US Dollar", "3. To_Currency Code": "EUR",
      "4. To_Currency Name": "Euro", "5. Exchange Rate": "0.92150",
      "6. Last Refreshed": "2026-07-05 12:00"}},
     ["USD", "EUR", "0.921500"], True),
    ("google-news", "search", _RSS, ["Big News", "BBC", "http://g/x"], False),
    ("google-news", "top_headlines", _RSS, ["Big News"], False),
    ("telegram_send", "send",
     {"ok": True, "result": {"message_id": 55, "chat": {"id": 99, "first_name": "Me"},
                             "date": 1720180800, "text": "build done"}},
     ["Me", "chat 99", "55", "build done"], True),
]


@pytest.mark.parametrize("plugin,action,raw,expected,is_json", _PHASE2_CASES,
                         ids=[f"{p}.{a}" for p, a, *_ in _PHASE2_CASES])
def test_phase2_stock_plugin_renders(plugin, action, raw, expected, is_json):
    a = parse_manifest((STOCK / f"{plugin}.md").read_text()).get_action(action)
    assert a is not None and a.is_schema and a.is_deterministic, f"{plugin}.{action} not opted in"
    body = json.dumps(raw) if is_json else raw
    out = render_schema_output(a, body)
    for tok in expected:
        assert tok in out, f"{plugin}.{action}: missing {tok!r} in:\n{out}"
