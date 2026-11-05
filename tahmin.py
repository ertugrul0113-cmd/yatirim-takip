"""1 haftalık görünüm: eğilim, olası fiyat aralığı ve (yalnızca geçmişte işe yaradıysa) yön sinyali.
Veriler: Binance (kripto fiyatları), alternative.me (Kripto Korku & Açgözlülük Endeksi),
FRED / ABD Merkez Bankası (reel faiz, dolar endeksi), Yahoo Finance (ons altın, USD/TRY).
Yöntem: yürüyen pencereli (walk-forward) lojistik regresyon; isabet yalnızca modelin
eğitimde görmediği dönemde ölçülür. Basit 'hep yukarı/aşağı' tahmininden belirgin
iyi değilse yön gösterilmez, 'Belirsiz' yazılır.
"""
import datetime as dt
import functools
import io
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

print = functools.partial(print, flush=True)  # noqa: A001

ROOT = Path(__file__).resolve().parent
MARKET = ROOT / "data" / "market.json"
OUT = ROOT / "data" / "tahmin.json"
H = 7                      # tahmin ufku (gün)
Z80 = 1.2816               # %80 aralık
ONS_GRAM = 31.1034768
UA = {"User-Agent": "Mozilla/5.0 yatirim-takip"}


def get(url, **kw):
    for i in range(3):
        try:
            r = requests.get(url, timeout=30, headers=UA, **kw)
            r.raise_for_status()
            return r
        except Exception as ex:  # noqa
            print("İstek hatası", url[:80], ex)
            time.sleep(3 * (i + 1))
    raise RuntimeError("erişilemedi: " + url[:80])


def daily(s):
    s = s.dropna().sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s.resample("D").last().ffill()


# ---------------- veri ----------------
def binance_daily(symbol):
    out, start = [], 1483228800000  # 2017-01-01
    while True:
        r = get("https://data-api.binance.vision/api/v3/klines",
                params={"symbol": symbol, "interval": "1d", "startTime": start, "limit": 1000}).json()
        if not r:
            break
        out += r
        if len(r) < 1000:
            break
        start = r[-1][0] + 86400000
    if not out:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([k[0] for k in out], unit="ms")
    return daily(pd.Series([float(k[4]) for k in out], index=idx))


def crypto_try(sym):
    s = pd.Series(dtype=float)
    try:
        s = binance_daily(sym + "TRY")
    except Exception as ex:  # noqa
        print("TRY paritesi yok:", sym, ex)
    # TRY paritesinin geçmişi kısaysa eski günleri USDT paritesi × USDT/TRY ile tamamla
    try:
        alt = (binance_daily(sym + "USDT") * binance_daily("USDTTRY")).dropna()
        s = alt if s.empty else s.combine_first(alt)
    except Exception as ex:  # noqa
        print("USDT yolu olmadı:", sym, ex)
    return daily(s)


def fear_greed():
    j = get("https://api.alternative.me/fng/", params={"limit": 0, "format": "json"}).json()["data"]
    idx = pd.to_datetime([int(d["timestamp"]) for d in j], unit="s").normalize()
    return daily(pd.Series([float(d["value"]) for d in j], index=idx))


def fred(series_id):
    txt = get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series_id}).text
    df = pd.read_csv(io.StringIO(txt))
    dcol = df.columns[0]
    v = pd.to_numeric(df[series_id], errors="coerce")
    return daily(pd.Series(v.values, index=pd.to_datetime(df[dcol])))


def from_market(key):
    m = json.loads(MARKET.read_text(encoding="utf-8"))
    pts = m["assets"][key]["d1"]
    idx = pd.to_datetime([p[0] for p in pts], unit="s")
    return daily(pd.Series([p[1] for p in pts], index=idx))


