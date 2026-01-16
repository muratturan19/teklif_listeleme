import logging
import os
import re
import sqlite3
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

import pandas as pd
import streamlit as st
from PyPDF2 import PdfReader
from tkinter import filedialog

DB_PATH = "teklifler.db"
LOG_PATH = "teklif_listeleme.log"
OFFER_FOLDER_PATTERN = re.compile(r"teklif", re.IGNORECASE)

FIRM_PATTERNS = [
    # Turkish patterns
    re.compile(r"(?:Firma\s*Adı|Firma|Şirket|Müşteri|Kurum|Kuruluş)\s*[:\-]?\s*(.+)", re.IGNORECASE),
    # English patterns
    re.compile(r"(?:Company\s*Name|Company|Client|Customer|Organization)\s*[:\-]?\s*(.+)", re.IGNORECASE),
    # Company type abbreviations (works for both Turkish and English)
    re.compile(r"(.+?(?:A\.Ş\.|A\.S\.|Ltd\.?\s*Şti\.?|San\.|Tic\.|Ltd\.|Inc\.|Corp\.|GmbH))", re.IGNORECASE),
]

GREETINGS_PATTERN = re.compile(r"(?:Sayın|Dear)\s+(.+)", re.IGNORECASE)

SUBJECT_PATTERNS = [
    # Turkish patterns
    re.compile(r"Konu\s*[:\-]?\s*(.+)", re.IGNORECASE),
    re.compile(r"Teklif\s*Konusu\s*[:\-]?\s*(.+)", re.IGNORECASE),
    re.compile(r"İlgi\s*[:\-]?\s*(.+)", re.IGNORECASE),
    # English patterns
    re.compile(r"Subject\s*[:\-]?\s*(.+)", re.IGNORECASE),
    re.compile(r"Regarding\s*[:\-]?\s*(.+)", re.IGNORECASE),
    re.compile(r"Project\s*[:\-]?\s*(.+)", re.IGNORECASE),
    # Both languages
    re.compile(r"(?:Re|RE|Ref)\s*[:\-]?\s*(.+)", re.IGNORECASE),
]

AMOUNT_PATTERNS = [
    # Turkish patterns - Match amount with currency after keywords
    # Example: "Toplam Tutar: 1.677 289,00 Euro" or "Teklif Tutarı: 157.500 €"
    re.compile(
        r"(?:Toplam\s*(?:Tutar|Fiyat)?|Teklif\s*Tutarı|Tutar)\s*[:\-]?\s*(?:\([^\)]*\))?\s*([\d\.\,\s]{4,}?)\s*(€|TL|₺|USD|EUR|euro)",
        re.IGNORECASE,
    ),
    # English patterns - Match amount with currency after keywords
    # Support large whitespace for table formatting: "Sum                    2.125.400€"
    # Example: "Sum: 1,925.000€" or "Total Price: 1.925.000€" or "Grand Total: 1,925.000€"
    re.compile(
        r"(?:Sum|Total\s*(?:Price|Quote|Cost|Amount)?|Grand\s*Total)\s*[:\-]?\s*(?:\([^\)]*\))?\s+([\d\.\,\s]{4,}?)\s*(€|TL|₺|USD|EUR|euro|\$)",
        re.IGNORECASE,
    ),
    # Match large amounts with currency (minimum 4 characters, support spaces)
    # This catches amounts without keywords
    re.compile(r"([\d\.\,\s]{4,}?)\s*(€|TL|₺|USD|EUR|euro|\$)", re.IGNORECASE),
]

OFFER_KEYWORD_PATTERN = re.compile(r"\b(?:teklif|offer|quote|proposal)\b", re.IGNORECASE)

# Firm name abbreviations for standardization
_ABBREVIATIONS = {
    "a.ş": "A.Ş",
    "a.ş.": "A.Ş.",
    "ltd": "Ltd.",
    "ltd.": "Ltd.",
    "şti": "Şti.",
    "şti.": "Şti.",
    "inc": "Inc.",
    "inc.": "Inc.",
    "gmbh": "GmbH",
}
# Pre-compile regex patterns for performance
_COMPILED_ABBR_PATTERNS = [
    (re.compile(re.escape(k), re.IGNORECASE), v) for k, v in _ABBREVIATIONS.items()
]

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",  # Fix Turkish character encoding in logs
)


