#!/usr/bin/env bash
# Draait in de GitHub Action "Verwerk Global Charts-upload": verwerkt
# data/incoming/gc.json, commit het resultaat, haalt het geuploade bestand weg
# en pusht. Los van de workflow-YAML zodat het lokaal te testen is.
#
# Het geuploade bestand wordt altijd verwijderd (ook als de controle het
# afkeurt), zodat je de volgende keer gewoon weer een nieuw "gc.json" kunt
# uploaden. Bij afkeuring faalt de run (rode run + mail van GitHub).
set -uo pipefail

INCOMING="data/incoming/gc.json"
if [ ! -f "$INCOMING" ]; then
  echo "Geen $INCOMING aanwezig -- niets te doen."
  exit 0
fi

WORK="${RUNNER_TEMP:-/tmp}"
cp "$INCOMING" "$WORK/gc_upload.json"

git config user.name  "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

status=1
for i in $(seq 1 20); do
  # Elke poging vanaf de nieuwste main (de 5-minuten-workflows pushen daar
  # ook), en de verwerking opnieuw doen -- idempotent, dus geen rebase-gedoe
  # op analysis_data.json.
  git fetch -q origin main && git reset -q --hard origin/main

  if python3 scripts/process_gc_upload.py "$WORK/gc_upload.json"; then status=0; else status=1; fi

  git add data/analysis_data.json
  git rm -q -f --ignore-unmatch "$INCOMING"
  if git diff --staged --quiet; then
    echo "Geen wijzigingen om te committen."
    break
  fi
  if [ "$status" -eq 0 ]; then
    git commit -q -m "Update Global Charts data $(date -u '+%Y-%m-%d %H:%M') UTC (upload via GitHub)"
  else
    git commit -q -m "Global Charts-upload afgekeurd en verwijderd (zie de Actions-run)"
  fi
  if git push -q origin HEAD:main; then
    echo "Gepusht."
    break
  fi
  echo "Push mislukt (poging $i/20) -- main is intussen gewijzigd, opnieuw proberen..."
  sleep $((RANDOM % 12 + 3))
done

if [ "$status" -ne 0 ]; then
  echo "::error::gc.json is niet verwerkt (zie de foutmeldingen hierboven)."
fi
exit "$status"
