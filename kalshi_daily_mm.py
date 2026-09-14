"""
Kalshi daily view count -- simpele two-sided market-making bot (v1, Taylor Swift).

Idee: op de Kalshi 'daily view count' markt (serie KXYTVIEWSD) bestaat per
artiest per dag een ladder van strikes ("Above 13.0M", "Above 13.1M", ...).
Voor strikes ONDER je MIN UCG-schatting is YES vrijwel zeker; voor strikes
BOVEN je MAX UCG-schatting is NO vrijwel zeker. Dit script plaatst op elk
van die strikes buiten je bandbreedte een tweezijdige limiet-quote (een
bied op de zeer waarschijnlijke kant tegen een kleine korting op de "zekere"
prijs, en een bied op de onwaarschijnlijke kant tegen een kleine korting op
de "vrijwel-nul" prijs) -- de klassieke manier om als market maker de
spread te verdienen in plaats van als market taker de brede spread te
moeten oversteken.

Bewust simpel gehouden voor deze eerste test: de MIN/MAX (in views) geef je
zelf mee als argument -- dezelfde getallen die je al op het Voorspelling-
tabblad ziet bij "MIN UCG" / "MAX UCG" voor de dag die net is afgesloten.
Dit script rekent dus niets opnieuw uit op basis van je eigen counter-data;
het regelt alleen de Kalshi-kant (strikes opzoeken, prijzen bepalen, orders
plaatsen). Dat kan later verder geautomatiseerd worden.

Vereist:
    pip install requests cryptography python-dotenv

Env variabelen (zet in een lokaal .env bestand, NOOIT in dit script of in git):
    KALSHI_KEY_ID      - je Kalshi API key ID
    KALSHI_PRIVATE_KEY - de volledige inhoud van je RSA private key (incl.
                         -----BEGIN/END----- regels), OF:
    KALSHI_KEY_FILE    - pad naar een los .pem/.key bestand met die key
                         (alleen nodig als je KALSHI_PRIVATE_KEY niet gebruikt)
    KALSHI_ENV         - "prod" of "demo" (default: demo)

Gebruik:
    # Eerst altijd even dry-run om te zien wat het zou doen:
    python kalshi_daily_mm.py --date 2026-09-17 --min 11000000 --max 13000000 --dry-run

    # Daarna echt plaatsen:
    python kalshi_daily_mm.py --date 2026-09-17 --min 11000000 --max 13000000
"""

import argparse
import base64
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.exceptions import InvalidSignature
from dotenv import load_dotenv

SERIES_TICKER = "KXYTVIEWSD"
ARTIST_CODE = "TAY"  # Taylor Swift; Drake=DRA, Bad Bunny=BAD, Ariana=ARI, Wallen=MOR, Bieber=JUS


class Environment(Enum):
    DEMO = "demo"
    PROD = "prod"