@dataclass
class OfferRecord:
    file_path: str
    firm: str
    subject: str
    amount: float | None
    currency: str | None


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS teklifler (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE,
                firm TEXT,
                subject TEXT,
                amount REAL,
                currency TEXT,
                extracted_at TEXT
            )
            """
        )


def reset_db() -> None:
    """Clear all records from database without deleting the file"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM teklifler")
        conn.commit()
    logging.info("Veritabanı sıfırlandı.")


def standardize_existing_records() -> int:
    """Standardize firm names and currency in existing database records.

    Returns the number of records updated.
    """
    init_db()
    updated_count = 0

    with sqlite3.connect(DB_PATH) as conn:
        # Use separate cursors: one for SELECT, one for UPDATE
        # This prevents cursor conflict when iterating and updating simultaneously
        select_cursor = conn.cursor()
        update_cursor = conn.cursor()

        # Fixed: Use correct column names from schema (firm, currency not firma, para_birimi)
        select_cursor.execute("SELECT id, firm, currency FROM teklifler")

        # Iterate over SELECT cursor, update with UPDATE cursor
        for record_id, firm, currency in select_cursor:
            # Standardize firm name
            normalized_firm = normalize_firm_name(firm) if firm else firm

            # Standardize currency using helper function
            normalized_currency = normalize_currency(currency)

            # Update if changed (using separate cursor)
            if normalized_firm != firm or normalized_currency != currency:
                update_cursor.execute(
                    "UPDATE teklifler SET firm = ?, currency = ? WHERE id = ?",
                    (normalized_firm, normalized_currency, record_id)
                )
                updated_count += 1

        conn.commit()

    logging.info(f"Standardizasyon tamamlandı: {updated_count} kayıt güncellendi.")
    return updated_count


def extract_page_text(page, path: str, page_number: int) -> str:
    try:
        return sanitize_text(page.extract_text() or "")
    except Exception as exc:  # noqa: BLE001
        logging.warning(
            "Sayfa metni okunamadı: %s (sayfa %s): %s",
            path,
            page_number,
            sanitize_text(str(exc)),
        )
        return ""


def load_pdf_reader(path: str) -> PdfReader | None:
    try:
        return PdfReader(path, strict=False)
    except Exception as exc:  # noqa: BLE001
        logging.warning("PDF okunamadı: %s (%s)", path, sanitize_text(str(exc)))
        return None


def extract_text_from_pdf(path: str) -> str:
    reader = load_pdf_reader(path)
    if reader is None:
        return ""
    chunks: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        text = extract_page_text(page, path, index)
        if text.strip():
            chunks.append(text)
    return "\n".join(chunks)


def extract_pages_from_pdf(path: str) -> list[str]:
    reader = load_pdf_reader(path)
    if reader is None:
        return []
    chunks: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        text = extract_page_text(page, path, index)
        chunks.append(text)
    return chunks


def sanitize_text(value: str) -> str:
    # Remove surrogate characters and other problematic Unicode
    return value.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")


def normalize_currency(currency: str | None) -> str | None:
    """Normalize currency code to uppercase standard format.

    Args:
        currency: Raw currency string (e.g., "Eur", "€", "TL")

    Returns:
        Standardized currency code (e.g., "EUR", "TL", "USD") or None
    """
    if not currency:
        return None

    currency_upper = currency.upper().strip()
    if currency_upper in ("€", "EURO", "EUR"):
        return "EUR"
    if currency_upper in ("₺", "TL", "TRY"):
        return "TL"
    if currency_upper == "USD":
        return "USD"
    return currency_upper


def normalize_firm_name(firm: str) -> str:
    """Normalize firm name for consistency.

    Applies title case but preserves common business abbreviations
    like A.Ş, Ltd., Inc., etc. Uses pre-compiled regex patterns for performance.
    """
    if not firm or len(firm) <= 2:
        return firm

    # Apply title case
    normalized = firm.title()

    # Fix common abbreviations using pre-compiled patterns
    for pattern, replacement in _COMPILED_ABBR_PATTERNS:
        normalized = pattern.sub(replacement, normalized)

    return normalized.strip()


def extract_field(patterns: list[re.Pattern], text: str) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            value = match.group(1).strip()
            # Split by newline or common field separators
            value = re.split(r"\n|\r", value)[0].strip()
            # Remove trailing noise - both Turkish and English stopwords
            # Turkish: Referansınız, Teklif No, Tarih, Sayfa
            # English: Your Reference, Offer No, Page, History, Topic
            value = re.split(
                r"\s+(?:Referans|Teklif\s*No|Tarih|Sayfa|Your|Offer|Page|History|Topic)",
                value,
                flags=re.IGNORECASE
            )[0].strip()
            return value
    return ""


