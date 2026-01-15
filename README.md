# teklif_listeleme

Basit bir teklif PDF listeleme aracı. PDF dosyalarını veya firma klasörlerini tarar, teklifin
hangi firmaya verildiğini, teklif konusunu ve tutarını SQLite veritabanına kaydeder. İstendiğinde özet tablo sunar.

## Kurulum

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Çalıştırma

```bash
streamlit run main.py
```

## Kullanım

- **PDF Ekle**: Seçtiğiniz PDF dosyalarını okur ve listeye ekler.
- **Klasör Tara**: Firma klasörlerini seçtiğinizde, sadece her firmanın `Teklifler` alt klasörü taranır.
- **Özet**: Firma ve konu bazında toplam tutarı listeler.
- **Sıfırla**: Kayıtlı tüm teklifleri temizlemek için veritabanını sıfırlar.

Veritabanı dosyası uygulama ile aynı dizinde `teklifler.db` olarak oluşturulur.