class KalshiClient:
    """Minimale Kalshi API-client: RSA-PSS request signing + de paar endpoints die dit script nodig heeft."""

    def __init__(self, key_id: str, private_key: rsa.RSAPrivateKey, environment: Environment):
        self.key_id = key_id
        self.private_key = private_key
        self.environment = environment
        self._last_call = datetime.now()
        if environment == Environment.DEMO:
            self.base_url = "https://external-api.demo.kalshi.co/trade-api/v2"
        else:
            self.base_url = "https://external-api.kalshi.com/trade-api/v2"

    def _sign(self, text: str) -> str:
        try:
            signature = self.private_key.sign(
                text.encode("utf-8"),
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                hashes.SHA256(),
            )
            return base64.b64encode(signature).decode("utf-8")
        except InvalidSignature as e:
            raise ValueError("RSA sign PSS mislukt") from e

    def _headers(self, method: str, path: str) -> dict:
        timestamp_ms = str(int(time.time() * 1000))
        msg = timestamp_ms + method + path
        return {
            "Content-Type": "application/json",
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": self._sign(msg),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        }

    def _rate_limit(self) -> None:
        elapsed = (datetime.now() - self._last_call).total_seconds()
        if elapsed < 0.15:
            time.sleep(0.15 - elapsed)
        self._last_call = datetime.now()

    def _request(self, method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None) -> Any:
        self._rate_limit()
        resp = requests.request(
            method,
            self.base_url + path,
            headers=self._headers(method, "/trade-api/v2" + path),
            params=params,
            json=body,
            timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text}")
        return resp.json()

    def get_markets(self, event_ticker: str, status: str = "open") -> list:
        data = self._request("GET", "/markets", params={"event_ticker": event_ticker, "status": status, "limit": 200})
        return data.get("markets", [])

    def create_order(self, ticker: str, side: str, price: float, count: float, expiration_time: Optional[int]) -> dict:
        body = {
            "ticker": ticker,
            "client_order_id": str(uuid.uuid4()),
            "side": side,  # "bid" (long yes) of "ask" (long no)
            "count": f"{count:.2f}",
            "price": f"{price:.2f}",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": True,  # nooit als taker over de spread heen matchen -- puur market-making
        }
        if expiration_time is not None:
            body["time_in_force"] = "good_till_canceled"
            body["expiration_time"] = expiration_time
        else:
            body["time_in_force"] = "good_till_canceled"
        return self._request("POST", "/portfolio/events/orders", body=body)


def build_event_ticker(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{SERIES_TICKER}-{ARTIST_CODE}{d.strftime('%y%b%d').upper()}"


def load_private_key() -> rsa.RSAPrivateKey:
    """Laadt de RSA private key uit env -- ofwel de hele inhoud inline (KALSHI_PRIVATE_KEY,
    zoals in .env of als GitHub Actions secret), ofwel een los bestand (KALSHI_KEY_FILE)."""
    inline = os.getenv("KALSHI_PRIVATE_KEY")
    if inline:
        return serialization.load_pem_private_key(inline.encode("utf-8"), password=None)
    key_file = os.getenv("KALSHI_KEY_FILE")
    if key_file:
        with open(key_file, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    sys.exit("Fout: zet KALSHI_PRIVATE_KEY (de hele key-inhoud) of KALSHI_KEY_FILE (pad naar een .pem-bestand) in je .env.")


def clip_price(p: float) -> float:
    return round(min(max(p, 0.01), 0.99), 2)


def plan_orders(markets: list, min_views: float, max_views: float, tail_price: float, half_spread: float, contracts: float):
    """Bepaalt per strike buiten [min_views, max_views] de twee orders (yes-bid + no-bid)."""
    plan = []
    for m in markets:
        strike_type = m.get("strike_type")
        floor_strike = m.get("floor_strike")
        if strike_type not in ("greater", "greater_or_equal") or floor_strike is None:
            continue

        if floor_strike < min_views:
            bucket = "confident_yes"
            yes_price = clip_price(tail_price - half_spread)
            no_price = clip_price((1 - tail_price) - half_spread)
        elif floor_strike > max_views:
            bucket = "confident_no"
            no_price = clip_price(tail_price - half_spread)
            yes_price = clip_price((1 - tail_price) - half_spread)
        else:
            continue  # binnen de bandbreedte -- geen quote, te onzeker

        plan.append({
            "ticker": m["ticker"],
            "floor_strike": floor_strike,
            "bucket": bucket,
            "cur_yes_ask": m.get("yes_ask_dollars"),
            "cur_no_bid": m.get("no_bid_dollars"),
            "orders": [
                {"side": "bid", "price": yes_price, "count": contracts, "label": "YES-bid"},
                {"side": "ask", "price": clip_price(1 - no_price), "count": contracts, "label": f"NO-bid @ {no_price:.2f}"},
            ],
        })
    plan.sort(key=lambda x: x["floor_strike"])
    return plan


def main():
    parser = argparse.ArgumentParser(description="Tweezijdige limit-orders buiten de UCG-bandbreedte op de Kalshi daily view count markt.")
    parser.add_argument("--date", required=True, help="Datum van de Kalshi-markt, YYYY-MM-DD (bv. 2026-09-17)")
    parser.add_argument("--min", type=float, required=True, help="MIN UCG-projectie in views (zoals op het Voorspelling-tabblad)")
    parser.add_argument("--max", type=float, required=True, help="MAX UCG-projectie in views (zoals op het Voorspelling-tabblad)")
    parser.add_argument("--contracts", type=float, default=5, help="Aantal contracten per order (default: 5)")
    parser.add_argument("--tail-price", type=float, default=0.97, help="Prijs voor de 'vrijwel zekere' kant (default: 0.97)")
    parser.add_argument("--spread", type=float, default=0.04, help="Totale spread tussen de twee kanten (default: 0.04)")
    parser.add_argument("--expire-hours", type=float, default=6, help="Orders automatisch laten vervallen na N uur (default: 6, 0 = nooit)")
    parser.add_argument("--dry-run", action="store_true", help="Alleen tonen wat er geplaatst zou worden, niets versturen")
    args = parser.parse_args()

    if args.min >= args.max:
        sys.exit(f"Fout: --min ({args.min:,.0f}) moet kleiner zijn dan --max ({args.max:,.0f})")

    load_dotenv()
    env = Environment(os.getenv("KALSHI_ENV") or "demo")
    key_id = os.getenv("KALSHI_KEY_ID")
    if not key_id:
        sys.exit("Fout: zet KALSHI_KEY_ID in je .env bestand (zie de docstring bovenin dit script).")
    private_key = load_private_key()

    client = KalshiClient(key_id, private_key, env)
    event_ticker = build_event_ticker(args.date)
    print(f"Omgeving: {env.value} | Event: {event_ticker}")

    markets = client.get_markets(event_ticker, status="open")
    if not markets:
        sys.exit(f"Geen open markten gevonden voor {event_ticker}. Klopt de datum, en bestaat deze markt al/nog?")

    plan = plan_orders(markets, args.min, args.max, args.tail_price, args.spread / 2, args.contracts)
    if not plan:
        print("Geen strikes buiten de bandbreedte gevonden -- niets te doen.")
        return

    print(f"\n{len(plan)} strikes buiten [{args.min:,.0f}, {args.max:,.0f}] views:\n")
    for item in plan:
        print(f"  {item['ticker']}  (floor {item['floor_strike']:,.0f}, {item['bucket']}, huidig yes_ask={item['cur_yes_ask']} no_bid={item['cur_no_bid']})")
        for o in item["orders"]:
            print(f"      -> {o['label']:<16} side={o['side']:<3} price={o['price']:.2f}  count={o['count']:.2f}")

    if args.dry_run:
        print("\n[DRY RUN] Er is niets naar Kalshi verstuurd.")
        return

    expiration_time = None
    if args.expire_hours > 0:
        expiration_time = int((datetime.now(timezone.utc) + timedelta(hours=args.expire_hours)).timestamp())

    print("\nOrders plaatsen...")
    for item in plan:
        for o in item["orders"]:
            try:
                result = client.create_order(item["ticker"], o["side"], o["price"], o["count"], expiration_time)
                print(f"  OK  {item['ticker']} {o['side']} @ {o['price']:.2f} -> order_id={result.get('order_id')} fill_count={result.get('fill_count')}")
            except Exception as e:
                print(f"  FOUT {item['ticker']} {o['side']} @ {o['price']:.2f} -> {e}")


if __name__ == "__main__":
    main()
