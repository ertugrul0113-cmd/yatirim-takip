# Yatırım Takip — Kurulum (bir kerelik, ~10 dakika)

Uygulama ücretsiz GitHub üzerinde çalışır. GitHub saatte bir döviz, altın ve TEFAS verisini kendisi çeker; telefonun sadece sayfayı açar. Bilgisayarının açık olmasına gerek yok.

1. github.com'da ücretsiz hesap aç.
2. Sağ üstte **+ → New repository**. Adı: `yatirim-takip`. **Public** seç (ücretsiz sayfa yayını için gerekli; içinde kişisel bilgin yok). **Create repository**.
3. Açılan sayfada **uploading an existing file** bağlantısına tıkla. Zip'ten çıkardığın klasörün **içindeki her şeyi** (`.github` klasörü dahil) sürükleyip bırak, **Commit changes**.
4. **Settings → Actions → General → Workflow permissions** bölümünde **Read and write permissions** seç, kaydet.
5. Üstteki **Actions** sekmesi → solda **Verileri guncelle** → **Run workflow**. İlk çalışma 5 yıllık fon geçmişini indirdiği için 5–15 dakika sürer; sonrakiler 1–2 dakika. Yeşil tik görünce tamam.
6. **Settings → Pages** → Source: **Deploy from a branch**, Branch: **main**, klasör **/(root)** → **Save**. Birkaç dakika sonra adresin hazır olur:
   `https://KULLANICI-ADIN.github.io/yatirim-takip/`
7. iPhone'da bu adresi **Safari** ile aç → **Paylaş** düğmesi → **Ana Ekrana Ekle**. Artık uygulama gibi açılır.

## Fon eklemek / çıkarmak
**Uygulamadan:** Fonlar sekmesinde **Fon ekle** → fon kodunu yaz → **GitHub'da ekle**. GitHub açılır, yeşil **Create** düğmesine bas. 5–10 dakika içinde fon uygulamada görünür ve GitHub'daki kayıt kendiliğinden kapanır. Çıkarmak için fonun detayında **Bu fonu listeden çıkar**. (GitHub'a giriş yapmış olman gerekir; başkalarının açtığı kayıtlar dikkate alınmaz.)

**Elle:** GitHub'da `fonlar.txt` dosyasını aç, kalem simgesine bas, her satıra bir fon kodu yaz, **Commit changes**. Bir sonraki saatlik güncellemede uygulamada görünür.

## Portföy ve fiyat alarmları
**Portföyüm** sekmesinde elindeki varlıkları ve istersen toplam alış maliyetini gir; toplam değer, günlük değişim ve kâr/zarar canlı hesaplanır. Alarmlar uygulama açıkken çalışır. Bilgiler sadece o telefonun tarayıcısında saklanır.

## Veriler nereden geliyor
- Bitcoin, Ethereum, ether.fi: Binance TL pariteleri, telefonda anlık.
- Dolar/TL, Euro/TL: Yahoo Finance, saatlik (canlı fiyat Binance'ten türetilir).
- Gram altın / gümüş: ons fiyatı × dolar kuru ÷ 31,10. Çeyrek altın: gram altın × 1,6035 (saf altın değeri).
- BIST 100: Yahoo Finance, saatlik.
- Enflasyon: TÜİK TÜFE aylık değişimleri (TCMB sayfasından), "Kim daha çok kazandırdı?" tablosundaki kesikli çizgi.
- Fonlar: tefas.gov.tr, günlük.
