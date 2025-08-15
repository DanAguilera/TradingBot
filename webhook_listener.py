# webhook_listener.py
from flask import Flask, request, jsonify
import os, time, csv
from typing import Tuple
from dotenv import load_dotenv

from kraken_client import (
    get_pair_info,
    get_ticker_price,
    get_balance,
    place_market_with_conditional_close,
    quote_from_symbol,
    base_from_symbol,   # <-- use the helper from kraken_client
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
        if not exists:
            w.writeheader()
        w.writerow(row)

def compute_qty_side_aware(symbol: str, side: str, price: float) -> Tuple[float, float]:
    """
    BUY  -> spend EQUITY_PCT of quote balance (USDT/ZUSD) -> base qty
    SELL -> sell 100% of base balance (spot flatten)      -> base qty
    Returns (qty_base, notional_quote)
    """
    info     = get_pair_info(symbol)
    lot_dec  = info["lot_decimals"]
    ordermin = info["ordermin"]

    if side == "buy":
        quote_key       = quote_from_symbol(symbol)      # 'USDT' or 'ZUSD'
        quote_balance   = get_balance(quote_key)         # in quote units
        target_notional = quote_balance * EQUITY_PCT
        raw_qty         = target_notional / price
    else:
        base_key  = base_from_symbol(symbol)             # e.g., 'XETH', 'XXBT'
        raw_qty   = get_balance(base_key)
        target_notional = raw_qty * price

    # Enforce Kraken base minimum + lot rounding
    qty = 0.0 if raw_qty < ordermin else raw_qty
    qty = float(f"{qty:.{lot_dec}f}")
    notional = qty * price
    return qty, notional

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
        symbol = payload["symbol"].replace(":", "").replace("/", "").upper()
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
        qty, notional = compute_qty_side_aware(symbol, side, entry_price)

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
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))



