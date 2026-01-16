import logging
import os
import re
import sqlite3
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

import streamlit as st
from PyPDF2 import PdfReader
from tkinter import filedialog

DB_PATH = "teklifler.db"
LOG_PATH = "teklif_listeleme.log"
OFFER_FOLDER_PATTERN = re.compile(r"teklif", re.IGNORECASE)

FIRM_PATTERNS = [
    re.compile(r"(?:Firma|Şirket|Müşteri|Kurum|Kuruluş)\s*[:\-]?\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:A\.Ş\.|A\.S\.|Ltd\.?\s*Şti\.?|San\.|Tic\.)", re.IGNORECASE),
]

GREETINGS_PATTERN = re.compile(r"Sayın\s+(.+)", re.IGNORECASE)

SUBJECT_PATTERNS = [
    re.compile(r"Konu\s*[:\-]?\s*(.+)", re.IGNORECASE),
    re.compile(r"Teklif\s*Konusu\s*[:\-]?\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:Re|RE|Ref)\s*[:\-]?\s*(.+)", re.IGNORECASE),
    re.compile(r"İlgi\s*[:\-]?\s*(.+)", re.IGNORECASE),
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
    # Accept if we found subject OR amount (more lenient)
    if subject or amount is not None:
        return True
    # Otherwise check for "teklif" keyword
    full_text = "\n".join(pages_text)
    if OFFER_KEYWORD_PATTERN.search(full_text):
        return True
    # Also accept if PDF has reasonable amount of text (not empty)
    if len(full_text.strip()) > 100:
        return True
    return False


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
    pdf_files: list[str] = []
    for offers_folder in iter_offer_folders(root_folder):
        pdf_files.extend(walk_pdf_files(offers_folder))
    logging.info("Tarama tamamlandı: %s içinde %s PDF bulundu.", root_folder, len(pdf_files))
    return pdf_files


def process_files(
    paths: list[str],
    debug_mode: bool = False,
    progress_callback: Callable[[float], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> tuple[int, int, list[str], list[dict]]:
    processed = 0
    skipped = 0
    errors: list[str] = []
    debug_info: list[dict] = []
    total = len(paths)

    for index, path in enumerate(paths, start=1):
        if status_callback:
            status_callback(f"{index}/{total} • {os.path.basename(path)} işleniyor...")

        try:
            pages_text = extract_pages_from_pdf(path)
            full_text = "\n".join(pages_text)

            firm = extract_firm(pages_text)
            subject = extract_subject(pages_text)
            amount, currency = extract_amount_from_pages(pages_text)

            if debug_mode:
                debug_info.append({
                    "path": path,
                    "text_preview": full_text[:500] if full_text else "(Boş)",
                    "firm": firm or "(Bulunamadı)",
                    "subject": subject or "(Bulunamadı)",
                    "amount": f"{amount} {currency or ''}" if amount else "(Bulunamadı)",
                })

            if not looks_like_offer(pages_text, subject, amount):
                skipped += 1
                continue

            record = OfferRecord(
                file_path=path,
                firm=firm,
                subject=subject,
                amount=amount,
                currency=currency,
            )
            save_offer(record)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            logging.exception("Dosya işlenemedi: %s", path)
            error_text = sanitize_text(str(exc))
            errors.append(f"{path} okunamadı: {error_text}")

        if progress_callback:
            progress_callback(index / total if total else 1.0)

    return processed, skipped, errors, debug_info


def read_log_tail(max_lines: int = 200) -> str:
    if not os.path.exists(LOG_PATH):
        return "Log dosyası henüz oluşmadı."
    with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as log_file:
        lines = log_file.readlines()
    return "".join(lines[-max_lines:]) or "Log dosyası boş."


def render_backend_log() -> None:
    with st.expander("Backend Logu"):
        st.code(read_log_tail(), language="text")


def render_upload_panel() -> None:
    st.subheader("PDF Dosyası Ekle")
    uploaded_files = st.file_uploader(
        "Teklif PDF'lerini seçin",
        type=["pdf"],
        accept_multiple_files=True,
    )
    debug_mode = st.checkbox("Debug Modu (PDF'den çıkarılan metni göster)", key="upload_debug")
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
        progress_bar = st.progress(0)
        status_area = st.empty()
        processed, skipped, errors, debug_info = process_files(
            paths,
            debug_mode=debug_mode,
            progress_callback=progress_bar.progress,
            status_callback=status_area.info,
        )
        status_area.success("Tarama tamamlandı.")
        st.success(f"{processed} teklif işlendi, {skipped} dosya teklif olarak algılanmadı.")
        if errors:
            st.warning("\n".join(errors))
        if debug_mode and debug_info:
            st.subheader("Debug Bilgileri")
            for info in debug_info:
                with st.expander(f"📄 {os.path.basename(info['path'])}"):
                    st.write(f"**Firma:** {info['firm']}")
                    st.write(f"**Konu:** {info['subject']}")
                    st.write(f"**Tutar:** {info['amount']}")
                    st.text_area("Metin Önizleme", info['text_preview'], height=200)
        render_backend_log()


def render_folder_panel() -> None:
    st.subheader("Klasör Tara")
    st.caption(
        "Seçilen klasörün içindeki firma klasörlerinde adı 'teklif' geçen alt klasörler taranır."
    )

    def choose_scan_folder() -> None:
        selected = pick_folder()
        if selected:
            st.session_state.scan_folder_path = selected

    if "scan_folder_path" not in st.session_state:
        st.session_state.scan_folder_path = ""
    input_col, browse_col = st.columns([4, 1])
    with input_col:
        st.text_input(
            "Firma klasörlerinin bulunduğu ana klasör yolu",
            key="scan_folder_path",
        )
    with browse_col:
        st.button("Gözat", on_click=choose_scan_folder)
    folder = st.session_state.scan_folder_path
    debug_mode = st.checkbox("Debug Modu (PDF'den çıkarılan metni göster)", key="folder_debug")
    if st.button("Klasörü Tara"):
        if not folder:
            st.info("Lütfen bir klasör yolu girin.")
            return
        logging.info("Klasör taraması başlatıldı: %s", folder)
        pdf_files = scan_company_offer_pdfs(folder)
        if not pdf_files:
            st.warning("Teklif klasörlerinde PDF bulunamadı.")
            render_backend_log()
            return
        progress_bar = st.progress(0)
        status_area = st.empty()
        processed, skipped, errors, debug_info = process_files(
            pdf_files,
            debug_mode=debug_mode,
            progress_callback=progress_bar.progress,
            status_callback=status_area.info,
        )
        status_area.success("Tarama tamamlandı.")
        st.success(f"{processed} teklif işlendi, {skipped} dosya teklif olarak algılanmadı.")
        if errors:
            st.warning("\n".join(errors))
        if debug_mode and debug_info:
            st.subheader("Debug Bilgileri")
            for info in debug_info[:10]:  # Show first 10 for performance
                with st.expander(f"📄 {os.path.basename(info['path'])}"):
                    st.write(f"**Firma:** {info['firm']}")
                    st.write(f"**Konu:** {info['subject']}")
                    st.write(f"**Tutar:** {info['amount']}")
                    st.text_area("Metin Önizleme", info['text_preview'], height=200, key=f"debug_{info['path']}")
            if len(debug_info) > 10:
                st.info(f"İlk 10 dosya gösteriliyor. Toplam {len(debug_info)} dosya işlendi.")
        render_backend_log()


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
