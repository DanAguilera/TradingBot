# kraken_client.py
import time, hmac, hashlib, base64, urllib.parse, requests, os
from typing import Optional, Dict
from dotenv import load_dotenv

load_dotenv()  # load .env into process env

API_KEY_RAW    = os.getenv("API_KEY")
API_SECRET_RAW = os.getenv("API_SECRET")
if not API_KEY_RAW or not API_SECRET_RAW:
    raise RuntimeError("Missing API_KEY or API_SECRET in .env")

# Kraken gives secret as base64; convert once to bytes for HMAC
try:
    API_SECRET_BYTES = base64.b64decode(API_SECRET_RAW)
except Exception as e:
    raise RuntimeError("API_SECRET must be base64 (as provided by Kraken).") from e

API_KEY  = API_KEY_RAW
BASE_URL = "https://api.kraken.com"

class KrakenAPIError(RuntimeError): pass

# ---------- signing / requests ----------
def _sign(path: str, data: dict) -> str:
    postdata = urllib.parse.urlencode(data)
    encoded  = (str(data["nonce"]) + postdata).encode()
    message  = path.encode() + hashlib.sha256(encoded).digest()
    mac      = hmac.new(API_SECRET_BYTES, message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()

def _private(path: str, data: dict) -> dict:
    data = dict(data or {})
    data["nonce"] = int(time.time() * 1000)
    headers = {"API-Key": API_KEY, "API-Sign": _sign(f"/0/private{path}", data)}
    r = requests.post(f"{BASE_URL}/0/private{path}", headers=headers, data=data, timeout=20)
    j = r.json()
    if j.get("error"):
        raise KrakenAPIError(",".join(j["error"]))
    return j["result"]

def _public(path: str, params: Optional[dict] = None) -> dict:
    r = requests.get(f"{BASE_URL}/0/public{path}", params=params or {}, timeout=20)
    j = r.json()
    if j.get("error"):
        raise KrakenAPIError(",".join(j["error"]))
    return j["result"]

# ---------- symbol helpers ----------
_PAIR_MAP = {
    "BTCUSD":"XBTUSD", "BTCUSDT":"XBTUSDT", "XBTUSD":"XBTUSD", "XBTUSDT":"XBTUSDT",
    "ETHUSD":"ETHUSD", "ETHUSDT":"ETHUSDT",
}
def normalize_pair(symbol: str) -> str:
    s = symbol.replace(":", "").replace("/", "").upper()
    return _PAIR_MAP.get(s, s)

def quote_from_symbol(symbol: str) -> str:
    s = symbol.replace(":", "").replace("/", "").upper()
    if s.endswith("USDT"): return "USDT"
    if s.endswith("USD"):  return "ZUSD"  # Kraken USD balance key
    return "ZUSD"

# ---------- market data / balances ----------
def get_pair_info(symbol: str) -> Dict:
    pair = normalize_pair(symbol)
    res  = _public("/AssetPairs", {"pair": pair})
    key  = list(res.keys())[0]
    p    = res[key]
    return {
        "pair_code": key,
        "lot_decimals": int(p.get("lot_decimals", 5)),
        "pair_decimals": int(p.get("pair_decimals", 2)),
        "ordermin": float(p.get("ordermin", 0) or 0.0),
    }

def get_ticker_price(symbol: str) -> float:
    pair = normalize_pair(symbol)
    res  = _public("/Ticker", {"pair": pair})
    key  = list(res.keys())[0]
    return float(res[key]["c"][0])

def get_balance(asset_key: str) -> float:
    res = _private("/Balance", {})
    return float(res.get(asset_key, 0.0))

# ---------- order placement ----------
def round_qty(q: float, lot_decimals: int) -> float:
    return float(f"{max(q, 0.0):.{lot_decimals}f}")

def place_market_with_conditional_close(
    symbol: str, side: str, qty: float,
    tp: Optional[float] = None, sl: Optional[float] = None,
    validate: bool = False, userref: Optional[int] = None
) -> dict:
    info = get_pair_info(symbol)
    pair_code = info["pair_code"]
    qty = round_qty(qty, info["lot_decimals"])

    data = {
        "pair": pair_code,
        "type": "buy" if side.lower() == "buy" else "sell",
        "ordertype": "market",
        "volume": str(qty),
        "validate": validate,
    }
    if userref is not None:
        data["userref"] = int(userref)

    # Proper OCO close: stop-loss + take-profit (market closes on trigger)
    have_sl = sl is not None and sl > 0
    have_tp = tp is not None and tp > 0
    if have_sl and have_tp:
        data["close[ordertype]"]  = "stop-loss"
        data["close[price]"]      = str(sl)
        data["close[ordertype2]"] = "take-profit"
        data["close[price2]"]     = str(tp)
    elif have_sl:
        data["close[ordertype]"]  = "stop-loss"
        data["close[price]"]      = str(sl)
    elif have_tp:
        data["close[ordertype]"]  = "take-profit"
        data["close[price]"]      = str(tp)

    return _private("/AddOrder", data)







