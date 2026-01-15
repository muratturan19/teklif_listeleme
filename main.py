import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import streamlit as st
from PyPDF2 import PdfReader

DB_PATH = "teklifler.db"
LOG_PATH = "teklif_listeleme.log"
OFFERS_FOLDER_NAME = "Teklifler"

FIRM_PATTERNS = [
    re.compile(r"(?:Firma|Şirket|Müşteri)\s*[:\-]\s*(.+)", re.IGNORECASE),
]

GREETINGS_PATTERN = re.compile(r"Sayın\s+(.+)", re.IGNORECASE)

SUBJECT_PATTERNS = [
    re.compile(r"Konu\s*[:\-]\s*(.+)", re.IGNORECASE),
    re.compile(r"Teklif\s*Konusu\s*[:\-]\s*(.+)", re.IGNORECASE),
]

AMOUNT_PATTERNS = [
    re.compile(
        r"(?:Toplam\s*Tutar|Teklif\s*Tutarı|Tutar)\s*[:\-]?\s*([\d\.\,]+)\s*(TL|₺|USD|EUR)?",
        re.IGNORECASE,
    ),
    re.compile(r"([\d\.\,]+)\s*(TL|₺|USD|EUR)", re.IGNORECASE),
]

OFFER_KEYWORD_PATTERN = re.compile(r"\bteklif\b", re.IGNORECASE)

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
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
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()


def extract_text_from_pdf(path: str) -> str:
    reader = PdfReader(path)
    chunks: list[str] = []
    for page in reader.pages:
        text = sanitize_text(page.extract_text() or "")
        if text.strip():
            chunks.append(text)
    return "\n".join(chunks)


def extract_pages_from_pdf(path: str) -> list[str]:
    reader = PdfReader(path)
    chunks: list[str] = []
    for page in reader.pages:
        text = sanitize_text(page.extract_text() or "")
        chunks.append(text)
    return chunks


def sanitize_text(value: str) -> str:
    return value.encode("utf-8", "replace").decode("utf-8")


def extract_field(patterns: list[re.Pattern], text: str) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            value = match.group(1).strip()
            value = re.split(r"\n|\r", value)[0].strip()
            return value
    return ""


def extract_firm(pages_text: list[str]) -> str:
    if not pages_text:
        return ""
    first_page = pages_text[0]
    lines = [line.strip() for line in first_page.splitlines() if line.strip()]
    header_block = "\n".join(lines[:12])
    firm = extract_field(FIRM_PATTERNS, header_block)
    if firm:
        return firm
    for line in lines[:15]:
        match = GREETINGS_PATTERN.search(line)
        if not match:
            continue
        candidate = match.group(1).strip()
        if re.search(r"\b(hanım|bey)\b", candidate, re.IGNORECASE):
            continue
        return candidate
    return ""


def extract_subject(pages_text: list[str]) -> str:
    if not pages_text:
        return ""
    first_page = pages_text[0]
    lines = [line.strip() for line in first_page.splitlines() if line.strip()]
    header_block = "\n".join(lines[:18])
    return extract_field(SUBJECT_PATTERNS, header_block)


def parse_amount(raw_amount: str, currency: str | None) -> tuple[float | None, str | None]:
    normalized = raw_amount.replace(",", ".")
    if normalized.count(".") > 1:
        parts = normalized.split(".")
        normalized = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(normalized), currency
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


def looks_like_offer(pages_text: list[str], subject: str, amount: float | None) -> bool:
    if subject or amount is not None:
        return True
    full_text = "\n".join(pages_text)
    return bool(OFFER_KEYWORD_PATTERN.search(full_text))


def parse_offer(path: str) -> OfferRecord | None:
    pages_text = extract_pages_from_pdf(path)
    firm = extract_firm(pages_text)
    subject = extract_subject(pages_text)
    amount, currency = extract_amount_from_pages(pages_text)
    if not looks_like_offer(pages_text, subject, amount):
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


def walk_pdf_files(folder: str) -> list[str]:
    pdf_files: list[str] = []
    for root, _, files in os.walk(folder):
        for file in files:
            if file.lower().endswith(".pdf"):
                pdf_files.append(os.path.join(root, file))
    return pdf_files


def iter_offer_folders(root_folder: str) -> Iterable[str]:
    if not os.path.isdir(root_folder):
        return []
    for entry in os.listdir(root_folder):
        company_path = os.path.join(root_folder, entry)
        if not os.path.isdir(company_path):
            continue
        offers_folder = None
        for sub_entry in os.listdir(company_path):
            if sub_entry.lower() == OFFERS_FOLDER_NAME.lower():
                offers_folder = os.path.join(company_path, sub_entry)
                break
        if offers_folder and os.path.isdir(offers_folder):
            yield offers_folder


