import json
import os
import ssl
import urllib.request

TOPIC = os.environ["NTFY_TOPIC"]
SYMBOL = os.environ.get("SYMBOL", "")
GRAN = 300
COUNT = 150
EMA_P, FAST, SLOW, SIG, RSI_P = 9, 12, 26, 2, 14
STATE_FILE = "last_signal.txt"
ENDPOINTS = [
    "wss://api.derivws.com/trading/v1/options/ws/public",
    "wss://ws.derivws.com/websockets/v3?app_id=1089",
]


def ema(a, p):
    k = 2 / (p + 1)
    out = [a[0]]
    for v in a[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def heiken(cs):
    ha = []
    for i, c in enumerate(cs):
        hc = (c["o"] + c["h"] + c["l"] + c["c"]) / 4
        ho = (c["o"] + c["c"]) / 2 if i == 0 else (ha[-1]["o"] + ha[-1]["c"]) / 2
        ha.append({"o": ho, "c": hc})
    return ha


def rsi(cl, p):
    g = l = 0.0
    for i in range(1, p + 1):
        d = cl[i] - cl[i - 1]
        g += max(d, 0)
        l += max(-d, 0)
    g /= p
    l /= p
    for i in range(p + 1, len(cl)):
        d = cl[i] - cl[i - 1]
        g = (g * (p - 1) + max(d, 0)) / p
        l = (l * (p - 1) + max(-d, 0)) / p
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def signal(cs):
    cl = [c["c"] for c in cs]
    ha = heiken(cs)
    hc = [h["c"] for h in ha]
    e9 = ema(hc, EMA_P)
    macd = [a - b for a, b in zip(ema(cl, FAST), ema(cl, SLOW))]
    sg = ema(macd, SIG)
    i = -1
    r = rsi(cl, RSI_P)
    votes = [
        ha[i]["c"] > ha[i]["o"],
        hc[i] > e9[i],
        macd[i] > sg[i],
        r > 50,
    ]
    down = [
        ha[i]["c"] < ha[i]["o"],
        hc[i] < e9[i],
        macd[i] < sg[i],
        r < 50,
    ]
    if all(votes):
        return "BUY", cl[i], r
    if all(down):
        return "SELL", cl[i], r
    return "WAIT", cl[i], r


def fetch_candles():
    import websocket  # pip install websocket-client

    errors = []
    for url in ENDPOINTS:
        try:
            ws = websocket.create_connection(url, timeout=15)
            sym = SYMBOL
            if not sym:
                ws.send(json.dumps({"active_symbols": "brief", "product_type": "basic"}))
                while True:
                    d = json.loads(ws.recv())
                    if d.get("msg_type") == "active_symbols":
                        for s in d.get("active_symbols", []):
                            name = s.get("display_name", "")
                            if "step" in name.lower() and "500" in name:
                                sym = s["symbol"]
                        break
                sym = sym or "stpRNG5"
            ws.send(json.dumps({
                "ticks_history": sym, "adjust_start_time": 1, "count": COUNT,
                "end": "latest", "style": "candles", "granularity": GRAN,
            }))
            while True:
                d = json.loads(ws.recv())
                if d.get("error"):
                    raise RuntimeError(d["error"].get("message", "error"))
                if d.get("candles"):
                    ws.close()
                    return [
                        {"t": int(c["epoch"]), "o": float(c["open"]), "h": float(c["high"]),
                         "l": float(c["low"]), "c": float(c["close"])}
                        for c in d["candles"]
                    ], sym
        except Exception as e:  # try the next endpoint
            errors.append(url + ": " + str(e))
    raise SystemExit("No Deriv feed worked: " + " | ".join(errors))


def notify(title, body):
    req = urllib.request.Request(
        "https://ntfy.sh/" + TOPIC,
        data=body.encode("utf-8"),
        headers={"Title": title, "Priority": "high"},
    )
    urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context())


def main():
    candles, sym = fetch_candles()
    closed = candles[:-1]  # drop the still-forming candle
    sig, price, r = signal(closed)
    last = open(STATE_FILE).read().strip() if os.path.exists(STATE_FILE) else ""
    print("signal", sig, "price", price, "rsi", round(r, 1), "last", last or "none")
    if sig in ("BUY", "SELL") and sig != last:
        notify("Step 500: " + sig, sig + " at " + format(price, ".2f") + " (RSI " + format(r, ".1f") + ", 5 min)")
    open(STATE_FILE, "w").write(sig)


main()
