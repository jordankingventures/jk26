"""
Kalshi daily view count -- geautomatiseerde versie voor Taylor Swift.

Draait via GitHub Actions (getriggerd door cron-job.org, zelfde patroon als
fetch_views.py). Bepaalt zelf, uit taylor_data.json, of de meest recent
afgesloten dag (gisteren, UTC-8) betrouwbaar berekend kan worden -- dezelfde
dag-afsluitlogica als computeDayInfo() in index.html -- en zo ja, plaatst
via kalshi_daily_mm.py's plan_orders_by_probability() een order per strike
die veilig genoeg is.

Prijs en strike-selectie komen uit een kansmodel (UCG-factor als Normaal-
verdeling, gemiddelde/stdev uit de historische punten): alleen strikes
waar de kans dat je fout zit onder RISK_THRESHOLD ligt worden gequote, en
de prijs schaalt mee met hoe diep een strike in die veilige zone zit --
van PRICE_FLOOR (net binnen de grens) tot PRICE_CAP (vrijwel zeker). Zie
strike_price_by_probability() in kalshi_daily_mm.py voor de precieze
formule.

Voordat er passief geboden wordt, wordt per strike eerst het orderboek
gecheckt: staat er al een tegenpartij die goedkoper is dan de fair value
(1 - p_wrong) min een veiligheidsmarge (TAKE_MARGIN), dan wordt die
meteen gepakt (kruisen, immediate-or-cancel) in plaats van passief te
bieden -- dat zet onzekere "misschien ooit gevuld"-orders om in directe,
gegarandeerde winst. Zie find_bargain_price() in kalshi_daily_mm.py.

Houdt bij welke datum al gequote is in kalshi_state.json (door de workflow
gecommit, zelfde patroon als de databestanden), zodat een herhaalde trigger
niet nogmaals dezelfde orders plaatst.

Veiligheid: standaard DRY RUN, ook als dit via de workflow draait. Pas als
de GitHub Actions secret KALSHI_DRY_RUN expliciet op "false" staat worden
er echt orders verstuurd. Dat moet je zelf aanzetten in GitHub (Settings ->
Secrets and variables -> Actions) als je klaar bent om live te gaan -- deze
code zet dat niet zelf aan.
"""

import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv

from kalshi_daily_mm import Environment, KalshiClient, build_event_ticker, find_bargain_price, load_private_key, plan_orders_by_probability

ARTIST_KEY = "taylor"
DATA_FILE = "data/taylor_data.json"
ANALYSIS_FILE = "data/analysis_data.json"
STATE_FILE = "data/kalshi_state.json"

CONTRACTS = 1
RISK_THRESHOLD = 0.01  # alleen strikes quoten met <1% kans dat je fout zit
PRICE_FLOOR = 0.55     # prijs vlak binnen de risicogrens (voorzichtig)
PRICE_CAP = 0.95       # prijs diep in de veilige zone (vrijwel zeker)
Z_RANGE = 2.0           # aantal extra standaarddeviaties boven de risicogrens tot PRICE_CAP bereikt wordt
TAKE_MARGIN = 0.03      # alleen een koopje pakken als het minstens dit veel goedkoper is dan fair value
# Orders blijven gewoon open staan tot de markt resolved (good_till_canceled
# zonder expiration_time) -- geen automatische vervaltijd.

DAY_TZ_OFFSET_HOURS = 8  # UTC-8, zie DAY_TZ in index.html / DAY_BOUNDARY_TZ in fetch_views.py


