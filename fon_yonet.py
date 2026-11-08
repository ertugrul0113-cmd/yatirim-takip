"""Uygulamadan açılan GitHub kaydına göre fonlar.txt'yi düzenler.

Kayıt başlığı "fon-ekle: ABC" veya "fon-sil: ABC" olmalı.
  python fon_yonet.py duzenle   -> fonlar.txt'yi değiştirir
  python fon_yonet.py kontrol   -> güncellemeden sonra sonucu yazar
Sonuç GitHub Actions çıktısına (ok, code, msg) yazılır.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FUNDS_FILE = ROOT / "fonlar.txt"
MARKET = ROOT / "data" / "market.json"


def output(**kw):
    path = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{k}={str(v).replace(chr(10), ' ')}" for k, v in kw.items()]
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def parse():
    m = re.match(r"^\s*fon-(ekle|sil)\s*:\s*([A-Za-z0-9]{3})\s*$", os.environ.get("TITLE", ""))
    if not m:
        return None, None
    return m.group(1), m.group(2).upper()


def duzenle():
    action, code = parse()
    if not action:
        output(ok="false", msg="Başlık anlaşılamadı. 'fon-ekle: ABC' veya 'fon-sil: ABC' biçiminde olmalı.")
        return
    lines = FUNDS_FILE.read_text(encoding="utf-8").splitlines() if FUNDS_FILE.exists() else []
    codes = [x.strip().upper() for x in lines if x.strip() and not x.strip().startswith("#")]
    if action == "ekle":
        if code in codes:
            output(ok="false", code=code, msg=f"{code} zaten listede.")
            return
        lines.append(code)
    else:
        if code not in codes:
            output(ok="false", code=code, msg=f"{code} listede yok.")
            return
        lines = [x for x in lines if x.strip().upper() != code]
    FUNDS_FILE.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    output(ok="true", code=code, action=action)


def kontrol():
    action, code = parse()
    funds = {}
    try:
        funds = json.loads(MARKET.read_text(encoding="utf-8")).get("funds", {})
    except Exception:  # noqa
        pass
    if action == "ekle":
        if code in funds:
            output(msg=f"{code} eklendi ({funds[code].get('name', '')}). Uygulamayı yenileyince görünür.")
        else:
            output(msg=f"{code} listeye yazıldı ama TEFAS'ta verisi bulunamadı. Kodu kontrol et; yanlışsa uygulamadan silebilirsin.")
    else:
        output(msg=f"{code} listeden çıkarıldı.")


if __name__ == "__main__":
    {"duzenle": duzenle, "kontrol": kontrol}[sys.argv[1]]()
