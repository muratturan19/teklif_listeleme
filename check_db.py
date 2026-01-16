#!/usr/bin/env python3
"""Check database for currency standardization issues"""
import sqlite3

DB_PATH = "teklifler.db"

with sqlite3.connect(DB_PATH) as conn:
    cursor = conn.cursor()

    print("=" * 60)
    print("PARA BİRİMİ DAĞILIMI")
    print("=" * 60)

    cursor.execute("""
        SELECT currency, COUNT(*) as count
        FROM teklifler
        GROUP BY currency
        ORDER BY count DESC
    """)

    for currency, count in cursor.fetchall():
        print(f"{currency or '(boş)':15} : {count} kayıt")

    print("\n" + "=" * 60)
    print("STANDART OLMAYAN KAYITLAR (küçük harfli para birimi)")
    print("=" * 60)

    cursor.execute("""
        SELECT id, firm, currency, amount
        FROM teklifler
        WHERE currency IS NOT NULL
          AND currency != UPPER(currency)
        LIMIT 10
    """)

    non_standard = cursor.fetchall()
    if non_standard:
        for record_id, firm, currency, amount in non_standard:
            print(f"ID {record_id}: {firm[:30]:30} | {currency:10} | {amount:,.2f}")
        print(f"\nToplam {len(non_standard)} standart olmayan kayıt bulundu.")
    else:
        print("✅ Tüm para birimleri standart formatta (BÜYÜK HARF)")

    print("\n" + "=" * 60)
    print("STANDART OLMAYAN FİRMA ADLARI (tamamı büyük harf)")
    print("=" * 60)

    cursor.execute("""
        SELECT id, firm, currency
        FROM teklifler
        WHERE firm IS NOT NULL
          AND firm != ''
          AND firm = UPPER(firm)
        LIMIT 10
    """)

    all_caps = cursor.fetchall()
    if all_caps:
        for record_id, firm, currency in all_caps:
            print(f"ID {record_id}: {firm[:40]:40} | {currency or '(boş)'}")
        print(f"\nToplam {len(all_caps)} tamamı büyük harfli firma adı bulundu.")
    else:
        print("✅ Tüm firma adları Title Case formatında")