def floor1000(n: float) -> int:
    return int(n // 1000 * 1000)


def date_key_utc8(ts_iso: str) -> str:
    """Poort van ptDateKey() (index.html): op welke UTC-8-kalenderdag valt deze timestamp."""
    ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    shifted = ts - timedelta(hours=DAY_TZ_OFFSET_HOURS)
    return shifted.strftime("%Y-%m-%d")


def compute_day_total(log: list, date_key: str) -> Optional[int]:
    """Poort van computeDayInfo() + dayTotalFor() (index.html), voor één specifieke dag.

    Geeft alleen een getal terug als de dag écht afgesloten EN betrouwbaar is
    (dayOver=true, video_count niet gewisseld rond de grens, endTotal bekend)
    -- exact dezelfde voorwaarden als waaronder de UI een concreet cijfer
    i.p.v. een "--" toont. Anders None: dan is er simpelweg nog niets te doen.
    """
    pts = sorted(
        ({"ms": datetime.fromisoformat(e["ts"].replace("Z", "+00:00")).timestamp() * 1000,
          "pub": e["pub_total"], "vc": e["video_count"]} for e in log),
        key=lambda p: p["ms"],
    )
    if not pts:
        return None
    last_ms = pts[-1]["ms"]

    def interp_at(boundary_ms: float) -> Optional[float]:
        prev = None
        for p in pts:
            if p["ms"] <= boundary_ms:
                prev = p
                continue
            if prev is None:
                return None
            if prev["vc"] != p["vc"]:
                return None
            if p["ms"] == prev["ms"]:
                return prev["pub"]
            frac = (boundary_ms - prev["ms"]) / (p["ms"] - prev["ms"])
            return prev["pub"] + frac * (p["pub"] - prev["pub"])
        return None  # grens ligt na de laatste meting

    def vc_constant_between(lo_ms: float, hi_ms: float) -> bool:
        seen = {p["vc"] for p in pts if lo_ms <= p["ms"] <= hi_ms}
        return len(seen) <= 1

    y, m, d = (int(x) for x in date_key.split("-"))
    start_dt = datetime(y, m, d, tzinfo=timezone.utc) + timedelta(hours=DAY_TZ_OFFSET_HOURS)
    start_ms = start_dt.timestamp() * 1000
    end_ms = start_ms + 24 * 3600 * 1000

    day_over = end_ms <= last_ms
    if not day_over:
        return None  # dag loopt nog -- nog niets te doen

    valid = vc_constant_between(start_ms, min(end_ms, last_ms))
    if not valid:
        return None

    start_total = interp_at(start_ms)
    end_total = interp_at(end_ms)
    if start_total is None or end_total is None:
        return None

    return round(end_total - start_total)


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main():
    load_dotenv(override=True)  # .env is leidend, ook als een terminal-sessie zelf al een KALSHI_*-variabele had gezet -- moet als eerste, voor elke os.getenv("KALSHI_...") hieronder

    with open(DATA_FILE) as f:
        log = json.load(f)["log"]
    with open(ANALYSIS_FILE) as f:
        ucg = json.load(f)["ucg_factor"].get(ARTIST_KEY)

    if not ucg:
        sys.exit(f"Geen UCG-factor-historie voor '{ARTIST_KEY}' in {ANALYSIS_FILE} -- kan geen MIN/MAX bepalen.")

    target_date = (datetime.now(timezone.utc) - timedelta(hours=DAY_TZ_OFFSET_HOURS) - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Doeldag (gisteren, UTC-8): {target_date}")

    state = load_state()
    artist_state = state.setdefault(ARTIST_KEY, {})
    if target_date in artist_state:
        print(f"Deze dag is al eerder gequote ({artist_state[target_date].get('quoted_at')}) -- niets te doen.")
        return

    day_total = compute_day_total(log, target_date)
    if day_total is None:
        print("Dagtotaal voor deze dag is nog niet betrouwbaar berekenbaar (dag nog niet afgesloten, "
              "of catalogus-sprong rond de daggrens) -- probeer het later opnieuw.")
        return

    factors = [p["factor"] for p in ucg["points"]]
    mean_factor = statistics.mean(factors)
    std_factor = statistics.pstdev(factors)
    # Ter referentie/vergelijking met de oude simpele min/max-bandbreedte (niet meer
    # gebruikt voor de strike-selectie zelf, die gebeurt nu via het kansmodel).
    min_views = floor1000(day_total * ucg["min"])
    max_views = floor1000(day_total * ucg["max"])
    print(f"Dagtotaal (eigen counter): {day_total:,} | UCG-factor gem={mean_factor:.4f} std={std_factor:.4f} "
          f"(n={len(factors)}) | ter referentie MIN={min_views:,} MAX={max_views:,}")

    dry_run = (os.getenv("KALSHI_DRY_RUN", "true").strip().lower() != "false")
    print(f"Modus: {'DRY RUN (niets wordt verstuurd)' if dry_run else 'LIVE'}")

    env = Environment(os.getenv("KALSHI_ENV") or "demo")
    key_id = os.getenv("KALSHI_KEY_ID")
    if not key_id:
        sys.exit("Fout: KALSHI_KEY_ID ontbreekt (env/secrets).")
    private_key = load_private_key()
    client = KalshiClient(key_id, private_key, env)

    event_ticker = build_event_ticker(target_date)
    print(f"Omgeving: {env.value} | Event: {event_ticker}")

    try:
        markets = client.get_markets(event_ticker, status="open")
    except Exception as e:
        sys.exit(f"Kon markten niet ophalen voor {event_ticker}: {e}")

    if not markets:
        print(f"Geen open markten gevonden voor {event_ticker} -- niets te doen.")
        return

    plan = plan_orders_by_probability(
        markets, day_total, mean_factor, std_factor, CONTRACTS,
        risk_threshold=RISK_THRESHOLD, price_floor=PRICE_FLOOR, price_cap=PRICE_CAP, z_range=Z_RANGE,
    )
    print(f"{len(plan)} strikes veilig genoeg (risico < {RISK_THRESHOLD * 100:.1f}%).")

    results = []
    for item in plan:
        for o in item["orders"]:
            kant = "yes" if o["side"] == "bid" else "no"
            fair_value = 1 - o["p_wrong"]

            bargain_price = None
            try:
                orderbook = client.get_orderbook(item["ticker"])
                bargain_price = find_bargain_price(orderbook, kant, fair_value, TAKE_MARGIN)
            except Exception as e:
                print(f"  (orderboek ophalen mislukt voor {item['ticker']}, {e})")

            if dry_run:
                if bargain_price is not None:
                    print(f"  [DRY RUN] KOOPJE zou gepakt worden: {item['ticker']} {o['side']} @ {bargain_price:.2f} (fair value={fair_value:.3f})")
                else:
                    print(f"  [DRY RUN] passief bod: {item['ticker']} {o['side']} @ {o['price']:.2f} (p_fout={o['p_wrong']*100:.3f}%)")
                continue

            if bargain_price is not None:
                try:
                    r = client.create_order(item["ticker"], o["side"], bargain_price, o["count"], expiration_time=None,
                                             post_only=False, time_in_force="immediate_or_cancel")
                    if float(r.get("fill_count", 0)) > 0:
                        print(f"  KOOPJE GEPAKT {item['ticker']} {o['side']} @ {bargain_price:.2f} "
                              f"(fair value={fair_value:.3f}) -> order_id={r.get('order_id')} fill_count={r.get('fill_count')}")
                        results.append({"ticker": item["ticker"], "side": o["side"], "price": bargain_price, "count": o["count"],
                                         "p_wrong": o["p_wrong"], "order_id": r.get("order_id"), "taken": True})
                        continue
                    print(f"  (koopje op {item['ticker']} was al weg toen we kruisten -- val terug op passief bieden)")
                except Exception as e:
                    print(f"  (koopje pakken mislukt voor {item['ticker']}, {e} -- val terug op passief bieden)")

            try:
                r = client.create_order(item["ticker"], o["side"], o["price"], o["count"], expiration_time=None)
                print(f"  OK  {item['ticker']} {o['side']} @ {o['price']:.2f} (p_fout={o['p_wrong']*100:.3f}%) -> order_id={r.get('order_id')}")
                results.append({"ticker": item["ticker"], "side": o["side"], "price": o["price"], "count": o["count"], "p_wrong": o["p_wrong"], "order_id": r.get("order_id")})
            except Exception as e:
                print(f"  FOUT {item['ticker']} {o['side']} @ {o['price']:.2f} -> {e}")
                results.append({"ticker": item["ticker"], "side": o["side"], "price": o["price"], "count": o["count"], "p_wrong": o["p_wrong"], "error": str(e)})

    if dry_run:
        print("\n[DRY RUN] Er is niets verstuurd en de state is niet bijgewerkt.")
        return

    if not any(r.get("order_id") for r in results):
        print("Geen enkele order is gelukt -- deze dag NIET als afgehandeld vastleggen, zodat een volgende run het opnieuw probeert.")
        return

    artist_state[target_date] = {
        "quoted_at": datetime.now(timezone.utc).isoformat(),
        "day_total": day_total,
        "mean_factor": round(mean_factor, 4),
        "std_factor": round(std_factor, 4),
        "risk_threshold": RISK_THRESHOLD,
        "min_views": min_views,
        "max_views": max_views,
        "strikes_quoted": len(plan),
        "orders": results,
    }
    save_state(state)


if __name__ == "__main__":
    main()
