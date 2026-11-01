"""
YouTube View Tracker -- data_status.py
Schrijft per artiest een klein bestand data/<artiest>_status.json met de
tijdstippen (unix-minuten) van de metingen uit de laatste 10 dagen, zodat de
pagina (index.html) kan laten zien als er metingen ontbreken -- zonder per
artiest het grote data-bestand te hoeven laden. Wordt door fetch_views.py na
elke geslaagde run bijgewerkt; blijft een artiest uitvallen, dan blijft het
bestand staan en ziet de pagina aan de oude laatste meting dat er iets mis is.

CLI (eenmalig/handmatig voor alle artiesten):  python3 scripts/data_status.py
"""

import glob
import json
import os
from datetime import datetime, timedelta, timezone

KEEP_DAYS = 10


def status_path(data_file):
    return data_file.replace("_data.json", "_status.json")


def write_status(data_file, log):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=KEEP_DAYS)
    minutes = []
    for e in log:
        try:
            t = datetime.strptime(e["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if t >= cutoff:
            minutes.append(int(t.timestamp() // 60))
    minutes.sort()
    with open(status_path(data_file), "w") as f:
        json.dump({"updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "ts": minutes}, f, separators=(",", ":"))
        f.write("\n")
    return len(minutes)


if __name__ == "__main__":
    for path in sorted(glob.glob("data/*_data.json")):
        if os.path.basename(path).startswith("analysis"):
            continue
        with open(path) as f:
            log = json.load(f).get("log", [])
        print(f"{path}: {write_status(path, log)} metingen")
