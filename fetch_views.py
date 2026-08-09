"""
YouTube View Tracker — fetch_views.py
Draait elk uur via GitHub Actions.
Haalt views op van alle Taylor Swift video's en slaat op in yt_data.json.
"""

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

API_KEY    = os.environ["YOUTUBE_API_KEY"]
CHANNEL_ID = "UCqECaJ8Gagnn7YCbPEzWH6g"
DATA_FILE  = "yt_data.json"
CACHE_FILE = "yt_video_ids.json"
# YouTube Charts (de Kalshi-resolver) hanteert Pacific Time als daggrens, niet UTC.
PT = ZoneInfo("America/Los_Angeles")


def yt_get(endpoint, params):
    params["key"] = API_KEY
    url = f"https://www.googleapis.com/youtube/v3/{endpoint}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read())
    if "error" in data:
        raise RuntimeError(data["error"]["message"])
    return data


def get_channel_info():
    data = yt_get("channels", {"id": CHANNEL_ID, "part": "contentDetails,statistics"})
    item = data["items"][0]
    return {
        "uploads_playlist_id": item["contentDetails"]["relatedPlaylists"]["uploads"],
        "official_view_count":  int(item["statistics"]["viewCount"]),
    }


def get_video_ids(uploads_playlist_id):
    today = datetime.now(PT).strftime("%Y-%m-%d")

    if os.path.exists(CACHE_FILE):
        cache = json.loads(open(CACHE_FILE).read())
        if cache.get("date") == today and cache.get("channel_id") == CHANNEL_ID:
            print(f"  Video IDs uit cache: {len(cache['ids'])}")
            return cache["ids"]

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

    with open(CACHE_FILE, "w") as f:
        json.dump({"date": today, "channel_id": CHANNEL_ID, "ids": ids}, f)
    print(f"  Video IDs opgehaald: {len(ids)}")
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

    pub_total = sum(v["views"] for v in videos)
    now_utc   = datetime.now(timezone.utc)
    ts        = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    today     = now_utc.astimezone(PT).strftime("%Y-%m-%d")  # PT-dag, matcht charts.youtube.com

    # Dag-baseline: reset elke dag op middernacht Pacific Time, geschat via interpolatie.
    if not data["day_baseline"] or data["day_baseline"].get("date") != today:
        data["day_baseline"] = estimate_midnight_baseline(videos, data.get("last_snapshot"), today, now_utc)

    base_total  = sum(data["day_baseline"]["views"].values())
    views_today = pub_total - base_total

    prev_total  = sum(data["last_snapshot"]["views"].values()) if data["last_snapshot"] else None
    delta_hour  = pub_total - prev_total if prev_total is not None else None

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
    channel = get_channel_info()
    ids     = get_video_ids(channel["uploads_playlist_id"])
    videos  = get_video_details(ids)
    pub_total = sum(v["views"] for v in videos)
    print(f"  Officieel totaal : {channel['official_view_count']:,}")
    print(f"  Publieke video's : {pub_total:,}")
    print(f"  Aantal video's   : {len(videos)}")
    save_snapshot(videos, channel["official_view_count"])
    print("[OK]")