# ---------------- özellikler ----------------
def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def base_features(p):
    lr = np.log(p).diff()
    f = pd.DataFrame(index=p.index)
    f["r7"] = np.log(p / p.shift(7))
    f["r28"] = np.log(p / p.shift(28))
    f["r90"] = np.log(p / p.shift(90))
    f["sma50"] = np.log(p / p.rolling(50).mean())
    f["sma200"] = np.log(p / p.rolling(200).mean())
    f["rsi"] = rsi(p) / 100
    f["vol30"] = lr.rolling(30).std()
    return f


def ewma_sigma(p, lam=0.94):
    lr = np.log(p).diff().dropna()
    var = lr.pow(2).ewm(alpha=1 - lam, adjust=False).mean()
    return np.sqrt(var)


# ---------------- model ----------------
def walk_forward(X, y, test_days=730, step=30, min_train=400):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    data = X.join(y.rename("y")).dropna()
    if len(data) < min_train + 100:
        return None
    dates = data.index
    start = max(dates[min_train], dates[-1] - pd.Timedelta(days=test_days))
    preds = []
    t = start
    while t <= dates[-1]:
        tr = data[data.index <= t - pd.Timedelta(days=H)]   # yalnızca sonucu belli olmuş günler
        te = data[(data.index > t) & (data.index <= t + pd.Timedelta(days=step))]
        if len(tr) >= min_train and len(te):
            m = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=500))
            m.fit(tr.drop(columns="y"), tr["y"])
            p = m.predict_proba(te.drop(columns="y"))[:, 1]
            preds.append(pd.DataFrame({"p": p, "y": te["y"].values}, index=te.index))
        t += pd.Timedelta(days=step)
    if not preds:
        return None
    P = pd.concat(preds)
    conf = P[(P["p"] - 0.5).abs() >= 0.05]          # yalnızca modelin "emin" olduğu günler
    acc = float(((conf["p"] > 0.5) == (conf["y"] == 1)).mean()) if len(conf) else float("nan")
    base = float(max(P["y"].mean(), 1 - P["y"].mean()))
    return {"acc": acc, "base": base, "n": int(len(conf)), "n_all": int(len(P))}


def current_prob(X, y):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    data = X.join(y.rename("y")).dropna()
    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=500))
    m.fit(data.drop(columns="y"), data["y"])
    last = X.dropna().iloc[[-1]]
    return float(m.predict_proba(last)[:, 1][0]), last.index[0]


def range_coverage(p, test_days=730):
    sig = ewma_sigma(p)
    fut = np.log(p.shift(-H) / p)
    df = pd.DataFrame({"s": sig, "f": fut}).dropna()
    df = df[df.index >= df.index[-1] - pd.Timedelta(days=test_days)].iloc[::H]   # örtüşmeyen haftalar
    if len(df) < 20:
        return None
    inside = (df["f"].abs() <= Z80 * df["s"] * math.sqrt(H)).mean()
    return float(inside), int(len(df))


def pct(x):
    return f"%{abs(x) * 100:.1f}".replace(".", ",")


def trend_text(p):
    last = p.iloc[-1]
    s50, s200 = p.rolling(50).mean().iloc[-1], p.rolling(200).mean().iloc[-1]
    r28 = p.iloc[-1] / p.iloc[-29] - 1
    if last > s50 > s200 and r28 > 0:
        return "Yükselen"
    if last < s50 < s200 and r28 < 0:
        return "Düşen"
    return "Yatay / karışık"


def describe(p, extra):
    f = []
    s200 = p.rolling(200).mean().iloc[-1]
    d = p.iloc[-1] / s200 - 1
    f.append(f"Fiyat 200 günlük ortalamanın {pct(d)} {'üstünde' if d >= 0 else 'altında'}")
    r28 = p.iloc[-1] / p.iloc[-29] - 1
    f.append(f"Son 4 haftada {pct(r28)} {'yükseldi' if r28 >= 0 else 'düştü'}")
    r = rsi(p).iloc[-1]
    if r >= 70:
        f.append(f"RSI {r:.0f}: aşırı alım bölgesinde, kısa vadede yorulma riski")
    elif r <= 30:
        f.append(f"RSI {r:.0f}: aşırı satım bölgesinde, tepki yükselişi görülebilir")
    return f + extra