def extract_firm(pages_text: list[str]) -> str:
    if not pages_text:
        return ""

    # Try first 3 pages (in case first page is cover image)
    for page_idx, page_text in enumerate(pages_text[:3]):
        lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        if not lines:
            continue  # Skip empty pages

        # Try to find "Firma Adı:" or "Company Name:" in first 20 lines
        for i, line in enumerate(lines[:20]):
            # Search for both Turkish and English firm labels
            if re.search(r"(?:Firma\s*Adı|Firma|Company\s*Name)\s*[:\-]", line, re.IGNORECASE):
                logging.debug(f"Firma etiketi bulundu (sayfa {page_idx + 1}): {line}")
                # Try to get firm name from same line after colon
                match = re.search(r"(?:Firma\s*Adı|Firma|Company\s*Name)\s*[:\-]\s*(.+)", line, re.IGNORECASE)
                if match:
                    firm = match.group(1).strip()
                    logging.debug(f"Aynı satırdan firma çıkartıldı (ham): {firm}")
                    # Clean up trailing noise - both Turkish and English
                    firm = re.split(
                        r"\s+(?:Referans|Teklif\s*No|Tarih|Sayfa|Your|Offer|Page|History|Topic)",
                        firm,
                        flags=re.IGNORECASE
                    )[0].strip()
                    logging.debug(f"Temizlenmiş firma: {firm}")
                    if firm and len(firm) > 2:
                        return normalize_firm_name(firm)
                # If not found on same line, check next line
                if i + 1 < len(lines):
                    firm = lines[i + 1].strip()
                    logging.debug(f"Sonraki satırdan firma çıkartıldı: {firm}")
                    firm = re.split(
                        r"\s+(?:Referans|Teklif\s*No|Tarih|Sayfa|Your|Offer|Page|History|Topic)",
                        firm,
                        flags=re.IGNORECASE
                    )[0].strip()
                    if firm and len(firm) > 2:
                        return normalize_firm_name(firm)

        # Fallback to header block extraction for this page
        header_block = "\n".join(lines[:12])
        firm = extract_field(FIRM_PATTERNS, header_block)
        if firm and len(firm) > 2:
            logging.debug(f"Header block'tan firma (sayfa {page_idx + 1}): {firm}")
            return normalize_firm_name(firm)

        # Try greetings pattern
        for line in lines[:15]:
            match = GREETINGS_PATTERN.search(line)
            if not match:
                continue
            candidate = match.group(1).strip()
            if re.search(r"\b(hanım|bey)\b", candidate, re.IGNORECASE):
                continue
            if len(candidate) > 2:
                logging.debug(f"Greetings pattern'den firma (sayfa {page_idx + 1}): {candidate}")
                return normalize_firm_name(candidate)

    logging.warning("Firma adı bulunamadı. İlk 3 sayfa kontrol edildi.")
    return ""


def extract_subject(pages_text: list[str]) -> str:
    if not pages_text:
        return ""

    # Try first 3 pages (in case first page is cover image)
    for page_idx, page_text in enumerate(pages_text[:3]):
        lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        if not lines:
            continue  # Skip empty pages

        # Try to find "Konu:" label in first 25 lines
        for i, line in enumerate(lines[:25]):
            if re.search(r"(?:Konu|Teklif\s*Konusu)\s*[:\-]", line, re.IGNORECASE):
                # Extract subject from same line or next line
                match = re.search(r"(?:Konu|Teklif\s*Konusu)\s*[:\-]\s*(.+)", line, re.IGNORECASE)
                if match:
                    subject = match.group(1).strip()
                    return subject[:200]  # Max 200 chars
                # Check next line if not on same line
                if i + 1 < len(lines):
                    return lines[i + 1].strip()[:200]

        # Fallback to header block
        header_block = "\n".join(lines[:18])
        subject = extract_field(SUBJECT_PATTERNS, header_block)
        if subject:
            return subject

    return ""


