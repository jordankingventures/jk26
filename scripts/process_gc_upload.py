"""
YouTube View Tracker -- process_gc_upload.py
Verwerkt het gc.json-bestand dat via de GitHub-website in data/incoming/ is
geupload (zie .github/workflows/process_gc_upload.yml): controleert het en
vult data/analysis_data.json aan met backfill_global_charts.py --fresh-window
(bestaande punten worden nooit overschreven).

Gebruik: python scripts/process_gc_upload.py <pad/naar/gc.json>
Faalt (exitcode 1, bestand wordt NIET verwerkt) bij ongeldige JSON, geen
bekende artiesten, onbruikbare datums/waarden of een waarde die meer dan
SPRONG_FACTOR keer boven/onder het recente niveau van die artiest ligt
(een kapotte download dus, geen echte release).
"""

import json
import os
import re
import statistics
import sys

import backfill_global_charts
from fetch_global_charts import ANALYSIS_FILE, ARTISTS

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SPRONG_FACTOR = 5


def fail(msg):
    print(f"::error::{msg}")
    sys.exit(1)


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    path = sys.argv[1]
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        fail(f"gc.json is geen geldige JSON: {e}")
    if not isinstance(data, dict) or not data:
        fail("gc.json heeft niet de verwachte vorm {artiest: {datum: views}}")

    known = [k for k in data if k in ARTISTS]
    unknown = [k for k in data if k not in ARTISTS]
    if not known:
        fail("Geen enkele bekende artiest in gc.json")
    if unknown:
        print(f"Onbekende artiesten (genegeerd): {unknown}")
    if len(known) < len(ARTISTS):
        missing = [k for k in ARTISTS if k not in data]
        print(f"::warning::Maar {len(known)} van {len(ARTISTS)} artiesten in het bestand, ontbrekend: {missing}")

    with open(ANALYSIS_FILE) as f:
        analysis = json.load(f)

    problems = []
    for k in known:
        values = data[k]
        if not isinstance(values, dict) or not values:
            problems.append(f"{k}: geen datums")
            continue
        points = analysis.get("ucg_factor", {}).get(k, {}).get("points", [])
        have = {p["date"] for p in points}
        recent = [p["global_charts"] for p in points[-7:]]
        median = statistics.median(recent) if recent else None
        for date, views in values.items():
            if not DATE_RE.match(date) or isinstance(views, bool) or not isinstance(views, (int, float)) or views <= 0:
                problems.append(f"{k} {date}: ongeldige datum of waarde ({views!r})")
            elif median and date not in have and not (median / SPRONG_FACTOR <= views <= median * SPRONG_FACTOR):
                problems.append(f"{k} {date}: {int(views):,} ligt ver buiten het recente niveau ({int(median):,})")
    if problems:
        for p in problems[:20]:
            print(f"::error::{p}")
        fail(f"{len(problems)} probleem/problemen in gc.json -- niets verwerkt")

    latest = max(max(v) for v in data.values() if isinstance(v, dict) and v)
    print(f"{len(known)} artiesten, laatste datum {latest}")

    sys.argv = ["backfill_global_charts.py", path, "--fresh-window"]
    backfill_global_charts.main()

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"Global Charts-upload verwerkt: {len(known)} artiesten, laatste datum {latest}.\n")


if __name__ == "__main__":
    main()