def analyze(name, p, extra_feats, extra_text):
    p = p.dropna()
    p = p[p > 0]
    X = base_features(p).join(extra_feats, how="left") if extra_feats is not None else base_features(p)
    X = X.ffill()
    y = (p.shift(-H) > p).astype(float).where(p.shift(-H).notna())
    wf = walk_forward(X, y)
    prob, asof = current_prob(X, y)
    cov = range_coverage(p)
    sig = float(ewma_sigma(p).iloc[-1])
    signal = None
    if wf and wf["n"] >= 150 and not math.isnan(wf["acc"]) and wf["acc"] >= wf["base"] + 0.03 and abs(prob - 0.5) >= 0.05:
        signal = "up" if prob > 0.5 else "down"
    res = {
        "trend": trend_text(p),
        "p_up": round(prob, 3),
        "signal": signal,
        "acc": None if not wf or math.isnan(wf["acc"]) else round(wf["acc"], 3),
        "base": None if not wf else round(wf["base"], 3),
        "n": None if not wf else wf["n"],
        "sig7": round(sig * math.sqrt(H), 5),
        "z": Z80,
        "cov": None if not cov else round(cov[0], 3),
        "cov_n": None if not cov else cov[1],
        "factors": describe(p, extra_text),
        "asof": asof.strftime("%Y-%m-%d"),
        "history_days": int(len(p)),
    }
    print(name, json.dumps(res, ensure_ascii=False))
    return res


def fg_label(v):
    return ("Aşırı korku" if v < 25 else "Korku" if v < 45 else "Nötr" if v <= 55
            else "Açgözlülük" if v <= 75 else "Aşırı açgözlülük")


def main():
    out = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "horizon_days": H, "assets": {}}

    # --- kripto ---
    fg = None
    try:
        fg = fear_greed()
    except Exception as ex:  # noqa
        print("Korku endeksi alınamadı:", ex)
    for sym in ["BTC", "ETH", "ETHFI"]:
        try:
            p = crypto_try(sym)
            extra, text = None, []
            if fg is not None and not fg.empty:
                extra = pd.DataFrame({"fg": fg / 100, "fg7": fg.diff(7) / 100})
                v = fg.iloc[-1]
                text.append(f"Kripto Korku & Açgözlülük Endeksi {v:.0f} ({fg_label(v)})")
            out["assets"][sym] = analyze(sym, p, extra, text)
        except Exception as ex:  # noqa
            print("Analiz olmadı:", sym, ex)

    # --- gram altın ---
    try:
        gram = from_market("GRAM")
        usd = from_market("USD")
        feats, text = {}, []
        try:
            ry = fred("DFII10")
            feats["ry28"] = ry.diff(28)
            c = ry.diff(28).dropna().iloc[-1]
            text.append(f"ABD reel faizi son 4 haftada {f"{abs(c):.2f}".replace(".", ",")} puan {'düştü (altın için genelde olumlu)' if c < 0 else 'yükseldi (altın için genelde olumsuz)'}")
        except Exception as ex:  # noqa
            print("Reel faiz alınamadı:", ex)
        try:
            dxy = fred("DTWEXBGS")
            feats["dxy28"] = np.log(dxy / dxy.shift(28))
            c = (dxy.iloc[-1] / dxy.dropna().iloc[-29] - 1)
            text.append(f"Dolar endeksi son 4 haftada {pct(c)} {'güçlendi (altın için genelde olumsuz)' if c > 0 else 'zayıfladı (altın için genelde olumlu)'}")
        except Exception as ex:  # noqa
            print("Dolar endeksi alınamadı:", ex)
        feats["try28"] = np.log(usd / usd.shift(28))
        out["assets"]["GRAM"] = analyze("GRAM", gram, pd.DataFrame(feats), text)
    except Exception as ex:  # noqa
        print("Altın analizi olmadı:", ex)

    if out["assets"]:
        OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print("Kaydedildi:", OUT)


if __name__ == "__main__":
    main()
