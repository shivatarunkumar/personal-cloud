"""What kind of file this is (picks the icon and the viewer), and its real content type.

Browsers often send application/octet-stream or nothing for perfectly ordinary files
(.heic, .mkv, .md), so the extension fills the gaps.
"""

from __future__ import annotations

import mimetypes

GENERIC = {"", "application/octet-stream", "binary/octet-stream"}

EXTRA_TYPES = {
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".webm": "video/webm",
    ".md": "text/markdown",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".7z": "application/x-7z-compressed",
}

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".tsv",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".log",
    ".ini",
    ".toml",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".sh",
    ".sql",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".h",
    ".cpp",
}
ARCHIVE_TYPES = {
    "application/zip",
    "application/x-7z-compressed",
    "application/x-tar",
    "application/gzip",
    "application/x-rar-compressed",
    "application/vnd.rar",
    "application/x-bzip2",
}
DOCUMENT_HINTS = ("msword", "officedocument", "opendocument", "ms-excel", "ms-powerpoint", "rtf", "iwork")

# Served inline these could run script on the app's own origin, so they only ever download.
NEVER_INLINE = {"text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml"}


def _extension(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def content_type_for(name: str, sent: str | None) -> str:
    sent = (sent or "").split(";")[0].strip().lower()
    if sent not in GENERIC:
        return sent
    ext = _extension(name)
    return EXTRA_TYPES.get(ext) or mimetypes.guess_type(f"x{ext}")[0] or "application/octet-stream"


def kind_for(name: str, content_type: str) -> str:
    ext = _extension(name)
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("video/"):
        return "video"
    if content_type.startswith("audio/"):
        return "audio"
    if content_type == "application/pdf":
        return "pdf"
    if content_type in ARCHIVE_TYPES or ext in {".zip", ".7z", ".tar", ".gz", ".tgz", ".rar", ".bz2"}:
        return "archive"
    if any(hint in content_type for hint in DOCUMENT_HINTS) or ext in {
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".odt",
        ".ods",
        ".odp",
        ".pages",
        ".numbers",
        ".key",
    }:
        return "document"
    if content_type.startswith("text/") or ext in TEXT_EXTENSIONS or content_type == "application/json":
        return "text"
    return "other"


def safe_inline(content_type: str) -> bool:
    return content_type not in NEVER_INLINE
