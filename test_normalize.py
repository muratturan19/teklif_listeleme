#!/usr/bin/env python3
"""Test firm name normalization"""

# Test normalize_firm_name directly without importing full main.py
def test_title_case():
    test_cases = [
        ("PAYNA Grup", "Payna Grup"),
        ("PAKSAN", "Paksan"),
        ("payna grup", "Payna Grup"),
        ("PAK GIDA ÜRETIM VE PAZARLAMA A.Ş", "Pak Gıda Üretim Ve Pazarlama A.Ş"),
    ]

    print("Title Case testleri:")
    print("=" * 70)
    for original, expected in test_cases:
        result = original.title()
        status = "✓" if result == expected else "✗"
        print(f"{status} {original:45} → {result}")
        if result != expected:
            print(f"  Beklenen: {expected}")
    print()

test_title_case()

# Show what Python's .title() does
print("Python .title() davranışı:")
print("=" * 70)
examples = ["PAYNA Grup", "PAYNA", "Grup", "ÖZYAŞAR TEL GALVANİZ"]
for ex in examples:
    print(f"{ex:30} → {ex.title()}")
