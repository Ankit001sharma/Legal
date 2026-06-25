"""Extract plain text from Dev UI file uploads."""

from __future__ import annotations

import io
import re


def infer_categories_from_filename(filename: str) -> list[str]:
    name = filename.lower()
    mapping = [
        ("liabil", "liability"),
        ("indemn", "indemnity"),
        ("confiden", "confidentiality"),
        ("termin", "termination"),
        ("privacy", "privacy"),
        ("govern", "governing_law"),
        ("intellect", "ip"),
        (" ip", "ip"),
    ]
    cats: list[str] = []
    for needle, cat in mapping:
        if needle in name and cat not in cats:
            cats.append(cat)
    return cats or ["general"]


def infer_contract_type_from_filename(filename: str) -> str:
    name = filename.lower()
    if "msa" in name or "master" in name:
        return "msa"
    if "nda" in name or "disclosure" in name:
        return "nda"
    return "nda"


def title_from_filename(filename: str) -> str:
    stem = re.sub(r"\.[^.]+$", "", filename)
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    return stem.title() if stem else "Untitled document"


def read_upload_text(filename: str, data: bytes) -> str:
    lower = (filename or "").lower()
    if lower.endswith(".txt"):
        return data.decode("utf-8", errors="replace")
    if lower.endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF upload requires pypdf: pip install pypdf") from exc
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if lower.endswith(".docx"):
        try:
            from docx import Document
        except ImportError as exc:
            raise ValueError("DOCX upload requires python-docx: pip install python-docx") from exc
        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs if p.text).strip()
    raise ValueError(f"Unsupported file type: {filename} (use .txt, .pdf, or .docx)")
