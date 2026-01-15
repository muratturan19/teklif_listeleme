import os
import re
import sqlite3
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from PyPDF2 import PdfReader

DB_PATH = "teklifler.db"

FIRM_PATTERNS = [
    re.compile(r"(?:Firma|Şirket|Müşteri)\s*[:\-]\s*(.+)", re.IGNORECASE),
    re.compile(r"Sayın\s+(.+)", re.IGNORECASE),
]

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


def extract_text_from_pdf(path: str) -> str:
    reader = PdfReader(path)
    chunks = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            chunks.append(text)
    return "\n".join(chunks)


def extract_field(patterns: list[re.Pattern], text: str) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            value = match.group(1).strip()
            value = re.split(r"\n|\r", value)[0].strip()
            return value
    return ""


def extract_amount(text: str) -> tuple[float | None, str | None]:
    for pattern in AMOUNT_PATTERNS:
        match = pattern.search(text)
        if match:
            raw_amount = match.group(1).strip()
            currency = match.group(2) if match.lastindex and match.lastindex >= 2 else None
            normalized = raw_amount.replace(".", "").replace(",", ".")
            try:
                return float(normalized), currency
            except ValueError:
                continue
    return None, None


def parse_offer(path: str) -> OfferRecord:
    text = extract_text_from_pdf(path)
    firm = extract_field(FIRM_PATTERNS, text)
    subject = extract_field(SUBJECT_PATTERNS, text)
    amount, currency = extract_amount(text)
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


def walk_pdf_files(folder: str, max_depth: int = 2) -> list[str]:
    pdf_files = []
    base_depth = folder.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(folder):
        current_depth = root.count(os.sep) - base_depth
        if current_depth >= max_depth:
            dirs[:] = []
        for file in files:
            if file.lower().endswith(".pdf"):
                pdf_files.append(os.path.join(root, file))
    return pdf_files


class OfferApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Teklif Listeleme")
        self.geometry("900x600")

        self._build_ui()
        init_db()
        self.refresh_table()

    def _build_ui(self) -> None:
        top_frame = ttk.Frame(self)
        top_frame.pack(fill="x", padx=12, pady=8)

        ttk.Button(top_frame, text="PDF Dosyası Ekle", command=self.add_files).pack(
            side="left", padx=6
        )
        ttk.Button(top_frame, text="Klasör Tara", command=self.scan_folder).pack(
            side="left", padx=6
        )
        ttk.Button(top_frame, text="Özet Tablo", command=self.show_summary).pack(
            side="left", padx=6
        )
        ttk.Button(top_frame, text="Listeyi Yenile", command=self.refresh_table).pack(
            side="left", padx=6
        )

        self.tree = ttk.Treeview(
            self,
            columns=("firm", "subject", "amount", "currency", "file"),
            show="headings",
        )
        self.tree.heading("firm", text="Firma")
        self.tree.heading("subject", text="Konu")
        self.tree.heading("amount", text="Tutar")
        self.tree.heading("currency", text="Para Birimi")
        self.tree.heading("file", text="Dosya")
        self.tree.column("firm", width=160)
        self.tree.column("subject", width=220)
        self.tree.column("amount", width=90, anchor="e")
        self.tree.column("currency", width=90, anchor="center")
        self.tree.column("file", width=300)
        self.tree.pack(fill="both", expand=True, padx=12, pady=8)

    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Teklif PDF'lerini Seçin", filetypes=[("PDF Files", "*.pdf")]
        )
        if not paths:
            return
        self.process_files(paths)

    def scan_folder(self) -> None:
        folder = filedialog.askdirectory(title="Tekliflerin Olduğu Klasörü Seçin")
        if not folder:
            return
        pdf_files = walk_pdf_files(folder, max_depth=2)
        if not pdf_files:
            messagebox.showinfo("Bilgi", "Belirtilen klasörde PDF bulunamadı.")
            return
        self.process_files(pdf_files)

    def process_files(self, paths: list[str]) -> None:
        count = 0
        for path in paths:
            try:
                record = parse_offer(path)
                save_offer(record)
                count += 1
            except Exception as exc:  # noqa: BLE001
                messagebox.showwarning("Uyarı", f"{path} okunamadı: {exc}")
        self.refresh_table()
        messagebox.showinfo("Tamamlandı", f"{count} teklif işlendi.")

    def refresh_table(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for record in load_offers():
            amount_text = "" if record.amount is None else f"{record.amount:,.2f}"
            self.tree.insert(
                "",
                "end",
                values=(record.firm, record.subject, amount_text, record.currency or "", record.file_path),
            )

    def show_summary(self) -> None:
        summary_window = tk.Toplevel(self)
        summary_window.title("Özet Tablo")
        summary_window.geometry("600x400")

        tree = ttk.Treeview(
            summary_window,
            columns=("firm", "subject", "total"),
            show="headings",
        )
        tree.heading("firm", text="Firma")
        tree.heading("subject", text="Konu")
        tree.heading("total", text="Toplam Tutar")
        tree.column("firm", width=160)
        tree.column("subject", width=240)
        tree.column("total", width=120, anchor="e")
        tree.pack(fill="both", expand=True, padx=12, pady=8)

        for firm, subject, total in load_summary():
            tree.insert("", "end", values=(firm, subject, f"{total:,.2f}"))


if __name__ == "__main__":
    app = OfferApp()
    app.mainloop()
