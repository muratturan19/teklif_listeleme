#!/usr/bin/env python3
"""Debug GTip PDF parsing - shows exactly what's extracted"""
import sys
import os
import re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyPDF2 import PdfReader

def test_gtip_pdf():
    pdf_path = r"E:\DELTA\GTip\Soya Yağı\Teklif\Proposal_Delta_EN.pdf"

    if not os.path.exists(pdf_path):
        print(f"PDF not found: {pdf_path}")
        return

    print("=" * 80)
    print(f"Testing: {os.path.basename(pdf_path)}")
    print("=" * 80)

    try:
        reader = PdfReader(pdf_path, strict=False)
        print(f"✓ PDF opened ({len(reader.pages)} pages)\n")

        # Test page 2 (index 1) where header info is
        page_num = 2
        page = reader.pages[page_num - 1]

        print(f"--- Page {page_num} ---")
        try:
            text = page.extract_text()
            if text:
                # Show first 1000 chars
                print("Extracted text (first 1000 chars):")
                print(text[:1000])
                print("\n" + "=" * 80)

                # Test Company Name pattern
                print("\n🔍 Testing Company Name extraction:")
                company_pattern = re.compile(
                    r"(?:Company\s*Name)\s*[:\-]\s*(.+)",
                    re.IGNORECASE
                )
                match = company_pattern.search(text)
                if match:
                    raw = match.group(1)
                    print(f"✓ Raw match: '{raw}'")

                    # Apply cleanup
                    cleaned = re.split(
                        r"\s+(?:Your|Offer|Page|History|Topic|Reference)",
                        raw,
                        flags=re.IGNORECASE
                    )[0].strip()
                    print(f"✓ After cleanup: '{cleaned}'")
                else:
                    print("✗ Company Name pattern not found")
                    # Show lines containing "Company"
                    print("\nLines with 'Company':")
                    for line in text.splitlines()[:30]:
                        if "company" in line.lower():
                            print(f"  {line}")

                # Test Sum pattern
                print("\n🔍 Testing Sum extraction:")
                amount_patterns = [
                    re.compile(
                        r"(?:Sum|Total\s*(?:Price|Quote)?)\s*[:\-]?\s+([\d\.\,\s]{4,}?)\s*(€|EUR|\$)",
                        re.IGNORECASE
                    ),
                    re.compile(r"([\d\.\,]{4,})\s*€", re.IGNORECASE),
                ]

                found = False
                for i, pattern in enumerate(amount_patterns):
                    match = pattern.search(text)
                    if match:
                        print(f"✓ Pattern {i+1} matched:")
                        print(f"  Amount: '{match.group(1)}'")
                        if len(match.groups()) > 1:
                            print(f"  Currency: '{match.group(2)}'")
                        found = True
                        break

                if not found:
                    print("✗ No amount pattern matched")
                    print("\nLines with 'Sum' or numbers followed by €:")
                    for line in text.splitlines():
                        if "sum" in line.lower() or re.search(r"\d.*€", line):
                            print(f"  {line.strip()}")

        except Exception as e:
            print(f"✗ Error extracting page: {e}")

    except Exception as e:
        print(f"✗ Failed to open PDF: {e}")

if __name__ == "__main__":
    test_gtip_pdf()
