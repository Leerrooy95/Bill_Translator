"""
document_processor.py — Document Ingestion + Text Extraction
==============================================================
Handles PDF, .txt, and .md file ingestion for the Bill Translator.
Extracts text content with secure filename handling and path validation.

Adapted from the Accountability Agent's document_processor.py.
"""

import hashlib
import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import pdfplumber

    _HAS_PDFPLUMBER = True
except ImportError:
    _HAS_PDFPLUMBER = False
    logger.warning("pdfplumber not installed. PDF extraction disabled.")

# Allowed file extensions
ALLOWED_EXTENSIONS = {"txt", "pdf", "md"}
MAX_TEXT_LENGTH = 500_000  # Characters — safety limit


def _validate_path(file_path: str, base_dir=None):
    """
    Validate and resolve a file path to prevent path traversal attacks.
    If base_dir is provided, ensures the resolved path is under that directory.
    Returns the resolved absolute path.
    """
    resolved = os.path.realpath(file_path)
    if not os.path.isfile(resolved):
        raise ValueError("File does not exist.")
    if base_dir is not None:
        base_resolved = os.path.realpath(base_dir)
        if not resolved.startswith(base_resolved + os.sep) and resolved != base_resolved:
            raise ValueError("Path traversal detected.")
    return resolved


def allowed_file(filename):
    """Check if the filename has an allowed extension."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def secure_filename_hash(filename):
    """
    Generate a secure filename using a random hash.
    Preserves the original extension but replaces the name with a random hex
    string. Only allows extensions from ALLOWED_EXTENSIONS.
    """
    ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        ext = "bin"
    random_name = secrets.token_hex(16)
    return f"{random_name}.{ext}"


def extract_text_from_pdf(file_path):
    """Extract text from a PDF file using pdfplumber."""
    if not _HAS_PDFPLUMBER:
        raise RuntimeError(
            "pdfplumber is not installed. Install with: pip install pdfplumber"
        )
    safe_path = _validate_path(file_path)
    text_parts = []
    with pdfplumber.open(safe_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    full_text = "\n\n".join(text_parts)
    return full_text[:MAX_TEXT_LENGTH]


def extract_text_from_file(file_path):
    """Extract text from a .txt or .md file."""
    safe_path = _validate_path(file_path)
    with open(safe_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read(MAX_TEXT_LENGTH)
    return text


def process_file(file_path, original_filename):
    """
    Process a file: determine type, extract text.

    Returns a dict with:
      - filename: original filename
      - file_type: pdf, txt, or md
      - raw_text: extracted text content
      - file_hash: SHA-256 hash of the file for deduplication
    """
    ext = original_filename.rsplit(".", 1)[1].lower() if "." in original_filename else ""

    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: .{ext}")

    # Compute file hash for deduplication
    safe_path = _validate_path(file_path)
    sha256 = hashlib.sha256()
    with open(safe_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    file_hash = sha256.hexdigest()

    # Extract text
    if ext == "pdf":
        raw_text = extract_text_from_pdf(file_path)
    else:
        raw_text = extract_text_from_file(file_path)

    return {
        "filename": original_filename,
        "file_type": ext,
        "raw_text": raw_text,
        "file_hash": file_hash,
    }


def save_uploaded_file(file_storage, upload_dir):
    """
    Save an uploaded file from Flask's request.files to the upload directory.
    Returns (secure_path, original_filename).
    """
    original_filename = file_storage.filename or "unnamed"
    secure_name = secure_filename_hash(original_filename)
    save_path = os.path.join(upload_dir, secure_name)
    Path(upload_dir).mkdir(parents=True, exist_ok=True)
    file_storage.save(save_path)
    return save_path, original_filename
