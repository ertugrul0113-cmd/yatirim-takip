"""Yön tahmini ve korku endeksleri.

Yön tahmini (Bitcoin, Ethereum, ether.fi, gram altın): yarın (1 gün) ve önümüzdeki 1 hafta
için her zaman bir yön ve olasılık verir. Fiyat aralığı verilmez.
  Göstergeler: getiri/momentum, 20-50-200 günlük ortalamalar, RSI, MACD, Bollinger,
  oynaklık, işlem hacmi, Kripto Korku & Açgözlülük Endeksi, vadeli işlem fonlama oranı
  (erişilebilirse), S&P 500, VIX, dolar endeksi, ABD 10 yıllık faizi, USD/TRY.
  Model: lojistik regresyon + gradyan artırmalı ağaç ortalaması, yürüyen pencere
  (walk-forward) ile yalnızca modelin görmediği günlerde ölçülür; olasılıklar bu ölçüme
  göre kalibre edilir (Platt). Yani "%60" demek, geçmişte bu güçteki tahminlerin
  yaklaşık %60'ının tuttuğu anlamına gelir.

Korku endeksleri (0 = aşırı korku, 100 = aşırı açgözlülük):
  kripto: alternative.me Kripto Korku & Açgözlülük Endeksi (hazır endeks)
  bist:   programın hesabı (BIST 100 momentumu, 52 haftalık konumu, BIST hisselerinin
          genişliği, oynaklık, VIX, hisse-altın kıyası)
  altin:  programın hesabı (gram altın momentumu, 52 haftalık konumu, RSI, altın ve kur
          oynaklığı)
"""
import datetime as dt
import functools
import json
import math
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")
print = functools.partial(print, flush=True)  # noqa: A001

ROOT = Path(__file__).resolve().parent
MARKET = ROOT / "data" / "market.json"
OUT = ROOT / "data" / "tahmin.json"
HORIZONS = {"d1": 1, "w1": 7}
ONS_GRAM = 31.1034768
UA = {"User-Agent": "Mozilla/5.0 yatirim-takip"}
BIST_TICKERS = ["AKBNK", "ALARK", "ARCLK", "ASELS", "ASTOR", "BIMAS", "EKGYO", "ENKAI", "EREGL",
                "FROTO", "GARAN", "HEKTS", "ISCTR", "KCHOL", "KOZAL", "KRDMD", "MGROS", "PETKM",
                "PGSUS", "SAHOL", "SASA", "SISE", "TAVHL", "TCELL", "THYAO", "TOASO", "TTKOM",
                "TUPRS", "ULKER", "YKBNK"]


def get(url, tries=3, **kw):
    for i in range(tries):
        try:
            r = requests.get(url, timeout=30, headers=UA, **kw)
            r.raise_for_status()
            return r
        except Exception as ex:  # noqa
            print("İstek hatası", url[:80], ex)
            if i < tries - 1:
                time.sleep(3 * (i + 1))
    raise RuntimeError("erişilemedi: " + url[:80])


def daily(s):
    s = s.dropna().sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s.resample("D").last().ffill()


# ---------------- veri ----------------
def binance_daily(symbol):
    """Günlük kapanış ve işlem hacmi (karşı para cinsinden)."""
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
        raise RuntimeError("veri yok: " + symbol)
    idx = pd.to_datetime([k[0] for k in out], unit="ms")
    close = daily(pd.Series([float(k[4]) for k in out], index=idx))
    vol = daily(pd.Series([float(k[7]) for k in out], index=idx))
    return close, vol


def funding_daily(symbol):
    """Vadeli işlem fonlama oranı, günlük ortalama. Erişilemezse hata verir (kullanılmaz)."""
    out, start = [], 1567296000000  # 2019-09-01
    for _ in range(20):
        r = requests.get("https://fapi.binance.com/fapi/v1/fundingRate", timeout=15, headers=UA,
                         params={"symbol": symbol, "startTime": start, "limit": 1000})
        r.raise_for_status()
        j = r.json()
        if not j:
            break
        out += j
        if len(j) < 1000:
            break
        start = j[-1]["fundingTime"] + 1
    if not out:
        raise RuntimeError("fonlama verisi yok")
    idx = pd.to_datetime([d["fundingTime"] for d in out], unit="ms").normalize()
    s = pd.Series([float(d["fundingRate"]) for d in out], index=idx)
    return daily(s.groupby(level=0).mean())


