"""
YouTube View Tracker — fetch_global_charts.py
Draait via GitHub Actions (1x per dag), net als fetch_views.py.

Haalt per artiest de dagelijkse "Global Charts"-totalen op via de interne
JSON-API die charts.youtube.com zelf gebruikt om de 28-dagen-grafiek op een
artiestpagina te vullen (POST naar youtubei/v1/browse, geen browser of
API-key nodig -- publiek toegankelijk, ontdekt door het netwerkverkeer van
de site zelf te bekijken). Combineert dat met het eigen dagtotaal uit
data/<artiest>_data.json (final_day_total in het log, al bijgehouden door
fetch_views.py) tot de UCG-factor-historie in data/analysis_data.json.

Venster is bewust kort (WINDOW_DAYS): Kalshi resolveert op de eerst
beschikbare Global Charts-waarde voor een dag, niet op een eventuele latere
herziening (Global Charts verwerkt nieuwe-video-views soms 1-2 dagen
vertraagd, zie de UCG-factor-analyse in de Voorspelling-tab). We willen dus
juist de waarde vastleggen zoals die er kort na publicatie uitziet -- niet
oude dagen blijven overschrijven met een later "definitiever" cijfer dat qua
timing niet meer overeenkomt met wanneer de markt al resolvede.

De entity-ID's hieronder zijn Google Knowledge Graph-ID's (/m/... of /g/...),
niet de YouTube-kanaal-ID's uit fetch_views.py -- charts.youtube.com adresseert
artiesten op deze manier. Eenmalig opgezocht en geverifieerd door de
opgehaalde channelId in de API-respons te vergelijken met de bekende
kanaal-ID per artiest.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

USER_AGENT = "Mozilla/5.0 (compatible; PersonalViewTracker/1.0)"
WINDOW_DAYS = 2
ANALYSIS_FILE = "data/analysis_data.json"

# Zelfde vaste dag-grens als fetch_views.py's DAY_BOUNDARY_TZ, zodat een dag
# hier en daar exact hetzelfde blok uren dekt.
DAY_BOUNDARY_TZ = timezone(timedelta(hours=-8))

ARTISTS = {
    "taylor":       {"entity_id": "/m/0dl567",     "data_file": "data/taylor_data.json"},
    "drake":        {"entity_id": "/m/05mt_q",     "data_file": "data/drake_data.json"},
    "wallen":       {"entity_id": "/g/11g7ntnqs5", "data_file": "data/wallen_data.json"},
    "badbunny":     {"entity_id": "/g/11gdq15782", "data_file": "data/badbunny_data.json"},
    "ariana":       {"entity_id": "/m/09gkdy4",    "data_file": "data/ariana_data.json"},
    "youngboy":     {"entity_id": "/g/11g9nh3b7y", "data_file": "data/youngboy_data.json"},
    "bieber":       {"entity_id": "/m/06w2sn5",    "data_file": "data/bieber_data.json"},
    "fuerzaregida": {"entity_id": "/g/11fy19q209", "data_file": "data/fuerzaregida_data.json"},
    "future":       {"entity_id": "/m/0hhwdgn",    "data_file": "data/future_data.json"},
    "kanye":        {"entity_id": "/m/02l840",     "data_file": "data/kanye_data.json"},
    "katseye":      {"entity_id": "/g/11vj6xh7_6", "data_file": "data/katseye_data.json"},
    "kendrick":     {"entity_id": "/m/0g9x698",    "data_file": "data/kendrick_data.json"},
    "olivia":       {"entity_id": "/g/11j0_8y5xw", "data_file": "data/olivia_data.json"},
    "postmalone":   {"entity_id": "/g/11bw82cs0m", "data_file": "data/postmalone_data.json"},
    "tatemcrae":    {"entity_id": "/g/11cs4tyyr8", "data_file": "data/tatemcrae_data.json"},
    "weeknd":       {"entity_id": "/m/0gjdn4c",    "data_file": "data/weeknd_data.json"},
}


def fetch_daily_views(entity_id, start_date, end_date):
    """{datum: viewCount} voor [start_date, end_date] via charts.youtube.com's interne browse-API."""
    body = {
        "context": {
            "client": {
                "clientName": "WEB_MUSIC_ANALYTICS",
                "clientVersion": "2.0",
                "hl": "en",
                "gl": "US",
                "experimentIds": [],
                "experimentsToken": "",
                "theme": "MUSIC",
            }
        },
        "browseId": "FEmusic_analytics_insights_artist",
        "query": (
            "perspective=ARTIST&entity_params_entity=ARTIST"
            f"&artist_params_id={urllib.parse.quote(entity_id, safe='')}"
            f"&date_params_start_time={start_date}T00:00:00Z"
            f"&date_params_end_time={end_date}T00:00:00Z"
            "&date_params_interval=DAY"
        ),
    }
    req = urllib.request.Request(
        "https://charts.youtube.com/youtubei/v1/browse?alt=json",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    content = data["contents"]["sectionListRenderer"]["contents"][0]["musicAnalyticsSectionRenderer"]["content"]
    date_views = content.get("dates", [{}])[0].get("dateViews", [])
    return {d["date"]: int(d["viewCount"]) for d in date_views}


def own_counter_by_date(data_file):
    """Datum (UTC-8) -> definitief dagtotaal, uit het log dat fetch_views.py bijhoudt."""
    if not os.path.exists(data_file):
        return {}
    data = json.loads(open(data_file).read())
    result = {}
    for entry in data.get("log", []):
        total = entry.get("final_day_total")
        if total is None:
            continue
        ts = datetime.strptime(entry["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        date_key = ts.astimezone(DAY_BOUNDARY_TZ).strftime("%Y-%m-%d")
        result[date_key] = total
    return result


def floor1000(n):
    return (n // 1000) * 1000


def main():
    analysis = json.loads(open(ANALYSIS_FILE).read()) if os.path.exists(ANALYSIS_FILE) else {"note": "", "ucg_factor": {}}
    analysis.setdefault("ucg_factor", {})

    now = datetime.now(timezone.utc)
    end_date = now.strftime("%Y-%m-%d")
    start_date = (now - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")

    for key, info in ARTISTS.items():
        print(f"[{key}] Global Charts ophalen ({start_date} t/m {end_date})...")
        try:
            gc_by_date = fetch_daily_views(info["entity_id"], start_date, end_date)
        except urllib.error.HTTPError as e:
            print(f"  FOUT: HTTP {e.code} -- {e.read()[:300]}")
            continue
        except Exception as e:
            print(f"  FOUT: {e}")
            continue

        counter_by_date = own_counter_by_date(info["data_file"])
        existing_points = analysis["ucg_factor"].get(key, {}).get("points", [])
        points_by_date = {p["date"]: p for p in existing_points}

        updated = 0
        for date, gc_raw in gc_by_date.items():
            counter_raw = counter_by_date.get(date)
            if counter_raw is None:
                continue  # eigen counter heeft deze dag nog niet afgesloten
            gc = floor1000(gc_raw)
            counter = floor1000(counter_raw)
            if counter == 0:
                continue
            points_by_date[date] = {
                "date": date,
                "own_counter": counter,
                "global_charts": gc,
                "factor": gc / counter,
            }
            updated += 1

        points = sorted(points_by_date.values(), key=lambda p: p["date"])
        factors = [p["factor"] for p in points]
        analysis["ucg_factor"][key] = {
            "points": points,
            "min": min(factors) if factors else None,
            "max": max(factors) if factors else None,
            "avg": sum(factors) / len(factors) if factors else None,
        }
        print(f"  {updated} dagen bijgewerkt binnen het venster, {len(points)} dagen totaal.")

    analysis["note"] = (
        "UCG-factor-historie automatisch bijgehouden door scripts/fetch_global_charts.py "
        "(GitHub Actions, dagelijks): eigen counter (data/<artiest>_data.json) vs. YouTube "
        "Global Charts (interne JSON-API van charts.youtube.com), per dag per artiest. Elke "
        f"run legt alleen de laatste {WINDOW_DAYS} dagen vast (de eerst beschikbare "
        "Global Charts-waarde -- dezelfde waarde waarop Kalshi resolveert), en laat oudere "
        "dagen ongemoeid zodat een latere Global Charts-herziening niet met terugwerkende "
        "kracht wordt overgenomen."
    )

    with open(ANALYSIS_FILE, "w") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    main()
