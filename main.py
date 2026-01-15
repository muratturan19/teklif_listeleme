import logging
import os
import queue
import re
import sqlite3
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from PyPDF2 import PdfReader

DB_PATH = "teklifler.db"
LOG_PATH = "teklif_listeleme.log"

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


def parse_offer(path: str) -> OfferRecord:
    pages_text = extract_pages_from_pdf(path)
    firm = extract_firm(pages_text)
    subject = extract_subject(pages_text)
    amount, currency = extract_amount_from_pages(pages_text)
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
        self.geometry("1040x680")
        self.minsize(960, 640)
        self.configure(bg="#f5f6f8")

        self._build_ui()
        init_db()
        self.refresh_table()

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#f5f6f8")
        style.configure("Header.TLabel", font=("Segoe UI", 18, "bold"), background="#f5f6f8")
        style.configure("SubHeader.TLabel", font=("Segoe UI", 10), foreground="#5f6b7a", background="#f5f6f8")
        style.configure("Toolbar.TFrame", background="#f5f6f8")
        style.configure("TButton", font=("Segoe UI", 10), padding=6)
        style.configure(
            "Treeview",
            font=("Segoe UI", 10),
            rowheight=28,
        )
        style.configure(
            "Treeview.Heading",
            font=("Segoe UI", 10, "bold"),
            background="#e3e7ed",
        )
        style.map("Treeview.Heading", background=[("active", "#d9dee6")])

        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        header_frame = ttk.Frame(container)
        header_frame.pack(fill="x")
        ttk.Label(header_frame, text="Teklif Yönetimi", style="Header.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            header_frame,
            text="PDF teklifleri tarayın, hızlıca özetleyin ve raporlayın.",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(2, 12))

        top_frame = ttk.Frame(container, style="Toolbar.TFrame")
        top_frame.pack(fill="x", pady=(0, 12))

        self.buttons: list[ttk.Button] = []
        self.buttons.append(
            ttk.Button(top_frame, text="PDF Dosyası Ekle", command=self.add_files)
        )
        self.buttons[-1].pack(
            side="left", padx=6
        )
        self.buttons.append(
            ttk.Button(top_frame, text="Klasör Tara", command=self.scan_folder)
        )
        self.buttons[-1].pack(
            side="left", padx=6
        )
        self.buttons.append(
            ttk.Button(top_frame, text="Özet Tablo", command=self.show_summary)
        )
        self.buttons[-1].pack(
            side="left", padx=6
        )
        self.buttons.append(
            ttk.Button(top_frame, text="Listeyi Yenile", command=self.refresh_table)
        )
        self.buttons[-1].pack(
            side="left", padx=6
        )

        tree_frame = ttk.Frame(container)
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            tree_frame,
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
        self.tree.tag_configure("odd", background="#ffffff")
        self.tree.tag_configure("even", background="#f1f4f8")

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        status_frame = ttk.Frame(container)
        status_frame.pack(fill="x", pady=(12, 0))
        self.status_var = tk.StringVar(value="Hazır.")
        self.count_var = tk.StringVar(value="Toplam teklif: 0")
        ttk.Label(status_frame, textvariable=self.status_var, anchor="w").pack(
            side="left"
        )
        ttk.Label(status_frame, textvariable=self.count_var, anchor="e").pack(
            side="right"
        )

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
        if not paths:
            return
        self._start_processing(paths)

    def _set_buttons_state(self, state: str) -> None:
        for button in self.buttons:
            button.configure(state=state)

    def _start_processing(self, paths: list[str]) -> None:
        self._set_buttons_state("disabled")
        self.status_var.set("Dosyalar işleniyor...")
        progress_window = tk.Toplevel(self)
        progress_window.title("İşlem Devam Ediyor")
        progress_window.geometry("480x160")
        progress_window.resizable(False, False)
        progress_window.transient(self)
        progress_window.grab_set()

        label_var = tk.StringVar(value="Dosyalar hazırlanıyor...")
        ttk.Label(progress_window, textvariable=label_var, anchor="w").pack(
            fill="x", padx=12, pady=(12, 6)
        )
        progress = ttk.Progressbar(
            progress_window, maximum=len(paths), mode="determinate"
        )
        progress.pack(fill="x", padx=12, pady=6)

        queue_updates: queue.Queue[tuple] = queue.Queue()
        thread = threading.Thread(
            target=self._process_files_worker, args=(paths, queue_updates), daemon=True
        )
        thread.start()

        self._poll_queue(
            progress_window,
            label_var,
            progress,
            queue_updates,
        )

    def _process_files_worker(self, paths: list[str], queue_updates: queue.Queue) -> None:
        count = 0
        errors: list[str] = []
        total = len(paths)
        for index, path in enumerate(paths, start=1):
            queue_updates.put(("progress", index, total, path))
            try:
                record = parse_offer(path)
                save_offer(record)
                count += 1
            except Exception as exc:  # noqa: BLE001
                logging.exception("Dosya işlenemedi: %s", path)
                error_text = sanitize_text(str(exc))
                errors.append(f"{path} okunamadı: {error_text}")
        queue_updates.put(("done", count, errors))

    def _poll_queue(
        self,
        progress_window: tk.Toplevel,
        label_var: tk.StringVar,
        progress: ttk.Progressbar,
        queue_updates: queue.Queue,
    ) -> None:
        try:
            while True:
                message = queue_updates.get_nowait()
                if message[0] == "progress":
                    _, index, total, path = message
                    progress["value"] = index
                    label_var.set(f"{index}/{total} işlendi: {os.path.basename(path)}")
                elif message[0] == "done":
                    _, count, errors = message
                    progress_window.destroy()
                    self._set_buttons_state("normal")
                    self.refresh_table()
                    self.status_var.set(f"{count} teklif işlendi.")
                    if errors:
                        messagebox.showwarning(
                            "Uyarı",
                            "\n".join(errors),
                        )
                    messagebox.showinfo("Tamamlandı", f"{count} teklif işlendi.")
                    return
        except queue.Empty:
            pass
        self.after(
            120,
            lambda: self._poll_queue(
                progress_window, label_var, progress, queue_updates
            ),
        )

    def refresh_table(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, record in enumerate(load_offers()):
            amount_text = "" if record.amount is None else f"{record.amount:,.2f}"
            self.tree.insert(
                "",
                "end",
                values=(
                    record.firm,
                    record.subject,
                    amount_text,
                    record.currency or "",
                    record.file_path,
                ),
                tags=("even" if index % 2 == 0 else "odd",),
            )
        self.count_var.set(f"Toplam teklif: {len(self.tree.get_children())}")

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