def fear_greed():
    j = get("https://api.alternative.me/fng/", params={"limit": 0, "format": "json"}).json()["data"]
    idx = pd.to_datetime([int(d["timestamp"]) for d in j], unit="s").normalize()
    return daily(pd.Series([float(d["value"]) for d in j], index=idx))


_ycache = {}


def yahoo_raw(ticker, period="10y"):
    """İşlem günlerine göre kapanış (hafta sonu doldurulmaz)."""
    if ticker in _ycache:
        return _ycache[ticker]
    import yfinance as yf
    s = pd.Series(dtype=float)
    for _ in range(3):
        try:
            h = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
            s = h["Close"].dropna()
            s = s[s > 0]
            if not s.empty:
                break
        except Exception as ex:  # noqa
            print("Yahoo hatası", ticker, ex)
        time.sleep(3)
    if s.empty:
        raise RuntimeError("Yahoo verisi yok: " + ticker)
    s.index = pd.to_datetime([d.date() for d in s.index])
    s = s[~s.index.duplicated(keep="last")].sort_index()
    _ycache[ticker] = s
    return s


def yahoo(ticker):
    return daily(yahoo_raw(ticker))


def from_market(key, raw=False):
    m = json.loads(MARKET.read_text(encoding="utf-8"))
    pts = m["assets"][key]["d1"]
    idx = pd.to_datetime([dt.datetime.utcfromtimestamp(p[0] + 3 * 3600).date() for p in pts])
    s = pd.Series([p[1] for p in pts], index=idx)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s = s[s > 0]
    return s if raw else daily(s)


# ---------------- göstergeler ----------------
def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def macd_hist(p):
    m = p.ewm(span=12, adjust=False).mean() - p.ewm(span=26, adjust=False).mean()
    return m - m.ewm(span=9, adjust=False).mean()


def tech_features(p, vol=None):
    lr = np.log(p).diff()
    f = pd.DataFrame(index=p.index)
    for n in (1, 3, 7, 14, 28, 90):
        f[f"r{n}"] = np.log(p / p.shift(n))
    for n in (20, 50, 200):
        f[f"sma{n}"] = np.log(p / p.rolling(n).mean())
    f["rsi14"] = rsi(p, 14) / 100
    f["rsi7"] = rsi(p, 7) / 100
    f["macd"] = macd_hist(p) / p
    m20, sd20 = p.rolling(20).mean(), p.rolling(20).std()
    f["bb"] = (p - m20) / (2 * sd20)
    f["vol7"] = lr.rolling(7).std()
    f["vol30"] = lr.rolling(30).std()
    f["volr"] = np.log(f["vol7"] / f["vol30"])
    hi, lo = p.rolling(90).max(), p.rolling(90).min()
    f["pos90"] = (p - lo) / (hi - lo)
    if vol is not None:
        f["vlm"] = np.log(vol.rolling(7).mean() / vol.rolling(60).mean())
    return f.replace([np.inf, -np.inf], np.nan)


def macro_features():
    f = {}
    try:
        spx = yahoo("^GSPC")
        f["spx5"] = np.log(spx / spx.shift(5))
        f["spx20"] = np.log(spx / spx.shift(20))
        f["spx200"] = np.log(spx / spx.rolling(200).mean())
    except Exception as ex:  # noqa
        print("S&P 500 alınamadı:", ex)
    try:
        vix = yahoo("^VIX")
        f["vix"] = np.log(vix)
        f["vix5"] = np.log(vix / vix.shift(5))
    except Exception as ex:  # noqa
        print("VIX alınamadı:", ex)
    try:
        dxy = yahoo("DX-Y.NYB")
        f["dxy20"] = np.log(dxy / dxy.shift(20))
    except Exception as ex:  # noqa
        print("Dolar endeksi alınamadı:", ex)
    try:
        tnx = yahoo("^TNX")
        f["tnx20"] = tnx.diff(20)
    except Exception as ex:  # noqa
        print("Tahvil faizi alınamadı:", ex)
    return pd.DataFrame(f)


# ---------------- model ----------------
def make_pair():
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    lr = make_pipeline(StandardScaler(), LogisticRegression(C=0.05, max_iter=2000))
    gb = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.04, max_iter=150,
                                        min_samples_leaf=50, l2_regularization=1.0, random_state=0)
    return lr, gb


