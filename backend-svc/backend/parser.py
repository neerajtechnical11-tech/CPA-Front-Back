"""Extract text from an uploaded document and split into chunks."""
from pathlib import Path

ALLOWED_EXTS = {".pdf", ".docx", ".txt", ".md"}


def extract_text(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise ValueError(
            f"Unsupported file type '{ext or '(none)'}'. Please upload a PDF, DOCX, TXT or MD file.")
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(path)
            return "\n".join((p.extract_text() or "") for p in reader.pages)
        if ext == ".docx":
            import docx
            return "\n".join(p.text for p in docx.Document(path).paragraphs)
        # .txt / .md
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except ValueError:
        raise
    except Exception as e:
        # Corrupt file, or content that does not match the extension (e.g. a renamed
        # binary). Surface a clear message instead of a raw parser traceback.
        raise ValueError(
            f"The file could not be read - it may be corrupted or not a valid "
            f"{ext.lstrip('.').upper()} file.") from e


def chunk(text: str, size: int = 120, overlap: int = 30) -> list[str]:
    """Simple sliding-window chunker over whitespace-normalized text."""
    words = text.split()
    chunks, i = [], 0
    step = max(1, size - overlap)
    while i < len(words):
        chunks.append(" ".join(words[i : i + size]))
        i += step
    return [c for c in chunks if c.strip()]
