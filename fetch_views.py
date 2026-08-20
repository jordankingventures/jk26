"""
YouTube View Tracker — fetch_views.py
Draait via GitHub Actions (getriggerd door cron-job.org, elke ~15 min).
Haalt views op van alle video's van een artiest over meerdere kanalen heen
(hoofdkanaal + het door YouTube/het label auto-gegenereerde "Topic"-kanaal
voor officiële audio, incl. remixen en alternatieve versies -- staat
volledig los van het hoofdkanaal, eigen video-ID's, eigen viewcounters)
en slaat op in <artiest>_data.json.

Gebruik: python fetch_views.py <artiest>   (bv. "taylor" of "drake")
"""

import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

API_KEY = os.environ["YOUTUBE_API_KEY"]
# YouTube Charts (de Kalshi-resolver) hanteert Pacific Time als daggrens, niet UTC.
PT = ZoneInfo("America/Los_Angeles")

# Eén set fetch-logica voor alle artiesten; alleen kanalen en bestandsnamen
# verschillen per artiest, zodat een bugfix maar op één plek hoeft.
ARTISTS = {
    "taylor": {
        "channels": [
            {"id": "UCqECaJ8Gagnn7YCbPEzWH6g", "label": "Hoofdkanaal"},
            {"id": "UCPC0L1d253x-KuMNwa05TpA", "label": "Topic"},
        ],
        "data_file":  "yt_data.json",
        "cache_file": "yt_video_ids.json",
    },
    "drake": {
        "channels": [
            {"id": "UCByOQJjav0CUDwxCk-jVNRQ", "label": "Hoofdkanaal"},
            {"id": "UCU6cE7pdJPc6DU2jSrKEsdQ", "label": "Topic"},
        ],
        "data_file":  "drake_data.json",
        "cache_file": "drake_video_ids.json",
    },
    "wallen": {
        "channels": [
            {"id": "UCzIyoPv6j1MAZpDHKLGP_eA", "label": "Hoofdkanaal"},
            {"id": "UC5xaQ6_dP7EGDmGLzVGZ1Ow", "label": "Topic"},
        ],
        "data_file":  "wallen_data.json",
        "cache_file": "wallen_video_ids.json",
    },
    "badbunny": {
        "channels": [
            {"id": "UCmBA_wu8xGg1OfOkfW13Q0Q", "label": "Hoofdkanaal"},
            {"id": "UCiY3z8HAGD6BlSNKVn2kSvQ", "label": "Topic"},
        ],
        "data_file":  "badbunny_data.json",
        "cache_file": "badbunny_video_ids.json",
    },
    "ariana": {
        "channels": [
            {"id": "UC9CoOnJkIBMdeijd9qYoT_g", "label": "Hoofdkanaal"},
            {"id": "UC0076UMUgEng8HORUw_MYHA", "label": "Topic"},
        ],
        "data_file":  "ariana_data.json",
        "cache_file": "ariana_video_ids.json",
    },
    "youngboy": {
        "channels": [
            {"id": "UClW4jraMKz6Qj69lJf-tODA", "label": "Hoofdkanaal"},
            {"id": "UCR28YDxjDE3ogQROaNdnRbQ", "label": "Topic"},
        ],
        "data_file":  "youngboy_data.json",
        "cache_file": "youngboy_video_ids.json",
    },
    "bieber": {
        "channels": [
            {"id": "UCIwFjwMjI0y7PDBVEO9-bkQ", "label": "Hoofdkanaal"},
            {"id": "UCGvj8kfUV5Q6lzECIrGY19g", "label": "Topic"},
        ],
        "data_file":  "bieber_data.json",
        "cache_file": "bieber_video_ids.json",
    },
}

# Worden in __main__ gezet op basis van het artiest-argument.
CHANNELS   = None
DATA_FILE  = None
CACHE_FILE = None


def yt_get(endpoint, params):
    params["key"] = API_KEY
    url = f"https://www.googleapis.com/youtube/v3/{endpoint}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read())
    if "error" in data:
        raise RuntimeError(data["error"]["message"])
    return data


def get_channels_info(channel_ids):
    """Eén API-call voor alle kanalen tegelijk (channels.list accepteert een lijst ID's, kost 1 unit)."""
    data = yt_get("channels", {"id": ",".join(channel_ids), "part": "contentDetails,statistics"})
    info = {}
    for item in data["items"]:
        info[item["id"]] = {
            "uploads_playlist_id": item["contentDetails"]["relatedPlaylists"]["uploads"],
            "official_view_count": int(item["statistics"]["viewCount"]),
        }
    return info


