"""Yatırım Takip veri güncelleyici.
GitHub Actions bu dosyayı saatte bir çalıştırır ve data/market.json'u yeniler.
Döviz/altın: Yahoo Finance, TEFAS fonları: tefas.gov.tr (tefas-crawler).
Kripto verisi telefonda doğrudan Binance'ten anlık çekilir, burada yoktur.
"""
import datetime as dt
import functools
import json
import os
import threading
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "market.json"
FUNDS_FILE = ROOT / "fonlar.txt"
ONS_GRAM = 31.1034768
DEADLINE = time.time() + 20 * 60  # fonlar için toplam süre sınırı
print = functools.partial(print, flush=True)  # noqa: A001  kayıtlar anında görünsün


def yclose(ticker, period, interval):
    for _ in range(3):
        try:
            h = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
            if not h.empty:
                s = h["Close"].dropna()
                s = s[s > 0]
                return s
        except Exception as e:  # noqa
            print("HATA", ticker, interval, e)
        time.sleep(3)
    return pd.Series(dtype=float)


def daily_index(s):
    """Günlük veriyi borsanın kendi tarihine sabitle (saat dilimi kaymasını önler)."""
    if s.empty:
        return s
    s = s.copy()
    s.index = pd.to_datetime([d.date() for d in s.index]).tz_localize("UTC")
    return s[~s.index.duplicated(keep="last")]


def hourly_index(s):
    if s.empty:
        return s
    s = s.copy()
    s.index = s.index.tz_convert("UTC").floor("h")
    return s[~s.index.duplicated(keep="last")]


def to_points(s, digits=6):
    s = s.dropna().sort_index()
    return [[int(ts.timestamp()), round(float(v), digits)] for ts, v in s.items()]


def multiply(a, b):
    """a'nın olduğu her zaman için b'nin o andaki (veya son bilinen) değeriyle çarp."""
    if a.empty or b.empty:
        return pd.Series(dtype=float)
    idx = a.index.union(b.index)
    bb = b.reindex(idx).ffill()
    return (a * bb.reindex(a.index)).dropna()


def fx_and_gold(old):
    assets = {}
    raw = {}
    for key, tic in [("USD", "USDTRY=X"), ("EUR", "EURTRY=X"), ("ONS", "GC=F")]:
        raw[key] = {
            "h1": hourly_index(yclose(tic, "90d", "1h")),
            "d1": daily_index(yclose(tic, "6y", "1d")),
        }
    gram = {k: multiply(raw["ONS"][k], raw["USD"][k]) / ONS_GRAM for k in ("h1", "d1")}
    names = {"USD": "Dolar / TL", "EUR": "Euro / TL", "GRAM": "Gram Altın"}
    for key in names:
        src = gram if key == "GRAM" else raw[key]
        item = {"name": names[key], "h1": to_points(src["h1"]), "d1": to_points(src["d1"])}
        prev = old.get("assets", {}).get(key, {})
        for k in ("h1", "d1"):  # bu sefer çekilemediyse eski veriyi koru
            if not item[k] and prev.get(k):
                item[k] = prev[k]
        assets[key] = item
    return assets


def with_timeout(fn, seconds=60):
    """Takılan istekleri bekleme: süre dolarsa hata ver."""
    box = {}

    def run():
        try:
            box["v"] = fn()
        except Exception as ex:  # noqa
            box["e"] = ex

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"{seconds} sn içinde cevap gelmedi")
    if "e" in box:
        raise box["e"]
    return box["v"]


def read_fund_codes():
    if not FUNDS_FILE.exists():
        return []
    codes = []
    for line in FUNDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip().upper()
        if line and not line.startswith("#") and line not in codes:
            codes.append(line)
    return codes


def funds(old):
    from tefas import Crawler

    crawler = Crawler()
    today = dt.date.today()
    result = {}
    for code in read_fund_codes():
        prev = old.get("funds", {}).get(code, {})
        pts = {p[0]: p[1] for p in prev.get("d1", [])}
        title = prev.get("name", code)
        if pts:
            start = dt.datetime.utcfromtimestamp(max(pts)).date() - dt.timedelta(days=7)
        else:
            start = today - dt.timedelta(days=5 * 366 + 10)
        s = start
        fails = 0
        while s <= today:
            if time.time() > DEADLINE or fails >= 2:
                print("Fon atlandı (zaman/erişim):", code)
                break
            e = min(s + dt.timedelta(days=85), today)
            df = None
            for _ in range(2):
                try:
                    df = with_timeout(lambda: crawler.fetch(start=s.isoformat(), end=e.isoformat(), name=code,
                                                            columns=["date", "price", "title"]))
                    break
                except Exception as ex:  # noqa
                    print("TEFAS HATA", code, s, ex)
                    time.sleep(3)
            if df is None:
                fails += 1
            else:
                fails = 0
            print("Fon", code, s, "->", 0 if df is None else len(df), "satır")
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    price = float(r["price"])
                    if price > 0:
                        d = pd.Timestamp(r["date"]).date()
                        ts = int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp())
                        pts[ts] = round(price, 6)
                if str(df["title"].iloc[0]).strip():
                    title = str(df["title"].iloc[0]).strip()
            s = e + dt.timedelta(days=1)
            time.sleep(1)
        if pts:
            result[code] = {"name": title, "d1": [[t, pts[t]] for t in sorted(pts)]}
            print("Fon", code, len(pts), "gün")
        else:
            print("Fon bulunamadı:", code)
    return result


def main():
    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            old = {}
    data = {
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "assets": fx_and_gold(old),
        "funds": old.get("funds", {}),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("Döviz/altın kaydedildi")
    try:
        data["funds"] = funds(old)
    except Exception as ex:  # noqa
        print("Fonlar çekilemedi, eski veri korunuyor:", ex)
        data["funds"] = old.get("funds", {})
    if not data["funds"] and old.get("funds"):
        data["funds"] = old["funds"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("Kaydedildi:", OUT)


if __name__ == "__main__":
    main()
    os._exit(0)  # takılı kalmış arka plan isteklerini bekleme
