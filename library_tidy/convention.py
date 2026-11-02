"""The naming convention itself: what a compliant name looks like, and how to
build one from a file's date, party, type and topic."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

# DATE forms we accept at the front of a name.
DATE_FORMS = (
    r"\d{4}-\d{2}-\d{2}(?: \d{2}\.\d{2}\.\d{2})?"  # 2024-10-01 or 2024-10-01 08.24.38
    r"|\d{4}-\d{2}"                                 # 2024-10
    r"|\d{4}-(?:Spring|Summer|Fall|Winter)(?:-\d{4})?"  # 2021-Fall
    r"|Undated"
    r"|\d{4}"                                       # 2024
)

SEPARATOR = " - "
CANONICAL = re.compile(rf"^(?:{DATE_FORMS}){re.escape(SEPARATOR)}")

# Characters that break Windows/SMB shares, HTTP URLs or shell quoting.
UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f#&]')
RESERVED_WINDOWS = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def stamp_for(dt: Optional[datetime], with_time: bool | None = None) -> str:
    """Render a datetime as a sortable date prefix. `None` means Undated."""
    if dt is None:
        return "Undated"
    if with_time is None:
        with_time = bool(dt.hour or dt.minute or dt.second)
    return f"{dt:%Y-%m-%d %H.%M.%S}" if with_time else f"{dt:%Y-%m-%d}"


def slug(text: str, limit: int = 60) -> str:
    """Make a filename-safe topic out of arbitrary text."""
    text = UNSAFE.sub(" ", text)
    text = re.sub(r"[_\u2013\u2014]+", " ", text)
    text = re.sub(r"[-_ ](?:19|20)\d{6}(?:[-_]\d{1,6})?(?=\s|$)", "", text)  # bot date+seq suffix
    text = re.sub(r"\(\d+\)\s*$", "", text)      # trailing copy counter
    text = re.sub(r"\s+", " ", text).strip(" .-_")
    text = text[:limit].strip(" .-_")
    if text.upper() in RESERVED_WINDOWS:
        text = f"{text}_file"
    return text or "Untitled"


def build_name(date: Optional[datetime], party: str, kind: str,
               topic: str = "", ext: str = "", with_time: bool | None = None) -> str:
    """Compose `DATE - PARTY - KIND - TOPIC.ext`, dropping empty segments."""
    parts = [stamp_for(date, with_time), slug(party, 30), slug(kind, 30)]
    if topic:
        parts.append(slug(topic))
    name = SEPARATOR.join(p for p in parts if p)
    return f"{name}{ext.lower()}" if ext else name


def parse_stamp(name: str) -> Optional[datetime]:
    """Read the date back out of a compliant name (best effort, no time)."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?: \d{2}\.\d{2}\.\d{2})?", name)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{4})-(\d{2}) - ", name)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            return None
    return None


def is_compliant(name: str) -> bool:
    return bool(CANONICAL.match(name))