def fit_predict(tr_X, tr_y, te_X):
    lr, gb = make_pair()
    med = tr_X.median()
    lr.fit(tr_X.fillna(med).fillna(0), tr_y)
    gb.fit(tr_X, tr_y)
    p1 = lr.predict_proba(te_X.fillna(med).fillna(0))[:, 1]
    p2 = gb.predict_proba(te_X)[:, 1]
    return 0.5 * (p1 + p2)


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def walk_forward(X, y, h, step=30, min_train=500, test_days=1095):
    data = X.join(y.rename("y"))
    data = data[data["y"].notna()]
    data = data[data.drop(columns="y").notna().mean(axis=1) >= 0.6]
    if len(data) < min_train + 120:
        min_train = max(250, len(data) - 300)
    if len(data) < min_train + 60:
        return None
    dates = data.index
    start = max(dates[min_train], dates[-1] - pd.Timedelta(days=test_days))
    preds, t = [], start
    while t <= dates[-1]:
        tr = data[data.index <= t - pd.Timedelta(days=h)]  # yalnızca sonucu belli olmuş günler
        te = data[(data.index > t) & (data.index <= t + pd.Timedelta(days=step))]
        if len(tr) >= min_train and len(te):
            p = fit_predict(tr.drop(columns="y"), tr["y"], te.drop(columns="y"))
            preds.append(pd.DataFrame({"p": p, "y": te["y"].values}, index=te.index))
        t += pd.Timedelta(days=step)
    if not preds:
        return None
    return pd.concat(preds), data


def platt(P):
    from sklearn.linear_model import LogisticRegression
    c = LogisticRegression(C=1.0)
    c.fit(logit(P["p"]).reshape(-1, 1), P["y"].astype(int))
    return lambda p: c.predict_proba(logit(p).reshape(-1, 1))[:, 1]


def forecast(name, X, p, h):
    y = (p.shift(-h) > p).astype(float).where(p.shift(-h).notna())
    wf = walk_forward(X, y, h)
    if wf is None:
        print(name, h, "yeterli geçmiş yok")
        return None
    P, data = wf
    cal = platt(P)
    pc = cal(P["p"].values)
    hit = (pc >= 0.5) == (P["y"].values == 1)
    base = float(max(P["y"].mean(), 1 - P["y"].mean()))
    # güncel tahmin: sonucu belli olan tüm günlerle eğit
    last_X = X.iloc[[-1]]
    tr = data[data.index <= X.index[-1] - pd.Timedelta(days=h)]
    raw = fit_predict(tr.drop(columns="y"), tr["y"], last_X)
    cur = float(cal(raw)[0])
    conf = abs(cur - 0.5)
    pconf = np.abs(pc - 0.5)
    like = pconf >= max(0.0, conf - 0.02)
    if like.sum() < 40:  # benzer güçte yeterli örnek yoksa en güçlü 40 günü al
        like = pconf >= np.sort(pconf)[-min(40, len(pconf))]
    res = {
        "days": h,
        "p_up": round(cur, 3),
        "acc": round(float(hit.mean()), 3),
        "base": round(base, 3),
        "n": int(len(P)),
        "acc_like": round(float(hit[like].mean()), 3),
        "n_like": int(like.sum()),
        "from": P.index[0].strftime("%Y-%m-%d"),
    }
    print(name, f"{h}g", json.dumps(res, ensure_ascii=False))
    return res


# ---------------- açıklamalar ----------------
def pct(x):
    return f"%{abs(x) * 100:.1f}".replace(".", ",")


def fg_label(v):
    return ("Aşırı korku" if v < 25 else "Korku" if v < 45 else "Nötr" if v <= 55
            else "Açgözlülük" if v <= 75 else "Aşırı açgözlülük")


def trend_text(p):
    last = p.iloc[-1]
    s50, s200 = p.rolling(50).mean().iloc[-1], p.rolling(200).mean().iloc[-1]
    r28 = p.iloc[-1] / p.iloc[-29] - 1
    if last > s50 > s200 and r28 > 0:
        return "Yükselen"
    if last < s50 < s200 and r28 < 0:
        return "Düşen"
    return "Yatay / karışık"