def get_video_ids(channel_id, uploads_playlist_id, cache):
    """Video-ID's van de uploads-playlist van één kanaal, per kanaal 1x per PT-dag gecachet."""
    today = datetime.now(PT).strftime("%Y-%m-%d")

    cached = cache.get(channel_id)
    if cached and cached.get("date") == today:
        print(f"  [{channel_id}] Video IDs uit cache: {len(cached['ids'])}")
        return cached["ids"]

    ids, page_token = [], None
    while True:
        params = {"playlistId": uploads_playlist_id, "part": "contentDetails", "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        data = yt_get("playlistItems", params)
        for item in data.get("items", []):
            ids.append(item["contentDetails"]["videoId"])
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    cache[channel_id] = {"date": today, "ids": ids}
    print(f"  [{channel_id}] Video IDs opgehaald: {len(ids)}")
    return ids


def get_video_details(ids):
    videos = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i+50]
        data = yt_get("videos", {"id": ",".join(chunk), "part": "snippet,statistics"})
        for item in data.get("items", []):
            videos.append({
                "id":           item["id"],
                "title":        item["snippet"]["title"],
                "thumbnail":    item["snippet"].get("thumbnails", {}).get("default", {}).get("url", ""),
                "published_at": item["snippet"].get("publishedAt", ""),
                "views":        int(item["statistics"].get("viewCount", 0)),
            })
    return videos


def estimate_midnight_baseline(videos, last_snapshot, today, now_utc):
    """
    Schat de views per video op exact 00:00 Pacific Time van `today`.
    Metingen komen onregelmatig binnen (elke ~1-2 uur, soms mislukt een run), dus de
    eerste meting na middernacht ligt meestal al wat na 00:00 en bevat dan groei die
    anders ten onrechte bij 'gisteren' zou horen. We interpoleren lineair tussen de
    laatste meting vóór middernacht (last_snapshot) en de eerste erna (videos/now_utc)
    naar het exacte 00:00-tijdstip.

    Retourneert ook 'prev_day_end_total': een schatting van het totaal op datzelfde
    middernacht-moment, maar dan opgeteld over uitsluitend de video's die de vorige
    dag al zelf volgde (dus zonder de bestaande viewcount van nieuw ontdekte video's
    mee te tellen als groei -- de dagelijkse video-lijst-verversing ontdekt bijna
    elke dag oudere catalogus-video's, of ziet er juist eentje verdwijnen; dat mag
    het dagtotaal van de afgelopen dag niet vervuilen). Video's die inmiddels uit de
    scan zijn verdwenen tellen mee met hun laatst bekende waarde.
    """
    curr_views = {v["id"]: v["views"] for v in videos}

    if not last_snapshot:
        return {"date": today, "views": curr_views, "interpolated": False, "prev_day_end_total": None}

    try:
        prev_ts_utc = datetime.strptime(last_snapshot["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return {"date": today, "views": curr_views, "interpolated": False, "prev_day_end_total": None}

    midnight_utc = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=PT).astimezone(timezone.utc)
    span_seconds = (now_utc - prev_ts_utc).total_seconds()

    if span_seconds <= 0:
        return {"date": today, "views": curr_views, "interpolated": False, "prev_day_end_total": None}

    frac = (midnight_utc - prev_ts_utc).total_seconds() / span_seconds
    frac = max(0.0, min(1.0, frac))  # clamp: bij een gemiste dag of rare gap niet extrapoleren

    prev_views = last_snapshot.get("views", {})
    estimated = {}
    for vid, v_now in curr_views.items():
        v_prev = prev_views.get(vid)
        estimated[vid] = v_now if v_prev is None else round(v_prev + frac * (v_now - v_prev))

    prev_day_end_total = 0
    for vid, v_prev in prev_views.items():
        prev_day_end_total += estimated[vid] if vid in estimated else v_prev

    return {
        "date":               today,
        "views":              estimated,
        "interpolated":       True,
        "gap_minutes":        round(span_seconds / 60, 1),
        "prev_day_end_total": prev_day_end_total,
    }


def save_snapshot(videos, official_total):
    data = {"log": [], "day_baseline": None, "last_snapshot": None, "videos": []}
    if os.path.exists(DATA_FILE):
        try:
            data = json.loads(open(DATA_FILE).read())
        except:
            pass

    pub_total  = sum(v["views"] for v in videos)
    now_utc    = datetime.now(timezone.utc)
    ts         = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    today      = now_utc.astimezone(PT).strftime("%Y-%m-%d")  # PT-dag, matcht charts.youtube.com
    curr_views = {v["id"]: v["views"] for v in videos}

    # Dag-baseline: reset elke dag op middernacht Pacific Time, geschat via interpolatie.
    if not data["day_baseline"] or data["day_baseline"].get("date") != today:
        old_baseline = data["day_baseline"]
        new_baseline = estimate_midnight_baseline(videos, data.get("last_snapshot"), today, now_utc)

        # De zojuist afgesloten dag krijgt met terugwerkende kracht zijn echte,
        # complete dagtotaal op de laatste logregel: inclusief het stukje groei
        # tot middernacht, maar zonder vervuiling door nieuw ontdekte (of net
        # verdwenen) video's. Zo hangt de juistheid niet af van hoe precies de
        # cron rond middernacht getimed is. We bewaren ook het absolute geschatte
        # eindtotaal (en het aantal video's waarover dat gaat) zelf, zodat de
        # 00:00-regel in de UI dat kan tonen zonder de contaminatie van de
        # volgende dag se (grotere of kleinere) videoset.
        if old_baseline and data["log"] and new_baseline.get("prev_day_end_total") is not None:
            old_base_total = sum(old_baseline["views"].values())
            data["log"][0]["final_day_total"] = new_baseline["prev_day_end_total"] - old_base_total
            data["log"][0]["final_day_pub_total"] = new_baseline["prev_day_end_total"]
            data["log"][0]["final_day_video_count"] = len(old_baseline["views"])
            data["log"][0]["final_day_interpolated"] = new_baseline.get("interpolated", False)
            data["log"][0]["final_day_gap_minutes"] = new_baseline.get("gap_minutes")

        data["day_baseline"] = new_baseline

    # Nieuwe video-ID's (net gepubliceerd, of nieuw toegevoegd aan tracking, bv. een extra
    # kanaal) missen nog een baseline-waarde voor vandaag. Zonder aanvulling zou hun
    # volledige bestaande viewcount ten onrechte als "groei vandaag" meetellen. Start ze
    # op hun huidige stand, zodat ze vanaf nu meetellen maar niet met terugwerkende kracht.
    for vid, v_now in curr_views.items():
        if vid not in data["day_baseline"]["views"]:
            data["day_baseline"]["views"][vid] = v_now

    # Omgekeerd geval, midden op de dag (dus los van de dagwissel hierboven):
    # video's die vandaag al meetelden maar nu even niet meer in de scan
    # voorkomen (tijdelijk API-hikje, of een video die net verdwenen is). Hun
    # oude baseline-waarde blijft anders staan terwijl pub_total ze niet meer
    # meetelt -- dan wordt de aftrekking te groot en duikt views_today
    # vals-negatief. Verwijder ze uit de baseline, symmetrisch aan hierboven.
    for vid in list(data["day_baseline"]["views"].keys()):
        if vid not in curr_views:
            del data["day_baseline"]["views"][vid]

    base_total  = sum(data["day_baseline"]["views"].values())
    views_today = pub_total - base_total

    # Zelfde probleem kan delta_hour raken: een video die er sinds de vorige meting
    # nieuw bij kwam, zou anders in één klap als "groei dit uur" meetellen.
    if data["last_snapshot"]:
        prev_views = data["last_snapshot"]["views"]
        prev_total = sum(prev_views.get(vid, v_now) for vid, v_now in curr_views.items())
    else:
        prev_total = None
    delta_hour = pub_total - prev_total if prev_total is not None else None

    # Log entry toevoegen
    data["log"].insert(0, {
        "ts":             ts,
        "official_total": official_total,
        "pub_total":      pub_total,
        "video_count":    len(videos),
        "views_today":    views_today,
        "delta_hour":     delta_hour,
    })
    data["log"] = data["log"][:200]

    # Laatste snapshot bewaren
    data["last_snapshot"] = {
        "ts":    ts,
        "views": {v["id"]: v["views"] for v in videos},
    }

    # Video lijst opslaan (gesorteerd op views)
    videos.sort(key=lambda v: v["views"], reverse=True)
    data["videos"] = videos

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"  Opgeslagen in {DATA_FILE}")


