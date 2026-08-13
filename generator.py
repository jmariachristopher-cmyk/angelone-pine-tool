"""
Core logic shared by scripts/run_daily.py (used by GitHub Actions) and
streamlit_app.py (used for on-demand intraday reruns).

Kept as plain functions with no side effects on import, so both callers
can use exactly the same, tested code path -- the daily automated run
and any manual "Regenerate now" click produce identical output.
"""

import csv
import io
import sys
from datetime import datetime

import requests

try:
    from SmartApi import SmartConnect
except ImportError:
    SmartConnect = None  # only needed when actually logging in

import pyotp

INSTRUMENT_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)


# ── Login ────────────────────────────────────────────────────────────
def login(api_key: str, client_id: str, password: str, totp_secret: str):
    if SmartConnect is None:
        raise RuntimeError("smartapi-python not installed. pip install smartapi-python")
    if not all([api_key, client_id, password, totp_secret]):
        raise ValueError("Missing one or more Angel One credentials.")
    obj = SmartConnect(api_key=api_key)
    totp = pyotp.TOTP(totp_secret).now()
    data = obj.generateSession(client_id, password, totp)
    if not data.get("status"):
        raise RuntimeError(f"Angel One login failed: {data}")
    return obj


def get_spot(obj, symbol: str) -> float:
    spot_symbol = symbol if symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY") else f"{symbol}-EQ"
    # NIFTY index token on NSE is 99926000; other indices/stocks need their own token,
    # SmartAPI's ltpData will still resolve correctly by tradingsymbol for most cases.
    token = "99926000" if symbol == "NIFTY" else ""
    resp = obj.ltpData("NSE", spot_symbol, token)
    if not resp.get("status"):
        raise RuntimeError(f"Could not fetch spot price: {resp}")
    return float(resp["data"]["ltp"])


# ── Instrument master + token resolution ────────────────────────────
def load_instrument_master():
    resp = requests.get(INSTRUMENT_MASTER_URL, timeout=60)
    resp.raise_for_status()
    return resp.json()


def find_option_tokens(instruments, symbol, expiry, spot, step, n_each_side):
    """Returns rows centered on ATM: n_each_side strikes above + below
    (2*n_each_side + 1 total), each with resolved CE/PE symbol tokens."""
    atm = round(spot / step) * step
    lo = atm - n_each_side * step
    hi = atm + n_each_side * step

    strikes = {}
    for inst in instruments:
        if inst.get("exch_seg") != "NFO":
            continue
        if inst.get("instrumenttype") not in ("OPTIDX", "OPTSTK"):
            continue
        if inst.get("name", "") != symbol:
            continue
        if inst.get("expiry") != expiry:
            continue
        try:
            strike = float(inst["strike"]) / 100
        except (KeyError, ValueError):
            continue
        if strike < lo or strike > hi:
            continue
        if round(strike) % step != 0:
            continue
        tsym = inst["symbol"]
        entry = strikes.setdefault(strike, {"strike": strike, "ce_token": None, "pe_token": None})
        if tsym.endswith("CE"):
            entry["ce_token"] = inst["token"]
        elif tsym.endswith("PE"):
            entry["pe_token"] = inst["token"]

    rows = sorted(strikes.values(), key=lambda r: r["strike"])
    rows = [r for r in rows if r["ce_token"] and r["pe_token"]]
    return rows, atm


# ── Quotes ───────────────────────────────────────────────────────────
def fetch_quotes(obj, rows):
    tokens = []
    for r in rows:
        tokens.append(r["ce_token"])
        tokens.append(r["pe_token"])

    BATCH = 50
    ltp_by_token = {}
    for i in range(0, len(tokens), BATCH):
        chunk = tokens[i:i + BATCH]
        resp = obj.getMarketData("LTP", {"NFO": chunk})
        if not resp.get("status"):
            continue
        for item in resp["data"]["fetched"]:
            ltp_by_token[item["symbolToken"]] = float(item["ltp"])

    for r in rows:
        r["ce"] = ltp_by_token.get(r["ce_token"], 0.0)
        r["pe"] = ltp_by_token.get(r["pe_token"], 0.0)
    return rows


