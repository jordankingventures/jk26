"""
Kalshi daily view count -- gedeelde bouwstenen voor de market-making bot (Taylor Swift).

Geen los te draaien script: dit bevat de Kalshi-API-client (authenticatie,
markten opvragen, orders plaatsen) en de kansmodel-gebaseerde strike-
selectie/prijsbepaling (plan_orders_by_probability) die kalshi_auto_taylor.py
importeert en aanroept. Zie dat bestand voor de daadwerkelijke uitvoering.

Idee achter het kansmodel: op de Kalshi 'daily view count' markt (serie
KXYTVIEWSD) bestaat per artiest per dag een ladder van strikes ("Above
13.0M", "Above 13.1M", ...). Per strike wordt de UCG-factor-drempel
(strike / dagtotaal) uitgedrukt in standaarddeviaties vanaf je historische
gemiddelde factor; alleen strikes met een kans op een foute uitkomst onder
een expliciete drempel worden gequote, tegen een prijs die meeschaalt met
hoe zeker je bent. Zie strike_price_by_probability() voor de precieze
formule.
"""

import base64
import os
import sys
import time
import uuid
from datetime import datetime
from enum import Enum
from statistics import NormalDist
from typing import Any, Optional

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.exceptions import InvalidSignature

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

    def get_orderbook(self, ticker: str) -> dict:
        data = self._request("GET", f"/markets/{ticker}/orderbook")
        return data.get("orderbook_fp", {})

    def create_order(
        self, ticker: str, side: str, price: float, count: float, expiration_time: Optional[int],
        post_only: bool = True, time_in_force: str = "good_till_canceled",
    ) -> dict:
        body = {
            "ticker": ticker,
            "client_order_id": str(uuid.uuid4()),
            "side": side,  # "bid" (long yes) of "ask" (long no)
            "count": f"{count:.2f}",
            "price": f"{price:.2f}",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": post_only,  # False = mag als taker over de spread heen matchen (een koopje pakken)
            "time_in_force": time_in_force,
        }
        if expiration_time is not None:
            body["expiration_time"] = expiration_time
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


def strike_price_by_probability(
    floor_strike: float, day_total: float, mean_factor: float, std_factor: float,
    risk_threshold: float, price_floor: float, price_cap: float, z_range: float,
) -> Optional[tuple]:
    """Bepaalt voor één strike (kant, prijs) op basis van een kansmodel, of None als de
    strike niet veilig genoeg is om te quoten.

    De UCG-factor wordt als Normaal(mean_factor, std_factor) verondersteld (zelfde
    gemiddelde/stdev als de "Interval (gem. +/- 1,5 sigma)"-statistiek op de
    Voorspelling-tab). Voor een strike wordt de impliciete factor-drempel
    (floor_strike / day_total) uitgedrukt in aantal standaarddeviaties (z) vanaf het
    gemiddelde. Alleen strikes met |z| >= z_min (de z-score die bij risk_threshold hoort,
    bv. z=2.33 voor 1%) worden gequote -- dat is de harde risicogrens.

    De prijs schaalt lineair met |z| tussen z_min (price_floor, net binnen de veilige
    zone, voorzichtig geprijsd) en z_min + z_range (price_cap, diep in de staart, vrijwel
    zeker -- daar mag je als enige liquiditeitsverschaffer agressiever vragen).
    """
    if std_factor <= 0 or day_total <= 0:
        return None

    threshold_factor = floor_strike / day_total
    z = (threshold_factor - mean_factor) / std_factor
    z_min = NormalDist().inv_cdf(1 - risk_threshold)
    cdf_val = NormalDist(mean_factor, std_factor).cdf(threshold_factor)  # P(actual <= floor_strike)

    if z <= -z_min:
        kant = "yes"       # drempel ligt ruim onder het gemiddelde -- vrijwel zeker YES
        p_wrong = cdf_val  # P(YES fout is) = P(actual <= strike)
    elif z >= z_min:
        kant = "no"              # drempel ligt ruim boven het gemiddelde -- vrijwel zeker NO
        p_wrong = 1 - cdf_val    # P(NO fout is) = P(actual > strike)
    else:
        return None  # binnen de onzekere zone -- niet quoten

    frac = min(1.0, (abs(z) - z_min) / z_range) if z_range > 0 else 1.0
    prijs = price_floor + frac * (price_cap - price_floor)
    return kant, round(prijs, 2), round(p_wrong, 5)


def find_bargain_price(orderbook: dict, kant: str, fair_value: float, take_margin: float) -> Optional[float]:
    """Checkt of er al een tegenpartij in het orderboek staat die goedkoper is dan
    fair_value - take_margin voor de kant waar je toch al vertrouwen in hebt. Zo ja,
    geeft de prijs terug waartegen je zou moeten kruisen (nemen i.p.v. passief bieden).

    Het orderboek toont alleen bids; de prijs om zelf te KOPEN (kruisen) is het
    spiegelbeeld van de beste bid aan de andere kant (YES-ask = 1 - beste NO-bid, en
    omgekeerd) -- zie de Kalshi-orderboekdocumentatie.
    """
    yes_bids = orderbook.get("yes_dollars", [])
    no_bids = orderbook.get("no_dollars", [])

    if kant == "yes":
        best_no_bid = max((float(p) for p, _ in no_bids), default=0.0)
        best_yes_ask = round(1 - best_no_bid, 4)
        if best_yes_ask <= fair_value - take_margin:
            return best_yes_ask
    else:
        best_yes_bid = max((float(p) for p, _ in yes_bids), default=0.0)
        best_no_ask = round(1 - best_yes_bid, 4)
        if best_no_ask <= fair_value - take_margin:
            return best_no_ask
    return None


def plan_orders_by_probability(
    markets: list, day_total: float, mean_factor: float, std_factor: float, contracts: float,
    risk_threshold: float = 0.01, price_floor: float = 0.55, price_cap: float = 0.95, z_range: float = 2.0,
):
    """Zelfde vorm als plan_orders(), maar met kant+prijs per strike bepaald door een
    kansmodel (strike_price_by_probability) i.p.v. een vaste min/max-bandbreedte en prijs."""
    plan = []
    for m in markets:
        strike_type = m.get("strike_type")
        floor_strike = m.get("floor_strike")
        if strike_type not in ("greater", "greater_or_equal") or floor_strike is None:
            continue

        result = strike_price_by_probability(
            floor_strike, day_total, mean_factor, std_factor, risk_threshold, price_floor, price_cap, z_range
        )
        if result is None:
            continue
        kant, prijs, p_wrong = result

        if kant == "yes":
            bucket = "confident_yes"
            order = {"side": "bid", "price": clip_price(prijs), "count": contracts, "p_wrong": p_wrong, "label": f"YES-bid @ {prijs:.2f}"}
        else:
            bucket = "confident_no"
            order = {"side": "ask", "price": clip_price(1 - prijs), "count": contracts, "p_wrong": p_wrong, "label": f"NO-bid @ {prijs:.2f}"}

        plan.append({
            "ticker": m["ticker"],
            "floor_strike": floor_strike,
            "bucket": bucket,
            "cur_yes_ask": m.get("yes_ask_dollars"),
            "cur_no_bid": m.get("no_bid_dollars"),
            "orders": [order],
        })
    plan.sort(key=lambda x: x["floor_strike"])
    return plan