def tech_text(p):
    """(metin, etki) listesi; etki: +1 yükseliş yönünde, -1 düşüş yönünde, 0 nötr."""
    f = []
    s200 = p.rolling(200).mean().iloc[-1]
    if not math.isnan(s200):
        d = p.iloc[-1] / s200 - 1
        f.append((f"Fiyat 200 günlük ortalamanın {pct(d)} {'üstünde' if d >= 0 else 'altında'}", 1 if d >= 0 else -1))
    s20 = p.rolling(20).mean().iloc[-1]
    d20 = p.iloc[-1] / s20 - 1
    f.append((f"Fiyat 20 günlük ortalamanın {pct(d20)} {'üstünde' if d20 >= 0 else 'altında'} (kısa vade)", 1 if d20 >= 0 else -1))
    r = rsi(p).iloc[-1]
    if r >= 70:
        f.append((f"RSI {r:.0f}: aşırı alım bölgesinde, kısa vadede yorulma riski", -1))
    elif r <= 30:
        f.append((f"RSI {r:.0f}: aşırı satım bölgesinde, tepki yükselişi görülebilir", 1))
    else:
        f.append((f"RSI {r:.0f}: nötr bölgede", 0))
    mh = macd_hist(p)
    if mh.iloc[-1] > 0:
        f.append(("MACD al tarafında" + (", güçleniyor" if mh.iloc[-1] > mh.iloc[-2] else ", zayıflıyor"), 1))
    else:
        f.append(("MACD sat tarafında" + (", güçleniyor" if mh.iloc[-1] < mh.iloc[-2] else ", zayıflıyor"), -1))
    return f


def macro_text(asset):
    f = []
    try:
        spx = yahoo("^GSPC")
        c = spx.iloc[-1] / spx.iloc[-21] - 1
        f.append((f"ABD borsası (S&P 500) son 20 günde {pct(c)} {'yükseldi' if c >= 0 else 'düştü'}",
                  (1 if c >= 0 else -1) if asset != "GRAM" else 0))
    except Exception:  # noqa
        pass
    try:
        vix = yahoo("^VIX").iloc[-1]
        f.append((f"Küresel korku göstergesi VIX {vix:.1f}".replace(".", ",") + (" (yüksek)" if vix >= 25 else " (sakin)" if vix < 16 else ""),
                  (-1 if vix >= 25 else 1 if vix < 16 else 0) if asset != "GRAM" else (1 if vix >= 25 else 0)))
    except Exception:  # noqa
        pass
    try:
        dxy = yahoo("DX-Y.NYB")
        c = dxy.iloc[-1] / dxy.iloc[-21] - 1
        f.append((f"Dolar endeksi son 20 günde {pct(c)} {'güçlendi' if c > 0 else 'zayıfladı'}", -1 if c > 0 else 1))
    except Exception:  # noqa
        pass
    if asset == "GRAM":
        try:
            tnx = yahoo("^TNX")
            c = tnx.diff(20).iloc[-1]
            f.append((f"ABD 10 yıllık faizi son 20 günde {abs(c):.2f} puan {'düştü' if c < 0 else 'yükseldi'}".replace(".", ","),
                      1 if c < 0 else -1))
        except Exception:  # noqa
            pass
    return f


# ---------------- korku endeksleri ----------------
def prank(s, win=500, minp=150):
    """Değerin son 'win' gündeki yüzdelik sırası (0-100)."""
    return s.rolling(win, min_periods=minp).apply(lambda a: (a[:-1] < a[-1]).mean() * 100, raw=True)


def combine(parts):
    """parts: [(ad, seri 0-100, açıklama)] -> sonuç sözlüğü."""
    df = pd.DataFrame({n: s for n, s, _ in parts}).dropna(how="all")
    ok = df.notna().sum(axis=1) >= max(3, len(parts) - 2)
    score = df.mean(axis=1).where(ok).dropna()
    if score.empty:
        raise RuntimeError("endeks hesaplanamadı")
    v = float(score.iloc[-1])
    comps = []
    for n, s, t in parts:
        sv = s.dropna()
        if len(sv):
            comps.append({"name": n, "value": round(float(sv.iloc[-1])), "text": t})
    return {
        "value": round(v), "label": fg_label(v),
        "prev1": round(float(score.iloc[-2])) if len(score) > 1 else None,
        "prev7": round(float(score.iloc[-6])) if len(score) > 5 else None,
        "asof": score.index[-1].strftime("%Y-%m-%d"),
        "components": comps,
    }


