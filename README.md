# teklif_listeleme

Basit bir teklif PDF listeleme aracı. PDF dosyalarını veya bir klasörü (2 seviye alt klasöre kadar) tarar, teklifin
hangi firmaya verildiğini, teklif konusunu ve tutarını SQLite veritabanına kaydeder. İstendiğinde özet tablo sunar.

## Kurulum

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Çalıştırma

```bash
python main.py
```

## Kullanım

- **PDF Dosyası Ekle**: Seçtiğiniz PDF dosyalarını okur ve listeye ekler.
- **Klasör Tara**: Klasör yolunu seçtiğinizde, 2 seviye alt klasöre kadar PDF dosyalarını bulur ve listeye ekler.
- **Özet Tablo**: Firma ve konu bazında toplam tutarı listeler.
- **Listeyi Yenile**: Kayıtlı tüm teklifleri güncel görünümle listeler.

Veritabanı dosyası uygulama ile aynı dizinde `teklifler.db` olarak oluşturulur.
