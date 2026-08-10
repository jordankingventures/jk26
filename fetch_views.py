"""
YouTube View Tracker — fetch_views.py
Draait via GitHub Actions (getriggerd door cron-job.org, elke ~15 min).
Haalt views op van alle Taylor Swift video's over meerdere kanalen heen
en slaat op in yt_data.json.
"""

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

API_KEY    = os.environ["YOUTUBE_API_KEY"]
DATA_FILE  = "yt_data.json"
CACHE_FILE = "yt_video_ids.json"
# YouTube Charts (de Kalshi-resolver) hanteert Pacific Time als daggrens, niet UTC.
PT = ZoneInfo("America/Los_Angeles")

# Alle kanalen die bij deze artiest horen. Het "Topic"-kanaal is een door
# YouTube/het label auto-gegenereerd kanaal voor officiële audio (incl.
# remixen en alternatieve versies) dat volledig los staat van het
# hoofdkanaal — eigen video-ID's, eigen viewcounters, en werd hiervoor
# helemaal niet meegeteld.
CHANNELS = [
    {"id": "UCqECaJ8Gagnn7YCbPEzWH6g", "label": "Hoofdkanaal"},
    {"id": "UCPC0L1d253x-KuMNwa05TpA", "label": "Topic"},
]


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
    """
    curr_views = {v["id"]: v["views"] for v in videos}

    if not last_snapshot:
        return {"date": today, "views": curr_views, "interpolated": False}

    try:
        prev_ts_utc = datetime.strptime(last_snapshot["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return {"date": today, "views": curr_views, "interpolated": False}

    midnight_utc = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=PT).astimezone(timezone.utc)
    span_seconds = (now_utc - prev_ts_utc).total_seconds()

    if span_seconds <= 0:
        return {"date": today, "views": curr_views, "interpolated": False}

    frac = (midnight_utc - prev_ts_utc).total_seconds() / span_seconds
    frac = max(0.0, min(1.0, frac))  # clamp: bij een gemiste dag of rare gap niet extrapoleren

    prev_views = last_snapshot.get("views", {})
    estimated = {}
    for vid, v_now in curr_views.items():
        v_prev = prev_views.get(vid)
        estimated[vid] = v_now if v_prev is None else round(v_prev + frac * (v_now - v_prev))

    return {
        "date":          today,
        "views":         estimated,
        "interpolated":  True,
        "gap_minutes":   round(span_seconds / 60, 1),
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
        data["day_baseline"] = estimate_midnight_baseline(videos, data.get("last_snapshot"), today, now_utc)

    # Nieuwe video-ID's (net gepubliceerd, of nieuw toegevoegd aan tracking, bv. een extra
    # kanaal) missen nog een baseline-waarde voor vandaag. Zonder aanvulling zou hun
    # volledige bestaande viewcount ten onrechte als "groei vandaag" meetellen. Start ze
    # op hun huidige stand, zodat ze vanaf nu meetellen maar niet met terugwerkende kracht.
    for vid, v_now in curr_views.items():
        if vid not in data["day_baseline"]["views"]:
            data["day_baseline"]["views"][vid] = v_now

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
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Start")

    channel_ids   = [c["id"] for c in CHANNELS]
    channels_info = get_channels_info(channel_ids)

    cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            cache = json.loads(open(CACHE_FILE).read())
        except Exception:
            cache = {}

    all_ids = []
    for c in CHANNELS:
        info = channels_info[c["id"]]
        all_ids.extend(get_video_ids(c["id"], info["uploads_playlist_id"], cache))

    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)

    # Dedupliceren voor het geval een video-ID toch in meerdere kanalen voorkomt.
    unique_ids = list(dict.fromkeys(all_ids))

    videos         = get_video_details(unique_ids)
    pub_total      = sum(v["views"] for v in videos)
    official_total = sum(info["official_view_count"] for info in channels_info.values())

    print(f"  Officieel totaal (alle kanalen): {official_total:,}")
    print(f"  Publieke video's               : {pub_total:,}")
    print(f"  Aantal video's                 : {len(videos)}")
    save_snapshot(videos, official_total)
    print("[OK]")
