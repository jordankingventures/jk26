"""
YouTube View Tracker -- backfill_global_charts.py
Handmatig (eenmalig) ontbrekende Global Charts-dagtotalen aanvullen in
data/analysis_data.json, bv. na een periode waarin fetch_global_charts.py niets
kon ophalen (HTTP 429 vanaf 5 okt 2026).

Gebruik:
  python3 scripts/backfill_global_charts.py gc_input.json
      gc_input.json = {"taylor": {"2026-10-04": 12345678, ...}, "drake": {...}}
      (bv. gemaakt met het console-snippet uit de toelichting, of handmatig)
  python3 scripts/backfill_global_charts.py --fetch 2026-10-04
      probeert de waarden zelf op te halen vanaf die datum (alleen zinvol zodra
      het endpoint weer werkt).
  Optie --fresh-window (bv. voor het wekelijkse Safari-bestand, zie
  scripts/update_gc.sh): dagen binnen het gebruikelijke 2-dagen-venster tellen als
  "vers" (bijgewerkt, zonder backfill-vlag), net als in de dagelijkse run.

Regels:
 - Bestaande punten worden NOOIT overschreven.
 - Dagen zonder afgesloten eigen counter (final_day_total) of in EXCLUDED_DATES
   worden overgeslagen (zelfde logica als de dagelijkse run).
 - Aangevulde punten krijgen "backfill": true -- die zijn later dan het gebruikelijke
   2-dagen-venster opgehaald en dus mogelijk al door YouTube herzien (niet exact
   de "eerst beschikbare" waarde waarop Kalshi resolveert).
"""

import json
import sys
from datetime import datetime, timedelta, timezone

from fetch_global_charts import ANALYSIS_FILE, ARTISTS, WINDOW_DAYS, fetch_daily_views, merge_artist


def main():
    args = sys.argv[1:]
    fresh_window = "--fresh-window" in args
    args = [a for a in args if a != "--fresh-window"]
    if not args:
        print(__doc__)
        sys.exit(1)
    fresh_from = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d") if fresh_window else None

    analysis = json.loads(open(ANALYSIS_FILE).read())
    analysis.setdefault("ucg_factor", {})

    if args[0] == "--fetch":
        start = args[1]
        end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        values = {}
        for key, info in ARTISTS.items():
            try:
                values[key] = fetch_daily_views(info["entity_id"], start, end)
            except Exception as e:
                print(f"[{key}] FOUT bij ophalen: {e}")
                sys.exit(2)
    else:
        values = json.loads(open(args[0]).read())

    unknown = [k for k in values if k not in ARTISTS]
    if unknown:
        print(f"Onbekende artiesten in invoer (genegeerd): {unknown}")

    total_added = 0
    for key, info in ARTISTS.items():
        gc_by_date = values.get(key)
        if not gc_by_date:
            print(f"[{key}] geen invoer, overgeslagen")
            continue
        added, total = merge_artist(analysis, key, info, {d: int(v) for d, v in gc_by_date.items()}, fresh_from=fresh_from)
        total_added += added
        print(f"[{key}] {added} dagen aangevuld, {total} dagen totaal")

    with open(ANALYSIS_FILE, "w") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Klaar: {total_added} punten toegevoegd.")


if __name__ == "__main__":
    main()