def fear_crypto(fg):
    v = float(fg.iloc[-1])
    return {"value": round(v), "label": fg_label(v),
            "prev1": round(float(fg.iloc[-2])), "prev7": round(float(fg.iloc[-8])),
            "asof": fg.index[-1].strftime("%Y-%m-%d"),
            "source": "alternative.me Kripto Korku & Açgözlülük Endeksi", "computed": False, "components": []}


def fear_bist():
    x = yahoo_raw("XU100.IS")
    lr = np.log(x).diff()
    parts = []
    ratio = x / x.rolling(125).mean()
    parts.append(("Momentum", prank(ratio),
                  f"BIST 100, 125 günlük ortalamasının {pct(ratio.iloc[-1] - 1)} {'üstünde' if ratio.iloc[-1] >= 1 else 'altında'}"))
    hi, lo = x.rolling(252).max(), x.rolling(252).min()
    pos = (x - lo) / (hi - lo) * 100
    parts.append(("52 haftalık konum", pos, f"Endeks son 1 yılın en düşüğü ile en yükseği arasında %{pos.iloc[-1]:.0f} noktasında"))
    vol = lr.rolling(20).std() * math.sqrt(252)
    parts.append(("Oynaklık", 100 - prank(vol), f"Son 20 günün yıllık oynaklığı %{vol.iloc[-1] * 100:.0f} (yüksek oynaklık korku sayılır)"))
    try:
        import yfinance as yf
        df = yf.download([t + ".IS" for t in BIST_TICKERS], period="3y", interval="1d",
                         auto_adjust=False, progress=False, threads=True)["Close"]
        df.index = pd.to_datetime([d.date() for d in df.index])
        df = df[~df.index.duplicated(keep="last")]
        m50 = df.rolling(50).mean()
        valid = m50.notna() & df.notna()
        br = ((df > m50) & valid).sum(axis=1) / valid.sum(axis=1).replace(0, np.nan) * 100
        br = br.dropna()
        parts.append(("Hisse genişliği", br, f"Büyük BIST hisselerinin %{br.iloc[-1]:.0f}'i 50 günlük ortalamasının üstünde"))
    except Exception as ex:  # noqa
        print("Hisse genişliği alınamadı:", ex)
    try:
        vix = yahoo_raw("^VIX").reindex(x.index).ffill()
        parts.append(("Küresel korku (VIX)", 100 - prank(vix), f"VIX {vix.iloc[-1]:.1f}".replace(".", ",")))
    except Exception as ex:  # noqa
        print("VIX alınamadı:", ex)
    try:
        gold = from_market("GRAM", raw=True).reindex(x.index).ffill()
        rel = np.log(x / x.shift(20)) - np.log(gold / gold.shift(20))
        parts.append(("Hisse mi altın mı", prank(rel),
                      f"Son 20 günde hisseler altından {pct(abs(rel.iloc[-1]))} {'iyi' if rel.iloc[-1] >= 0 else 'kötü'} gitti"))
    except Exception as ex:  # noqa
        print("Altın kıyası olmadı:", ex)
    out = combine(parts)
    out.update({"source": "Programın hesabı: BIST 100 ve büyük BIST hisseleri", "computed": True})
    return out


def fear_gold():
    g = from_market("GRAM", raw=True)
    lr = np.log(g).diff()
    parts = []
    ratio = g / g.rolling(125).mean()
    parts.append(("Momentum", prank(ratio),
                  f"Gram altın 125 günlük ortalamasının {pct(ratio.iloc[-1] - 1)} {'üstünde' if ratio.iloc[-1] >= 1 else 'altında'}"))
    hi, lo = g.rolling(252).max(), g.rolling(252).min()
    pos = (g - lo) / (hi - lo) * 100
    parts.append(("52 haftalık konum", pos, f"Gram altın son 1 yılın en düşüğü ile en yükseği arasında %{pos.iloc[-1]:.0f} noktasında"))
    r = rsi(g)
    parts.append(("RSI", r, f"14 günlük RSI {r.iloc[-1]:.0f}"))
    vol = lr.rolling(20).std() * math.sqrt(252)
    parts.append(("Altın oynaklığı", 100 - prank(vol), f"Gram altının yıllık oynaklığı %{vol.iloc[-1] * 100:.0f}"))
    try:
        usd = from_market("USD", raw=True)
        uv = np.log(usd).diff().rolling(20).std() * math.sqrt(252)
        uv = uv.reindex(g.index).ffill()
        parts.append(("Kur oynaklığı", 100 - prank(uv), f"Dolar/TL'nin yıllık oynaklığı %{uv.iloc[-1] * 100:.1f}".replace(".", ",")))
    except Exception as ex:  # noqa
        print("Kur oynaklığı olmadı:", ex)
    out = combine(parts)
    out.update({"source": "Programın hesabı: gram altın ve dolar/TL", "computed": True})
    return out