def parse_amount(raw_amount: str, currency: str | None) -> tuple[float | None, str | None]:
    # Turkish format: 27.560,50 or 27.560 or "1.677 289,00" (dot/space=thousands, comma=decimal)
    # English format: 27,560.50 or 27.560 (comma=thousands, dot=decimal)

    # Remove all spaces first (support formats like "1.677 289,00")
    raw_amount = raw_amount.replace(" ", "").strip()

    # Reject amounts that are too small (likely noise like "1.00", "2.00")
    if len(raw_amount.replace(".", "").replace(",", "")) < 3:
        return None, None

    # If both comma and dot exist, determine which is decimal separator
    if "," in raw_amount and "." in raw_amount:
        # Check which comes last (that's the decimal separator)
        last_comma_pos = raw_amount.rfind(",")
        last_dot_pos = raw_amount.rfind(".")
        if last_comma_pos > last_dot_pos:
            # Turkish: dot=thousands, comma=decimal (e.g., 1.234,56)
            normalized = raw_amount.replace(".", "").replace(",", ".")
        else:
            # English: comma=thousands, dot=decimal (e.g., 1,234.56)
            normalized = raw_amount.replace(",", "")
    elif "," in raw_amount:
        # Only comma - assume Turkish decimal separator (e.g., 1234,56)
        normalized = raw_amount.replace(",", ".")
    elif "." in raw_amount:
        # Only dot - check if it's thousands or decimal separator
        parts = raw_amount.split(".")
        # If last part has exactly 3 digits, it's likely thousands separator (e.g., 27.560)
        if len(parts) > 1 and len(parts[-1]) == 3:
            # Turkish thousands separator (e.g., 27.560 = 27560)
            normalized = raw_amount.replace(".", "")
        else:
            # Decimal separator (e.g., 27.5 or 27.56)
            normalized = raw_amount
    else:
        normalized = raw_amount

    try:
        amount = float(normalized)
        # Normalize currency using helper function
        normalized_currency = normalize_currency(currency)
        return amount, normalized_currency
    except ValueError:
        return None, None


def extract_amount_from_pages(pages_text: list[str]) -> tuple[float | None, str | None]:
    for pattern in AMOUNT_PATTERNS:
        for page_text in pages_text:
            match = pattern.search(page_text)
            if not match:
                continue
            raw_amount = match.group(1).strip()
            currency = match.group(2) if match.lastindex and match.lastindex >= 2 else None
            amount, normalized_currency = parse_amount(raw_amount, currency)
            if amount is not None:
                return amount, normalized_currency
    return None, None


def looks_like_offer(firm: str, subject: str, amount: float | None) -> bool:
    """Check if extracted data looks like a valid offer.

    Requires at least 2 out of 3 fields (firm, subject, amount) to be found.
    This prevents random PDFs from being classified as offers.
    """
    found_fields = 0

    if firm and len(firm) > 2:
        found_fields += 1
    if subject and len(subject) > 2:
        found_fields += 1
    if amount is not None:
        found_fields += 1

    # Accept if we found at least 2 out of 3 key fields
    return found_fields >= 2


def parse_offer(path: str) -> OfferRecord | None:
    pages_text = extract_pages_from_pdf(path)
    firm = extract_firm(pages_text)
    subject = extract_subject(pages_text)
    amount, currency = extract_amount_from_pages(pages_text)
    if not looks_like_offer(firm, subject, amount):
        return None
    return OfferRecord(
        file_path=path,
        firm=firm,
        subject=subject,
        amount=amount,
        currency=currency,
    )


def save_offer(record: OfferRecord) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO teklifler (file_path, firm, subject, amount, currency, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.file_path,
                record.firm,
                record.subject,
                record.amount,
                record.currency,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def get_existing_file_paths() -> set[str]:
    """Get set of file paths that are already in the database.

    Returns:
        Set of absolute file paths already processed
    """
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM teklifler")
        return {row[0] for row in cursor.fetchall()}


def load_offers() -> list[OfferRecord]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT file_path, firm, subject, amount, currency FROM teklifler ORDER BY extracted_at DESC"
        ).fetchall()
    return [OfferRecord(*row) for row in rows]


