"""
YouTube View Tracker -- backfill_kalshi_history.py
Eenmalig: bouwt data/kalshi_price_history.json op uit de git-geschiedenis van
data/kalshi_markets.json (die wordt elke 5 minuten gecommit), zodat de
prijshistorie niet pas begint op het moment dat fetch_kalshi_markets.py de
historie ging bijhouden. Aantallen shares ontbreken in die oude snapshots
(null) -- vanaf de eerste live run worden ze wel vastgelegd.

Gebruik (vanuit de repo-root):
  python3 scripts/backfill_kalshi_history.py 2026-10-11
"""

import json
import subprocess
import sys
from datetime import datetime, timezone

MARKETS_FILE = "data/kalshi_markets.json"
HISTORY_FILE = "data/kalshi_price_history.json"


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def main():
    since = sys.argv[1] if len(sys.argv) > 1 else "2026-10-11"
    commits = git("log", f"--since={since}", "--format=%H", "--", MARKETS_FILE).split()
    commits.reverse()  # oud -> nieuw

    markets = {}
    for h in commits:
        try:
            d = json.loads(git("show", f"{h}:{MARKETS_FILE}"))
            minute = int(datetime.strptime(d["fetched_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() // 60)
        except (subprocess.CalledProcessError, ValueError, KeyError):
            continue
        for key, a in d["artists"].items():
            ladders = [("w", a.get("event_ticker"), a.get("brackets", []))]
            ladders += [("m", m.get("event_ticker"), m.get("brackets", [])) for m in a.get("monthly", [])]
            for kind, event, brackets in ladders:
                for b in brackets:
                    ask = b.get("yes_ask")
                    if ask is None:
                        continue
                    e = markets.setdefault(b["ticker"], {"artist": key, "kind": kind, "event": event, "strike": b.get("floor_strike"), "s": []})
                    if not e["s"] or e["s"][-1][1] != ask:
                        e["s"].append([minute, ask, None])
                    e["_last"] = minute

    # alleen markten die in de nieuwste snapshot nog open staan
    latest = json.loads(open(MARKETS_FILE).read())
    open_tickers = set()
    for a in latest["artists"].values():
        for b in a.get("brackets", []):
            open_tickers.add(b["ticker"])
        for m in a.get("monthly", []):
            for b in m.get("brackets", []):
                open_tickers.add(b["ticker"])
    out = {t: {k: v for k, v in e.items() if k != "_last"} for t, e in markets.items() if t in open_tickers}

    with open(HISTORY_FILE, "w") as f:
        json.dump({"updated": latest["fetched_at"], "markets": out}, f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")
    print(f"{len(commits)} snapshots, {len(out)} markten, {sum(len(e['s']) for e in out.values())} punten.")


if __name__ == "__main__":
    main()
