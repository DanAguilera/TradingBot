# webhook_listener.py
from flask import Flask, request, jsonify
import os, time, csv
from typing import Tuple
from dotenv import load_dotenv
from kraken_client import (
    get_pair_info, get_ticker_price, get_balance,
    place_market_with_conditional_close, quote_from_symbol,
)

load_dotenv()

app = Flask(__name__)

# ----------- CONFIG -----------
EQUITY_PCT       = float(os.getenv("EQUITY_PCT", "0.30"))   # 30% of wallet for BUYS
MIN_NOTIONAL_USD = float(os.getenv("MIN_NOTIONAL_USD", "10"))
DEDUP_WINDOW_SEC = float(os.getenv("DEDUP_WINDOW_SEC", "8"))
SHARED_SECRET    = os.getenv("SHARED_SECRET", "gearsofwar4life")
LOG_FILE         = "trades.csv"

# ----------- STATE -----------
_last_sig = {"key": None, "ts": 0.0}

# ----------- Helpers -----------
def dedup_key(payload: dict) -> str:
    s    = str(payload.get("symbol","")).upper()
    side = str(payload.get("side","")).lower()
    sls  = str(payload.get("sl","")) + str(payload.get("sl_long","")) + str(payload.get("sl_short",""))
    tps  = str(payload.get("tp","")) + str(payload.get("tp_long","")) + str(payload.get("tp_short",""))
    return "|".join([s, side, sls, tps])

def write_log(row: dict):
    exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not exists: w.writeheader()
        w.writerow(row)

def compute_buy_qty_equity_pct(symbol: str, equity_quote: float, entry_price: float) -> Tuple[float, float]:
    """BUY sizing: % of quote wallet. Returns (qty_base, notional_quote)."""
    info = get_pair_info(symbol)
    target_notional = equity_quote * EQUITY_PCT
    raw_qty = max(0.0, target_notional / entry_price)
    qty = raw_qty if raw_qty >= info["ordermin"] else 0.0
    qty = float(f"{qty:.{info['lot_decimals']}f}")
    return qty, qty * entry_price

def base_from_symbol(symbol: str) -> str:
    s = symbol.replace(":","").replace("/","").upper()
    if s.startswith("ETH"): return "XETH"   # Kraken base key for ETH
    if s.startswith("BTC") or s.startswith("XBT"): return "XXBT"
    return "XETH"  # fallback

def compute_sell_qty_from_base(symbol: str) -> float:
    """SELL sizing for spot: sell what you hold in base asset (no shorting)."""
    info = get_pair_info(symbol)
    base_key = base_from_symbol(symbol)
    base_bal = get_balance(base_key)  # base units (e.g., ETH amount)
    if base_bal <= 0:
        return 0.0
    return float(f"{base_bal:.{info['lot_decimals']}f}")

# ----------- Route -----------
@app.post("/webhook")
def tv_webhook():
    global _last_sig
    try:
        payload = request.get_json(force=True)
        print("📩 Incoming:", payload)

        # Secret check
        if SHARED_SECRET and payload.get("secret") != SHARED_SECRET:
            return jsonify({"ok": False, "err": "bad secret"}), 401

        # Core fields
        symbol = payload["symbol"].replace(":","").replace("/","").upper()
        side   = str(payload.get("side","")).lower()  # "buy" / "sell"

        # Accept BOTH schemas: new(sl/tp) OR legacy(sl_long/tp_long/sl_short/tp_short)
        def to_f(x):
            try: return float(x)
            except: return 0.0
        sl = to_f(payload.get("sl"))
        tp = to_f(payload.get("tp"))
        if sl == 0.0 and tp == 0.0:
            if side == "buy":
                sl = to_f(payload.get("sl_long"));  tp = to_f(payload.get("tp_long"))
            elif side == "sell":
                sl = to_f(payload.get("sl_short")); tp = to_f(payload.get("tp_short"))

        # De-dup
        key = dedup_key(payload); now = time.time()
        if _last_sig["key"] == key and (now - _last_sig["ts"]) < DEDUP_WINDOW_SEC:
            print("⏭️  Dedup: same signal inside window; skipping.")
            return jsonify({"ok": False, "err": "duplicate"}), 200
        _last_sig = {"key": key, "ts": now}

        # Price & sizing
        entry_price = get_ticker_price(symbol)

        if side == "buy":
            quote_key   = quote_from_symbol(symbol)  # 'USDT' or 'ZUSD'
            equity      = get_balance(quote_key)
            qty, notional = compute_buy_qty_equity_pct(symbol, equity, entry_price)
        else:
            # SELL on spot = flatten using base balance (no short)
            qty = compute_sell_qty_from_base(symbol)
            notional = qty * entry_price

        print(f"→ parsed side={side} sl={sl} tp={tp} price={entry_price} qty={qty} notional=${notional:.2f}")

        if qty <= 0 or notional < MIN_NOTIONAL_USD:
            msg = f"skip: qty={qty} notional=${notional:.2f} < ${MIN_NOTIONAL_USD}"
            print("⚠️", msg)
            return jsonify({"ok": False, "err": msg}), 200

        # Place order (LIVE). For a dry run, set validate=True.
        userref = int(now)
        resp = place_market_with_conditional_close(
            symbol=symbol, side=side, qty=qty,
            tp=tp if tp > 0 else None, sl=sl if sl > 0 else None,
            validate=False, userref=userref
        )
        print("✅ Order accepted:", resp)

        # Log
        logrow = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "symbol": symbol, "side": side, "qty": qty,
            "entry_price": entry_price, "sl": sl, "tp": tp,
            "notional": round(notional, 2), "userref": userref
        }
        write_log(logrow)

        return jsonify({"ok": True, "kraken": resp, "log": logrow}), 200

    except Exception as e:
        print("❌ Error:", e)
        return jsonify({"ok": False, "err": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","5000")))


