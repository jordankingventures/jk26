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

API_KEY    = os.environ["YOUTUBE_API_KEY"]
CHANNEL_ID = "UCqECaJ8Gagnn7YCbPEzWH6g"
DATA_FILE  = "yt_data.json"
CACHE_FILE = "yt_video_ids.json"


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
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

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


def save_snapshot(videos, official_total):
    data = {"log": [], "day_baseline": None, "last_snapshot": None, "videos": []}
    if os.path.exists(DATA_FILE):
        try:
            data = json.loads(open(DATA_FILE).read())
        except:
            pass

    pub_total = sum(v["views"] for v in videos)
    ts        = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today     = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Dag-baseline: reset elke dag
    if not data["day_baseline"] or data["day_baseline"].get("date") != today:
        data["day_baseline"] = {
            "date":  today,
            "views": {v["id"]: v["views"] for v in videos},
        }

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