if __name__ == "__main__":
    artist_key = sys.argv[1] if len(sys.argv) > 1 else "taylor"
    if artist_key not in ARTISTS:
        raise SystemExit(f"Onbekende artiest '{artist_key}', kies uit: {', '.join(ARTISTS)}")
    artist     = ARTISTS[artist_key]
    CHANNELS   = artist["channels"]
    DATA_FILE  = artist["data_file"]
    CACHE_FILE = artist["cache_file"]

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Start ({artist_key})")

    channel_ids   = [c["id"] for c in CHANNELS]
    channels_info = get_channels_info(channel_ids)

    cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            cache = json.loads(open(CACHE_FILE).read())
        except Exception:
            cache = {}

    all_ids = []
    id_to_channel = {}
    for c in CHANNELS:
        info = channels_info[c["id"]]
        ids  = get_video_ids(c["id"], info["uploads_playlist_id"], cache)
        all_ids.extend(ids)
        for vid in ids:
            id_to_channel.setdefault(vid, c["label"])  # eerste kanaal in CHANNELS wint bij overlap

    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)

    # Dedupliceren voor het geval een video-ID toch in meerdere kanalen voorkomt.
    unique_ids = list(dict.fromkeys(all_ids))

    videos = get_video_details(unique_ids)
    for v in videos:
        v["channel"] = id_to_channel.get(v["id"], "Onbekend")
    pub_total      = sum(v["views"] for v in videos)
    official_total = sum(info["official_view_count"] for info in channels_info.values())

    print(f"  Officieel totaal (alle kanalen): {official_total:,}")
    print(f"  Publieke video's               : {pub_total:,}")
    print(f"  Aantal video's                 : {len(videos)}")
    save_snapshot(videos, official_total)
    print("[OK]")
