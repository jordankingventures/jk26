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
from datetime import datetime, timezone

from fetch_global_charts import ANALYSIS_FILE, ARTISTS, fetch_daily_views, merge_artist


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

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
        added, total = merge_artist(analysis, key, info, {d: int(v) for d, v in gc_by_date.items()}, backfill=True)
        total_added += added
        print(f"[{key}] {added} dagen aangevuld, {total} dagen totaal")

    with open(ANALYSIS_FILE, "w") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Klaar: {total_added} punten toegevoegd.")


if __name__ == "__main__":
    main()
