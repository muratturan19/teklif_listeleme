#!/usr/bin/env python3
"""Test GTip PDFs to understand why they fail to parse"""
import sys
import os

# Add parent directory to path to import main
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pypdf import PdfReader  # Updated from PyPDF2

def test_pdf(pdf_path: str):
    """Test a single PDF to see what we can extract"""
    print("=" * 80)
    print(f"Testing: {os.path.basename(pdf_path)}")
    print("=" * 80)

    try:
        reader = PdfReader(pdf_path, strict=False)
        print(f"✓ PDF opened successfully ({len(reader.pages)} pages)")

        for page_num, page in enumerate(reader.pages[:3], start=1):
            print(f"\n--- Page {page_num} ---")
            try:
                text = page.extract_text()
                if text:
                    # Show first 500 characters
                    text_preview = text[:500].strip()
                    print(f"Text extracted ({len(text)} chars):")
                    print(text_preview)
                    print("...")

                    # Check for firm name patterns
                    if "GTip" in text or "G Tip" in text:
                        print("\n✓ Found 'GTip' in text")
                    if "Firma" in text or "Company" in text:
                        print("✓ Found 'Firma' or 'Company' in text")
                    if any(keyword in text for keyword in ["A.Ş", "A.S", "Ltd", "Inc"]):
                        print("✓ Found company type abbreviation")
                else:
                    print("⚠ No text extracted from this page")

            except UnicodeDecodeError as e:
                print(f"✗ Unicode error: {e}")
            except Exception as e:
                print(f"✗ Error extracting text: {e}")

    except Exception as e:
        print(f"✗ Failed to open PDF: {e}")

    print()

if __name__ == "__main__":
    # Test both GTip PDFs
    gtip_folder = r"E:\DELTA\GTip\Soya Yağı\Teklif"

    if not os.path.exists(gtip_folder):
        print(f"Folder not found: {gtip_folder}")
        print("Please run this script on the machine where the PDFs are located.")
        sys.exit(1)

    pdfs = [
        os.path.join(gtip_folder, "Proposal_Delta_EN.pdf"),
        os.path.join(gtip_folder, "Teklif_EN.pdf"),
    ]

    for pdf_path in pdfs:
        if os.path.exists(pdf_path):
            test_pdf(pdf_path)
        else:
            print(f"✗ File not found: {pdf_path}\n")

    print("\n" + "=" * 80)
    print("SONUÇ")
    print("=" * 80)
    print("""
Bu PDF'lerin firma adı çıkartılabilmesi için:
1. İlk 3 sayfada "Firma Adı:" veya benzeri bir etiket olmalı
2. VEYA: A.Ş, Ltd., Inc. gibi şirket kısaltması olmalı
3. VEYA: "Sayın XYZ" gibi bir greeting pattern olmalı

Eğer bu bilgiler PDF'de yoksa, manuel olarak firma adı eklemeniz gerekebilir.
    """)