def load_summary() -> list[tuple[str, str, float]]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT firm, subject, COALESCE(SUM(amount), 0)
            FROM teklifler
            GROUP BY firm, subject
            ORDER BY firm, subject
            """
        ).fetchall()
    return rows


def get_offers_dataframe() -> pd.DataFrame:
    """Get all offers as pandas DataFrame for Excel export"""
    offers = load_offers()
    if not offers:
        return pd.DataFrame()

    data = {
        "Firma": [o.firm for o in offers],
        "Konu": [o.subject for o in offers],
        "Tutar": [o.amount if o.amount is not None else 0 for o in offers],
        "Para Birimi": [o.currency or "" for o in offers],
        "Dosya Yolu": [o.file_path for o in offers],
    }
    return pd.DataFrame(data)


def get_dashboard_stats() -> dict:
    """Get statistics for dashboard"""
    with sqlite3.connect(DB_PATH) as conn:
        total_offers = conn.execute("SELECT COUNT(*) FROM teklifler").fetchone()[0]
        total_firms = conn.execute("SELECT COUNT(DISTINCT firm) FROM teklifler WHERE firm != ''").fetchone()[0]

        # Amount by currency
        amounts_by_currency = conn.execute(
            """
            SELECT currency, COALESCE(SUM(amount), 0), COUNT(*)
            FROM teklifler
            WHERE amount IS NOT NULL
            GROUP BY currency
            ORDER BY SUM(amount) DESC
            """
        ).fetchall()

        # Top firms by total amount
        top_firms = conn.execute(
            """
            SELECT firm, COALESCE(SUM(amount), 0) as total, COUNT(*) as count
            FROM teklifler
            WHERE firm != ''
            GROUP BY firm
            ORDER BY total DESC
            LIMIT 10
            """
        ).fetchall()

    return {
        "total_offers": total_offers,
        "total_firms": total_firms,
        "amounts_by_currency": amounts_by_currency,
        "top_firms": top_firms,
    }


def walk_pdf_files(folder: str) -> list[str]:
    pdf_files: list[str] = []
    for root, _, files in os.walk(folder):
        for file in files:
            if file.lower().endswith(".pdf"):
                pdf_files.append(os.path.join(root, file))
    return pdf_files


def is_offer_folder(name: str) -> bool:
    return bool(OFFER_FOLDER_PATTERN.search(name))


def iter_offer_folders(root_folder: str) -> Iterable[str]:
    if not os.path.isdir(root_folder):
        return []
    for entry in os.listdir(root_folder):
        company_path = os.path.join(root_folder, entry)
        if not os.path.isdir(company_path):
            continue
        offers_folder = None
        for sub_entry in os.listdir(company_path):
            if is_offer_folder(sub_entry):
                offers_folder = os.path.join(company_path, sub_entry)
                break
        if offers_folder and os.path.isdir(offers_folder):
            logging.info("Teklif klasörü bulundu: %s", offers_folder)
            yield offers_folder
        else:
            logging.info("Teklif klasörü bulunamadı: %s", company_path)


def scan_company_offer_pdfs(root_folder: str) -> list[str]:
    """Scan for PDF files in offer folders at depths 0-3.

    Searches for folders matching 'teklif*' pattern (case-insensitive)
    at 0, 1, 2, and 3 levels deep within root_folder, and scans PDFs only
    in those matched folders.

    Example:
      E:/DELTA/Teklifler (depth 0)
      E:/DELTA/FirmaAdi/Teklifler (depth 1)
      E:/DELTA/FirmaAdi/Subdir/Teklifler (depth 2)
      E:/DELTA/GTip/Soya Yağı/Teklif (depth 3)
    """
    pdf_files: list[str] = []

    if not os.path.isdir(root_folder):
        return pdf_files

    def find_teklif_folders(base_path: str, current_depth: int, max_depth: int = 3) -> list[str]:
        """Recursively find folders matching teklif pattern up to max_depth."""
        teklif_folders = []

        if current_depth > max_depth:
            return teklif_folders

        try:
            entries = os.listdir(base_path)
        except (PermissionError, OSError):
            return teklif_folders

        for entry in entries:
            entry_path = os.path.join(base_path, entry)

            if not os.path.isdir(entry_path):
                continue

            # Check if this folder matches the teklif pattern
            if is_offer_folder(entry):
                logging.info("Teklif klasörü bulundu (seviye %d): %s", current_depth, entry_path)
                teklif_folders.append(entry_path)

            # Continue searching deeper (even if current folder matched)
            if current_depth < max_depth:
                teklif_folders.extend(find_teklif_folders(entry_path, current_depth + 1, max_depth))

        return teklif_folders

    # Check if root_folder itself matches teklif pattern (depth 0)
    if is_offer_folder(os.path.basename(root_folder)):
        logging.info("Teklif klasörü bulundu (seviye 0): %s", root_folder)
        teklif_folders = [root_folder]
    else:
        # Search subdirectories at depths 1, 2, and 3
        teklif_folders = find_teklif_folders(root_folder, 1, 3)

    # Scan PDFs in each teklif folder
    for teklif_folder in teklif_folders:
        pdfs_in_folder = walk_pdf_files(teklif_folder)
        pdf_files.extend(pdfs_in_folder)
        logging.info("  → %s PDF bulundu: %s", len(pdfs_in_folder), teklif_folder)

    logging.info("Toplam %s teklif klasöründe %s PDF bulundu.", len(teklif_folders), len(pdf_files))
    return pdf_files


def process_files(
    paths: list[str],
    progress_callback: Callable[[float], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> tuple[list[OfferRecord], list[str]]:
    """Parse PDF files and return list of offers (does NOT save to DB)"""
    records: list[OfferRecord] = []
    errors: list[str] = []
    total = len(paths)

    for index, path in enumerate(paths, start=1):
        if status_callback:
            status_callback(f"{index}/{total} • {os.path.basename(path)} işleniyor...")

        try:
            record = parse_offer(path)
            if record is not None:
                records.append(record)
        except Exception as exc:  # noqa: BLE001
            logging.exception("Dosya işlenemedi: %s", path)
            error_text = sanitize_text(str(exc))
            errors.append(f"{os.path.basename(path)}: {error_text}")

        if progress_callback:
            progress_callback(index / total if total else 1.0)

    return records, errors


def save_offers_batch(records: list[OfferRecord]) -> int:
    """Save multiple offers to database at once"""
    saved = 0
    with sqlite3.connect(DB_PATH) as conn:
        for record in records:
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO teklifler (file_path, firm, subject, amount, currency, extracted_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.file_path,
                        record.firm,
                        record.subject,
                        record.amount,
                        record.currency,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                saved += 1
            except Exception as exc:  # noqa: BLE001
                logging.error("Kayıt başarısız: %s - %s", record.file_path, exc)
    return saved


def read_log_tail(max_lines: int = 200) -> str:
    if not os.path.exists(LOG_PATH):
        return "Log dosyası henüz oluşmadı."
    with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as log_file:
        lines = log_file.readlines()
    return "".join(lines[-max_lines:]) or "Log dosyası boş."


def render_home_page() -> None:
    """Ana sayfa: PDF tarama ve önizleme"""
    st.header("📄 Teklif PDF Tarama")

    # Initialize session state for parsed offers
    if "parsed_offers" not in st.session_state:
        st.session_state.parsed_offers = []
    if "selected_indices" not in st.session_state:
        st.session_state.selected_indices = []

    # Database management section
    with st.expander("🗑️ Veritabanı Yönetimi"):
        offers_count = len(load_offers())
        st.write(f"**Veritabanında {offers_count} teklif var**")

        if offers_count > 0:
            st.warning("⚠️ Veritabanını sıfırlamak tüm kayıtlı teklifleri silecektir!")
            confirm_reset = st.checkbox("Veritabanını sıfırlamayı onaylıyorum", key="confirm_reset_home")
            if st.button("🗑️ Veritabanını Sıfırla", disabled=not confirm_reset, type="secondary"):
                reset_db()
                st.success("✅ Veritabanı temizlendi!")
                st.rerun()
        else:
            st.info("Veritabanı zaten boş.")

    st.divider()

    # Folder scanning section
    st.subheader("Klasör Tara")

    # Callback function for folder picker
    def on_browse_click():
        selected = pick_folder()
        if selected:
            st.session_state.scan_folder_path = selected

    if "scan_folder_path" not in st.session_state:
        st.session_state.scan_folder_path = ""

    col1, col2 = st.columns([4, 1])
    with col1:
        st.text_input(
            "Firma klasörlerinin bulunduğu ana klasör yolu",
            key="scan_folder_path",
            placeholder="E:/DELTA",
        )
    with col2:
        st.button("Gözat", on_click=on_browse_click)

    if st.button("📂 Klasörü Tara", type="primary", use_container_width=True):
        folder = st.session_state.scan_folder_path
        if not folder:
            st.warning("Lütfen klasör yolu girin.")
            return

        logging.info("Klasör taraması başlatıldı: %s", folder)
        pdf_files = scan_company_offer_pdfs(folder)

        if not pdf_files:
            st.warning("Teklif klasörlerinde PDF bulunamadı.")
            return

        # Filter out PDFs that are already in database
        existing_paths = get_existing_file_paths()
        new_pdf_files = [path for path in pdf_files if path not in existing_paths]
        already_processed_count = len(pdf_files) - len(new_pdf_files)

        # Show info about new vs existing PDFs
        if already_processed_count > 0:
            st.info(
                f"📊 Toplam {len(pdf_files)} PDF bulundu. "
                f"{already_processed_count} tanesi zaten veritabanında. "
                f"Sadece {len(new_pdf_files)} yeni PDF işlenecek."
            )

        if not new_pdf_files:
            st.success("✅ Tüm PDF'ler zaten veritabanında. Yeni dosya yok.")
            return

        progress_bar = st.progress(0)
        status_area = st.empty()

        records, errors = process_files(
            new_pdf_files,
            progress_callback=progress_bar.progress,
            status_callback=status_area.info,
        )

        status_area.success(f"✅ Tarama tamamlandı! {len(records)} yeni teklif bulundu.")
        st.session_state.parsed_offers = records
        st.session_state.selected_indices = list(range(len(records)))  # Select all by default

        if errors:
            with st.expander("⚠️ Hatalar"):
                st.error("\n\n".join(errors))

    # Display parsed offers in table with checkboxes
    if st.session_state.parsed_offers:
        st.divider()
        st.subheader(f"📋 Bulunan Teklifler ({len(st.session_state.parsed_offers)})")

        # Select all checkbox
        select_all = st.checkbox(
            "🔘 Tümünü Seç / Seçimi Kaldır",
            value=len(st.session_state.selected_indices) == len(st.session_state.parsed_offers),
            key="select_all",
        )

        if select_all:
            st.session_state.selected_indices = list(range(len(st.session_state.parsed_offers)))
        else:
            if len(st.session_state.selected_indices) == len(st.session_state.parsed_offers):
                st.session_state.selected_indices = []

        # Display offers in table format
        for idx, offer in enumerate(st.session_state.parsed_offers):
            col_check, col_firm, col_subject, col_amount = st.columns([1, 3, 4, 2])

            with col_check:
                is_selected = st.checkbox(
                    "Seç",
                    value=idx in st.session_state.selected_indices,
                    key=f"check_{idx}",
                    label_visibility="collapsed",
                )
                if is_selected and idx not in st.session_state.selected_indices:
                    st.session_state.selected_indices.append(idx)
                elif not is_selected and idx in st.session_state.selected_indices:
                    st.session_state.selected_indices.remove(idx)

            with col_firm:
                st.write(f"**{offer.firm or '(Firma bulunamadı)'}**")

            with col_subject:
                st.write(offer.subject or "(Konu bulunamadı)")

            with col_amount:
                if offer.amount:
                    st.write(f"**{offer.amount:,.2f} {offer.currency or ''}**")
                else:
                    st.write("(Tutar bulunamadı)")

            # File path in small text
            st.caption(f"📁 {os.path.basename(offer.file_path)}")
            st.divider()

        # Save button
        st.write(f"**Seçili: {len(st.session_state.selected_indices)} / {len(st.session_state.parsed_offers)}**")

        if st.button(
            f"💾 Seçili {len(st.session_state.selected_indices)} Teklifi DB'ye Ekle",
            type="primary",
            use_container_width=True,
            disabled=len(st.session_state.selected_indices) == 0,
        ):
            selected_offers = [
                st.session_state.parsed_offers[i] for i in st.session_state.selected_indices
            ]
            saved = save_offers_batch(selected_offers)
            st.success(f"✅ {saved} teklif veritabanına eklendi!")
            st.session_state.parsed_offers = []
            st.session_state.selected_indices = []
            st.rerun()


def render_tekliflerim_page() -> None:
    """Tekliflerim sayfası: DB'deki tüm teklifler + Excel export"""
    st.header("📋 Tekliflerim")

    offers = load_offers()

    if not offers:
        st.info("Henüz veritabanında teklif yok. Ana sayfadan PDF tarayın ve ekleyin.")
        return

    st.write(f"**Toplam {len(offers)} teklif**")

    # Excel export button
    df = get_offers_dataframe()
    if not df.empty:
        # Convert to Excel in memory
        from io import BytesIO

        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Teklifler")

        st.download_button(
            label="📥 Excel Olarak İndir",
            data=buffer.getvalue(),
            file_name="teklifler.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    st.divider()

    # Display offers as dataframe
    table_data = {
        "Firma": [o.firm for o in offers],
        "Konu": [o.subject for o in offers],
        "Tutar": [f"{o.amount:,.2f}" if o.amount is not None else "" for o in offers],
        "Para Birimi": [o.currency or "" for o in offers],
        "Dosya": [os.path.basename(o.file_path) for o in offers],
    }

    st.dataframe(table_data, use_container_width=True, hide_index=False)

    # Standardization section
    st.divider()
    st.subheader("🔄 Kayıtları Standardize Et")
    st.info(
        "Bu işlem mevcut kayıtlardaki firma adlarını (Title Case) ve para birimlerini (BÜYÜK HARF) "
        "standart formata çevirir. Örn: 'PAKSAN' → 'Paksan', 'Eur' → 'EUR'"
    )
    if st.button("Mevcut Kayıtları Standardize Et", type="primary"):
        with st.spinner("Kayıtlar standardize ediliyor..."):
            updated_count = standardize_existing_records()
        if updated_count > 0:
            st.success(f"✅ {updated_count} kayıt standardize edildi!")
            st.rerun()
        else:
            st.info("Tüm kayıtlar zaten standart formatta.")

    # Reset database section
    st.divider()
    st.subheader("🗑️ Veritabanını Temizle")
    st.warning("Bu işlem tüm teklifleri silecektir ve geri alınamaz!")
    confirm = st.checkbox("Veritabanını silmeyi onaylıyorum")
    if st.button("Tüm Teklifleri Sil", disabled=not confirm, type="secondary"):
        reset_db()
        st.success("Veritabanı temizlendi.")
        st.rerun()


def render_dashboard_page() -> None:
    """Dashboard sayfası: İstatistikler ve grafikler"""
    st.header("📊 Dashboard")

    stats = get_dashboard_stats()

    # Top metrics
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Toplam Teklif", stats["total_offers"])
    with col2:
        st.metric("Toplam Firma", stats["total_firms"])

    st.divider()

    # Amounts by currency
    if stats["amounts_by_currency"]:
        st.subheader("💰 Para Birimine Göre Toplam Tutarlar")
        currency_data = {
            "Para Birimi": [row[0] or "Belirtilmemiş" for row in stats["amounts_by_currency"]],
            "Toplam Tutar": [f"{row[1]:,.2f}" for row in stats["amounts_by_currency"]],
            "Adet": [row[2] for row in stats["amounts_by_currency"]],
        }
        st.dataframe(currency_data, use_container_width=True, hide_index=True)

    st.divider()

    # Top firms
    if stats["top_firms"]:
        st.subheader("🏢 En Yüksek Toplam Tutarlı Firmalar (Top 10)")
        firms_data = {
            "Firma": [row[0] for row in stats["top_firms"]],
            "Toplam Tutar": [f"{row[1]:,.2f}" for row in stats["top_firms"]],
            "Teklif Sayısı": [row[2] for row in stats["top_firms"]],
        }
        st.dataframe(firms_data, use_container_width=True, hide_index=True)


# Old UI functions removed - replaced with sidebar navigation


def ensure_temp_dir() -> str:
    temp_dir = os.path.join(os.getcwd(), ".streamlit_tmp")
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def pick_folder() -> str | None:
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        folder = filedialog.askdirectory()
    finally:
        root.destroy()
    return folder or None


def main() -> None:
    st.set_page_config(page_title="Teklif Listeleme", page_icon="📄", layout="wide")

    init_db()
    if "temp_dir" not in st.session_state:
        st.session_state.temp_dir = ensure_temp_dir()

    # Sidebar navigation
    st.sidebar.title("📄 Teklif Listeleme")
    st.sidebar.markdown("---")

    page = st.sidebar.radio(
        "Navigasyon",
        ["🏠 Ana Sayfa", "📋 Tekliflerim", "📊 Dashboard"],
        label_visibility="collapsed",
    )

    st.sidebar.markdown("---")
    st.sidebar.caption("PDF tekliflerini tarayın, yönetin ve analiz edin.")

    # Display backend log in sidebar
    with st.sidebar.expander("📜 Backend Logu"):
        st.code(read_log_tail(max_lines=100), language="text")

    # Route to appropriate page
    if page == "🏠 Ana Sayfa":
        render_home_page()
    elif page == "📋 Tekliflerim":
        render_tekliflerim_page()
    elif page == "📊 Dashboard":
        render_dashboard_page()


if __name__ == "__main__":
    main()