# ── Math (verified against the original spreadsheet) ────────────────
def compute_zones(rows):
    n = len(rows)
    out = []
    for i, r in enumerate(rows):
        ce, pe = r["ce"], r["pe"]
        avg = (ce + pe) / 2
        if i >= 2:
            ce_up = rows[i + 2]["ce"] if i + 2 < n else 0.0
            pe_down = rows[i - 2]["pe"]
            bl = (ce_up + pe_down) / 2
        else:
            bl = None
        out.append({
            "strike": r["strike"], "ce": ce, "pe": pe, "avg": avg, "bl": bl,
            "uce133": ce * 1.33, "uce15": ce * 1.5,
            "upe133": pe * 1.33, "upe15": pe * 1.5,
            "lce15": ce / 1.5, "lce2": ce / 3,
            "lpe15": pe / 1.5, "lpe2": pe / 3,
        })
    return out


# ── Output text builders ────────────────────────────────────────────
def fv(v):
    return "0" if v is None else f"{v:.4f}"


def build_csv_text(data) -> str:
    cols = ["strike", "ce", "pe", "avg", "bl", "uce133", "uce15",
            "upe133", "upe15", "lce15", "lce2", "lpe15", "lpe2"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for d in data:
        w.writerow([d[c] if d[c] is not None else "" for c in cols])
    return buf.getvalue()


def build_pine_text(data, symbol, expiry) -> str:
    strikes = [f'"{d["strike"]:g}"' for d in data]
    default_s = f'"{data[len(data)//2]["strike"]:g}"'
    today = datetime.now().strftime("%d %b %Y")

    def arr(key):
        return ", ".join(fv(d[key]) for d in data)

    L = []
    L.append("//@version=5")
    L.append(f'indicator("Options Reversal Zones - {symbol} {today} | Expiry: {expiry}", '
              f'overlay=true, max_lines_count=500, max_labels_count=500)')
    L.append("")
    L.append("// -- Strike Selector --")
    L.append("// Add this indicator to the UNDERLYING chart (e.g. NIFTY spot/futures),")
    L.append("// not an option chart -- Auto-ATM needs the underlying's live close price.")
    L.append('auto_atm = input.bool(true, "Auto-track ATM to live price")')
    L.append(f'selected = input.string({default_s}, "Manual Strike (used when Auto-track is off)", options=[{", ".join(strikes)}])')
    L.append("")
    L.append("// -- Zone Toggles --")
    L.append('show_avg    = input.bool(true,  "Individual Average")')
    L.append('show_bl     = input.bool(true,  "Boundary Line")')
    L.append('show_uce    = input.bool(true,  "Upper Reversal Zone CE")')
    L.append('show_upe    = input.bool(true,  "Upper Reversal Zone PE")')
    L.append('show_lce    = input.bool(true,  "Lower Reversal Zone CE")')
    L.append('show_lpe    = input.bool(true,  "Lower Reversal Zone PE")')
    L.append('show_lbl    = input.bool(true,  "Show Labels")')
    L.append('lw          = input.int(1, "Line Width", minval=1, maxval=4)')
    L.append("")
    L.append("// -- Data (pulled from Angel One this morning, computed here) --")
    L.append(f'var string[] strike_arr  = array.from({", ".join(strikes)})')
    L.append(f'var float[]  avg_arr     = array.from({arr("avg")})')
    L.append(f'var float[]  bl_arr      = array.from({arr("bl")})')
    L.append(f'var float[]  uce133_arr  = array.from({arr("uce133")})')
    L.append(f'var float[]  uce15_arr   = array.from({arr("uce15")})')
    L.append(f'var float[]  upe133_arr  = array.from({arr("upe133")})')
    L.append(f'var float[]  upe15_arr   = array.from({arr("upe15")})')
    L.append(f'var float[]  lce15_arr   = array.from({arr("lce15")})')
    L.append(f'var float[]  lce2_arr    = array.from({arr("lce2")})')
    L.append(f'var float[]  lpe15_arr   = array.from({arr("lpe15")})')
    L.append(f'var float[]  lpe2_arr    = array.from({arr("lpe2")})')
    L.append("")
    L.append("f_line(y, col, w) =>")
    L.append("    line.new(bar_index-1, y, bar_index, y, extend=extend.both, color=col, width=w)")
    L.append("")
    L.append("f_lbl(y, txt, col) =>")
    L.append("    if show_lbl")
    L.append("        label.new(bar_index, y, txt, xloc=xloc.bar_index, yloc=yloc.price,")
    L.append("                  style=label.style_label_left, color=color.new(col,80),")
    L.append("                  textcolor=col, size=size.small)")
    L.append("")
    L.append("// -- Auto-ATM: pick the strike in our list nearest the live close --")
    L.append("f_nearest_idx() =>")
    L.append("    var int best = 0")
    L.append("    minDiff = 1e10")
    L.append("    for i = 0 to array.size(strike_arr) - 1")
    L.append("        sk = str.tonumber(array.get(strike_arr, i))")
    L.append("        d = math.abs(close - sk)")
    L.append("        if d < minDiff")
    L.append("            minDiff := d")
    L.append("            best := i")
    L.append("    best")
    L.append("")
    L.append("idx = auto_atm ? f_nearest_idx() : array.indexof(strike_arr, selected)")
    L.append("")
    L.append("if barstate.islast and idx >= 0")
    L.append("    s = array.get(strike_arr, idx)")
    L.append("    if show_avg")
    L.append("        v = array.get(avg_arr, idx)")
    L.append("        f_line(v, color.new(color.gray, 20), lw)")
    L.append('        f_lbl(v, s + " Avg " + str.tostring(v, "#.00"), color.gray)')
    L.append("    if show_bl")
    L.append("        v = array.get(bl_arr, idx)")
    L.append("        f_line(v, color.new(color.orange, 20), lw)")
    L.append('        f_lbl(v, s + " BL " + str.tostring(v, "#.00"), color.orange)')
    L.append("    if show_uce")
    L.append("        v = array.get(uce133_arr, idx)")
    L.append("        f_line(v, color.new(color.red, 20), lw)")
    L.append('        f_lbl(v, s + " UCEx1.33 " + str.tostring(v, "#.00"), color.red)')
    L.append("        v := array.get(uce15_arr, idx)")
    L.append("        f_line(v, color.new(color.red, 40), lw)")
    L.append('        f_lbl(v, s + " UCEx1.5 " + str.tostring(v, "#.00"), color.red)')
    L.append("    if show_upe")
    L.append("        v = array.get(upe133_arr, idx)")
    L.append("        f_line(v, color.new(color.purple, 20), lw)")
    L.append('        f_lbl(v, s + " UPEx1.33 " + str.tostring(v, "#.00"), color.purple)')
    L.append("        v := array.get(upe15_arr, idx)")
    L.append("        f_line(v, color.new(color.purple, 40), lw)")
    L.append('        f_lbl(v, s + " UPEx1.5 " + str.tostring(v, "#.00"), color.purple)')
    L.append("    if show_lce")
    L.append("        v = array.get(lce15_arr, idx)")
    L.append("        f_line(v, color.new(color.teal, 20), lw)")
    L.append('        f_lbl(v, s + " LCEx1.5 " + str.tostring(v, "#.00"), color.teal)')
    L.append("        v := array.get(lce2_arr, idx)")
    L.append("        f_line(v, color.new(color.teal, 40), lw)")
    L.append('        f_lbl(v, s + " LCEx2 " + str.tostring(v, "#.00"), color.teal)')
    L.append("    if show_lpe")
    L.append("        v = array.get(lpe15_arr, idx)")
    L.append("        f_line(v, color.new(color.blue, 20), lw)")
    L.append('        f_lbl(v, s + " LPEx1.5 " + str.tostring(v, "#.00"), color.blue)')
    L.append("        v := array.get(lpe2_arr, idx)")
    L.append("        f_line(v, color.new(color.blue, 40), lw)")
    L.append('        f_lbl(v, s + " LPEx2 " + str.tostring(v, "#.00"), color.blue)')
    L.append("")
    L.append('plot(na, "Individual Average",     color=color.gray)')
    L.append('plot(na, "Boundary Line",          color=color.orange)')
    L.append('plot(na, "Upper Reversal Zone CE", color=color.red)')
    L.append('plot(na, "Upper Reversal Zone PE", color=color.purple)')
    L.append('plot(na, "Lower Reversal Zone CE", color=color.teal)')
    L.append('plot(na, "Lower Reversal Zone PE", color=color.blue)')

    return "\n".join(L)


def run_pipeline(api_key, client_id, password, totp_secret, symbol, expiry, step, n_each_side):
    """One call that does the whole thing; used by both the CLI script and Streamlit."""
    obj = login(api_key, client_id, password, totp_secret)
    spot = get_spot(obj, symbol)
    instruments = load_instrument_master()
    rows, atm = find_option_tokens(instruments, symbol, expiry, spot, step, n_each_side)
    if not rows:
        raise RuntimeError(
            "No matching option contracts found. Check --symbol/--expiry spelling "
            "against Angel One's instrument master, and that --step matches the "
            "underlying's real strike interval."
        )
    rows = fetch_quotes(obj, rows)
    data = compute_zones(rows)
    return {
        "data": data,
        "spot": spot,
        "atm": atm,
        "symbol": symbol,
        "expiry": expiry,
        "csv_text": build_csv_text(data),
        "pine_text": build_pine_text(data, symbol, expiry),
        "generated_at": datetime.now().isoformat(),
    }