def scan_company_offer_pdfs(root_folder: str) -> list[str]:
    pdf_files: list[str] = []
    for offers_folder in iter_offer_folders(root_folder):
        pdf_files.extend(walk_pdf_files(offers_folder))
    return pdf_files


def process_files(paths: list[str]) -> tuple[int, int, list[str]]:
    processed = 0
    skipped = 0
    errors: list[str] = []
    for path in paths:
        try:
            record = parse_offer(path)
            if record is None:
                skipped += 1
                continue
            save_offer(record)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            logging.exception("Dosya işlenemedi: %s", path)
            error_text = sanitize_text(str(exc))
            errors.append(f"{path} okunamadı: {error_text}")
    return processed, skipped, errors


def render_upload_panel() -> None:
    st.subheader("PDF Dosyası Ekle")
    uploaded_files = st.file_uploader(
        "Teklif PDF'lerini seçin",
        type=["pdf"],
        accept_multiple_files=True,
    )
    if st.button("Seçilen PDF'leri Tara", type="primary"):
        if not uploaded_files:
            st.info("Lütfen en az bir PDF seçin.")
            return
        paths = []
        for file in uploaded_files:
            temp_path = os.path.join(st.session_state.temp_dir, file.name)
            with open(temp_path, "wb") as temp_file:
                temp_file.write(file.read())
            paths.append(temp_path)
        with st.spinner("PDF'ler işleniyor..."):
            processed, skipped, errors = process_files(paths)
        st.success(f"{processed} teklif işlendi, {skipped} dosya teklif olarak algılanmadı.")
        if errors:
            st.warning("\n".join(errors))


def render_folder_panel() -> None:
    st.subheader("Klasör Tara")
    st.caption(
        "Seçilen klasörün içindeki firma klasörlerinde sadece 'Teklifler' alt klasörü taranır."
    )
    folder = st.text_input("Firma klasörlerinin bulunduğu ana klasör yolu")
    if st.button("Klasörü Tara"):
        if not folder:
            st.info("Lütfen bir klasör yolu girin.")
            return
        pdf_files = scan_company_offer_pdfs(folder)
        if not pdf_files:
            st.warning("Teklifler klasörlerinde PDF bulunamadı.")
            return
        with st.spinner("PDF'ler işleniyor..."):
            processed, skipped, errors = process_files(pdf_files)
        st.success(f"{processed} teklif işlendi, {skipped} dosya teklif olarak algılanmadı.")
        if errors:
            st.warning("\n".join(errors))


def render_offers_table() -> None:
    st.subheader("Teklif Listesi")
    offers = load_offers()
    if not offers:
        st.info("Henüz kayıtlı teklif yok.")
        return
    table_data = [
        {
            "Firma": offer.firm,
            "Konu": offer.subject,
            "Tutar": "" if offer.amount is None else f"{offer.amount:,.2f}",
            "Para Birimi": offer.currency or "",
            "Dosya": offer.file_path,
        }
        for offer in offers
    ]
    st.dataframe(table_data, use_container_width=True, hide_index=True)


def render_summary_table() -> None:
    st.subheader("Özet Tablo")
    summary = load_summary()
    if not summary:
        st.info("Özet için teklif bulunamadı.")
        return
    summary_data = [
        {
            "Firma": firm,
            "Konu": subject,
            "Toplam Tutar": f"{total:,.2f}",
        }
        for firm, subject, total in summary
    ]
    st.dataframe(summary_data, use_container_width=True, hide_index=True)


def render_reset_section() -> None:
    st.subheader("Listeyi Sıfırla")
    st.warning(
        "Bu işlem mevcut SQLite veritabanını siler ve tüm kayıtları temizler.",
        icon="⚠️",
    )
    confirm = st.checkbox("Listeyi sıfırlamayı onaylıyorum")
    if st.button("Listeyi Sıfırla", disabled=not confirm):
        reset_db()
        st.success("Veritabanı sıfırlandı.")


def ensure_temp_dir() -> str:
    temp_dir = os.path.join(os.getcwd(), ".streamlit_tmp")
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def main() -> None:
    st.set_page_config(page_title="Teklif Listeleme", page_icon="📄", layout="wide")
    st.title("Teklif Listeleme")
    st.caption("PDF tekliflerini tarayın, listeleyin ve özetleyin.")

    init_db()
    if "temp_dir" not in st.session_state:
        st.session_state.temp_dir = ensure_temp_dir()

    tab_upload, tab_scan, tab_list, tab_summary, tab_reset = st.tabs(
        ["PDF Ekle", "Klasör Tara", "Teklif Listesi", "Özet", "Sıfırla"]
    )

    with tab_upload:
        render_upload_panel()
    with tab_scan:
        render_folder_panel()
    with tab_list:
        render_offers_table()
    with tab_summary:
        render_summary_table()
    with tab_reset:
        render_reset_section()


if __name__ == "__main__":
    main()
