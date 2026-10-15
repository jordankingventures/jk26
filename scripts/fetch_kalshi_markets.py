"""
YouTube View Tracker — fetch_kalshi_markets.py
Draait via GitHub Actions, geen authenticatie nodig: Kalshi's GET /events en
GET /markets zijn publiek toegankelijk (geen API-key, geen orders -- puur
lezen). Losstaand van kalshi_daily_mm.py / kalshi_auto_taylor.py, die wel de
prive-trading-key gebruiken om daadwerkelijk orders te plaatsen.

Haalt de actuele Kalshi-views-markten op met hun live YES/NO-prijzen per
bracket: weekly (series KXYTVIEWSW, "Highest daily view count this week", 1
event per artiest) en monthly (series KXYTVIEWSHIGH, "Highest daily view
count" in een kalendermaand, per artiest 1 event per open maand), en
schrijft dat naar data/kalshi_markets.json.
De Kalshi-tab in index.html vergelijkt dat client-side met de eigen
min/max-voorspelling voor de lopende week (geen logica hier -- puur de
ruwe marktdata wegschrijven, 1 bron van waarheid voor die berekening).
"""

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES_TICKER = "KXYTVIEWSW"
MONTHLY_SERIES_TICKER = "KXYTVIEWSHIGH"
OUT_FILE = "data/kalshi_markets.json"
USER_AGENT = "Mozilla/5.0 (compatible; PersonalViewTracker/1.0)"

# Event-ticker-prefix (bv. "TAY" in KXYTVIEWSW-TAY26SEP27) -> onze eigen
# artiest-sleutel. Kalshi's weergavenaam wijkt soms af van de onze (bv.
# "NBA YoungBoy" vs "YoungBoy Never Broke Again"), dus matchen we op dit
# vaste prefix i.p.v. op naam.
EVENT_PREFIX_TO_ARTIST = {
    "ARI": "ariana",
    "BAD": "badbunny",
    "DRA": "drake",
    "FUE": "fuerzaregida",
    "FUT": "future",
    "JUS": "bieber",
    "KAT": "katseye",
    "KEN": "kendrick",
    "MOR": "wallen",
    "OLI": "olivia",
    "POS": "postmalone",
    "TAT": "tatemcrae",
    "TAY": "taylor",
    "WEE": "weeknd",
    "YE": "kanye",
    "YOU": "youngboy",
}


def get_json(path, params):
    url = f"{API_BASE}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def artist_key_for_event_ticker(event_ticker, series=SERIES_TICKER):
    """'KXYTVIEWSW-TAY26SEP27' -> 'TAY' -> 'taylor' (of None als onbekend)."""
    if not event_ticker.startswith(series + "-"):
        return None
    suffix = event_ticker[len(series) + 1:]
    m = re.match(r"^([A-Z]+)\d", suffix)
    if not m:
        return None
    return EVENT_PREFIX_TO_ARTIST.get(m.group(1))


def fetch_events(series=SERIES_TICKER):
    data = get_json("/events", {"series_ticker": series, "status": "open", "limit": 200})
    return data.get("events", [])


def fetch_all_markets(series=SERIES_TICKER):
    markets = []
    cursor = None
    while True:
        params = {"series_ticker": series, "status": "open", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        data = get_json("/markets", params)
        markets.extend(data.get("markets", []))
        cursor = data.get("cursor")
        if not cursor:
            break
    return markets


def to_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def bracket_from_market(m):
    return {
        "ticker": m["ticker"],
        "floor_strike": m.get("floor_strike"),
        "label": m.get("yes_sub_title"),
        "yes_bid": to_float(m.get("yes_bid_dollars")),
        "yes_ask": to_float(m.get("yes_ask_dollars")),
        "no_bid": to_float(m.get("no_bid_dollars")),
        "no_ask": to_float(m.get("no_ask_dollars")),
    }


def month_of_event(event):
    """'Ariana Grande: September 2026' -> '2026-09' (of None)."""
    sub = (event.get("sub_title") or "").split(":")[-1].strip()
    try:
        return datetime.strptime(sub, "%B %Y").strftime("%Y-%m")
    except ValueError:
        return None


def fetch_monthly(artists):
    """Voegt per artiest 'monthly' toe: lijst open maand-events (vroegste sluiting eerst)."""
    events = fetch_events(MONTHLY_SERIES_TICKER)
    by_ticker = {}
    for e in events:
        key = artist_key_for_event_ticker(e["event_ticker"], MONTHLY_SERIES_TICKER)
        if not key:
            print(f"WAARSCHUWING: onbekend artiest-prefix voor maand-event {e['event_ticker']}")
            continue
        entry = {
            "event_ticker": e["event_ticker"],
            "title": e.get("title"),
            "month": month_of_event(e),
            "close_time": None,
            "brackets": [],
        }
        by_ticker[e["event_ticker"]] = (key, entry)

    for m in fetch_all_markets(MONTHLY_SERIES_TICKER):
        hit = by_ticker.get(m["event_ticker"])
        if not hit:
            continue
        hit[1]["close_time"] = m.get("close_time")
        hit[1]["brackets"].append(bracket_from_market(m))

    for key, entry in by_ticker.values():
        entry["brackets"].sort(key=lambda b: b["floor_strike"] if b["floor_strike"] is not None else 0)
        artists.setdefault(key, {"artist_name": (entry["title"] or "").split(":")[0].strip(), "brackets": []})
        artists[key].setdefault("monthly", []).append(entry)
    for a in artists.values():
        if "monthly" in a:
            a["monthly"].sort(key=lambda x: x["close_time"] or "")


def main():
    events = fetch_events()
    markets = fetch_all_markets()

    artists = {}
    for e in events:
        key = artist_key_for_event_ticker(e["event_ticker"])
        if not key:
            print(f"WAARSCHUWING: onbekend artiest-prefix voor event {e['event_ticker']}")
            continue
        artists[key] = {
            "artist_name": (e.get("title") or "").split(":")[0].strip(),
            "event_ticker": e["event_ticker"],
            "title": e.get("title"),
            "close_time": None,  # hieronder gevuld vanuit de markets van dit event
            "brackets": [],
        }

    unmatched = 0
    for m in markets:
        key = artist_key_for_event_ticker(m["event_ticker"])
        if not key or key not in artists:
            unmatched += 1
            continue
        artists[key]["close_time"] = m.get("close_time")
        artists[key]["brackets"].append(bracket_from_market(m))

    for a in artists.values():
        a["brackets"].sort(key=lambda b: b["floor_strike"] if b["floor_strike"] is not None else 0)

    fetch_monthly(artists)

    out = {
        "series_ticker": SERIES_TICKER,
        "monthly_series_ticker": MONTHLY_SERIES_TICKER,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artists": artists,
    }

    with open(OUT_FILE, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")

    total_brackets = sum(len(a["brackets"]) for a in artists.values())
    print(f"{len(artists)} artiesten, {total_brackets} brackets totaal.")
    if unmatched:
        print(f"({unmatched} markt(en) genegeerd, geen bekend artiest-prefix)")


if __name__ == "__main__":
    main()