# ---------------- ana akış ----------------
def analyze(name, p, X, factors, ccy):
    res = {"ccy": ccy, "trend": trend_text(p), "h": {},
           "factors": [{"t": t, "e": e} for t, e in factors]}
    for key, h in HORIZONS.items():
        try:
            r = forecast(name, X, p, h)
            if r:
                res["h"][key] = r
        except Exception as ex:  # noqa
            print("Tahmin olmadı:", name, key, ex)
    return res if res["h"] else None


def main():
    t0 = time.time()
    out = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "assets": {}, "fear": {}}
    fg = None
    try:
        fg = fear_greed()
        out["fear"]["kripto"] = fear_crypto(fg)
    except Exception as ex:  # noqa
        print("Kripto korku endeksi alınamadı:", ex)
    for key, fn in (("bist", fear_bist), ("altin", fear_gold)):
        try:
            out["fear"][key] = fn()
            print("Korku endeksi", key, out["fear"][key]["value"])
        except Exception as ex:  # noqa
            print("Korku endeksi olmadı:", key, ex)

    macro = macro_features()

    # --- kripto (dolar bazında) ---
    btc_r = None
    for sym in ["BTC", "ETH", "ETHFI"]:
        try:
            p, vol = binance_daily(sym + "USDT")
            X = tech_features(p, vol)
            if btc_r is not None:
                X = X.join(btc_r, how="left")
            if fg is not None:
                X["fg"] = fg / 100
                X["fg7"] = fg.diff(7) / 100
            if not macro.empty:
                X = X.join(macro, how="left")
            ftext = []
            try:
                fr = funding_daily(sym + "USDT")
                X["fund"] = fr * 1000
                X["fund7"] = fr.rolling(7).mean() * 1000
                v = fr.rolling(7).mean().iloc[-1] * 3 * 365 * 100
                ftext.append((f"Vadeli işlem fonlama oranı yıllık %{v:.1f}".replace(".", ",") +
                              (" (kaldıraçlı alıcılar kalabalık)" if v > 20 else " (satıcılar baskın)" if v < 0 else ""),
                              -1 if v > 20 else 1 if v < 0 else 0))
            except Exception as ex:  # noqa
                print("Fonlama oranı alınamadı:", sym, ex)
            X = X.ffill(limit=5)
            factors = tech_text(p) + ftext
            if fg is not None:
                v = fg.iloc[-1]
                factors.append((f"Kripto Korku & Açgözlülük Endeksi {v:.0f} ({fg_label(v)})", 0))
            factors += macro_text(sym)
            r = analyze(sym, p, X, factors, "USD")
            if r:
                out["assets"][sym] = r
            if sym == "BTC":
                btc_r = pd.DataFrame({"btc7": np.log(p / p.shift(7)), "btc28": np.log(p / p.shift(28))})
        except Exception as ex:  # noqa
            print("Analiz olmadı:", sym, ex)

    # --- gram altın (TL) ---
    try:
        gram = from_market("GRAM")
        usd = from_market("USD")
        X = tech_features(gram)
        X["try28"] = np.log(usd / usd.shift(28))
        if not macro.empty:
            X = X.join(macro, how="left")
        X = X.ffill(limit=5)
        r = analyze("GRAM", gram, X, tech_text(gram) + macro_text("GRAM"), "TRY")
        if r:
            out["assets"]["GRAM"] = r
    except Exception as ex:  # noqa
        print("Altın analizi olmadı:", ex)

    if out["assets"] or out["fear"]:
        OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print("Kaydedildi:", OUT, f"{time.time() - t0:.0f} sn")


if __name__ == "__main__":
    main()
