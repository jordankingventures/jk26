#!/usr/bin/env bash
# Verwerkt het bestand gc.json (uit Safari, zie scripts/gc_bookmarklet.js) in
# data/analysis_data.json, commit en pusht het.
#
# Gebruik (vanuit de projectmap):  ./scripts/update_gc.sh
# Opties via omgevingsvariabelen:
#   DOWNLOADS=/pad      map waarin gc*.json staat (standaard ~/Downloads)
#   DRY_RUN=1           alleen verwerken, niet committen/pushen
set -euo pipefail

cd "$(dirname "$0")/.."
DOWNLOADS="${DOWNLOADS:-$HOME/Downloads}"

# Nieuwste gc*.json (Safari maakt bij een tweede download "gc 2.json" e.d.)
FILE="$(ls -t "$DOWNLOADS"/gc*.json 2>/dev/null | head -1 || true)"
if [ -z "$FILE" ]; then
  echo "Geen gc*.json gevonden in $DOWNLOADS -- haal eerst de cijfers op met de bladwijzer/console (scripts/gc_bookmarklet.js)."
  exit 1
fi
AGE_MIN=$(( ( $(date +%s) - $(stat -f %m "$FILE" 2>/dev/null || stat -c %Y "$FILE") ) / 60 ))
echo "Bestand: $FILE (${AGE_MIN} min oud)"
if [ "$AGE_MIN" -gt 360 ]; then
  echo "Let op: het bestand is ouder dan 6 uur. Haal je verse cijfers op? (bestaande punten blijven toch staan, dit is dus ongevaarlijk)"
fi

python3 - "$FILE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
n = len(d)
latest = max((max(v) for v in d.values() if v), default=None)
print(f"{n} artiesten in het bestand, laatste datum {latest}")
if n < 16:
    print("WAARSCHUWING: minder dan 16 artiesten -- ontbrekende artiesten worden overgeslagen.")
PY

PYTHONPATH=scripts python3 scripts/backfill_global_charts.py "$FILE" --fresh-window

mkdir -p "$DOWNLOADS/gc_verwerkt"
mv "$FILE" "$DOWNLOADS/gc_verwerkt/gc_$(date +%Y%m%d_%H%M%S).json"

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "DRY_RUN: niet gecommit/gepusht."
  exit 0
fi

if git diff --quiet -- data/analysis_data.json; then
  echo "Geen nieuwe punten -- niets te committen."
  exit 0
fi
git add data/analysis_data.json
git commit -q -m "Update Global Charts data $(date -u '+%Y-%m-%d %H:%M') UTC (handmatig via Safari)"
git pull --rebase --autostash -q origin main
git push -q origin main
echo "Gepusht."
