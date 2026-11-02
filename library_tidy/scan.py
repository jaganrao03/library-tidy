"""Reading the library: dates from names and metadata, integrity checks, and the
inventory that every command and the audit report are built on."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Date shapes seen in the wild: camera counters, download bots, editors, browsers.
NAME_PATTERNS = (
    (re.compile(r"(20\d{2})(\d{2})(\d{2})[_-]?(\d{2})(\d{2})(\d{2})"), 6),   # 20241001_083045
    (re.compile(r"(20\d{2})-(\d{2})-(\d{2})[T _](\d{2})[-.:](\d{2})[-.:](\d{2})"), 6),
    (re.compile(r"(20\d{2})(\d{2})(\d{2})[_-](\d{2})(\d{2})(?!\d)"), 5),     # 20241001-0830
    (re.compile(r"(20\d{2})(\d{2})(\d{2})"), 3),                             # 20241001
    (re.compile(r"(20\d{2})-(\d{2})-(\d{2})"), 3),
)

# Compound formats that are really ZIP (Office, ODF, MySQL Workbench, plugins).
ZIP_FAMILY = {".zip", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odg", ".odp",
              ".epub", ".mwb", ".jar", ".apk", ".whl", ".kra", ".xpi"}

MAGIC = (
    (b"\xff\xd8\xff", {".jpg", ".jpeg"}),
    (b"\x89PNG\r\n\x1a\n", {".png"}),
    (b"%PDF", {".pdf"}),
    (b"PK\x03\x04", ZIP_FAMILY),
    (b"Rar!", {".rar"}),
    (b"7z\xbc\xaf\x27\x1c", {".7z"}),
    (b"\x1f\x8b", {".gz", ".tgz", ".prproj"}),
    (b"GIF89a", {".gif"}),
    (b"GIF87a", {".gif"}),
    (b"fLaC", {".flac"}),
    (b"ID3", {".mp3"}),
    (b"\x1aE\xdf\xa3", {".mkv", ".webm"}),
    (b"RIFF", {".webp", ".avi", ".wav"}),
)

# Directories whose contents are addressed by software, not by humans.
DEFAULT_SKIP_DIRS = {"_Library Metadata", "_Duplicates", "Music and Audio",
                     "Programs", "Code", "src", "node_modules", ".git",
                     "Lightroom", "venv", ".venv", "__pycache__"}

# Extensions that are config, code or vendor artefacts — never date-prefixed.
def is_ignored(rel: str, skip) -> bool:
    """True if any path segment is a skip dir or dot-prefixed.

    Dot *directories* count, not just dot files: a duplicate inside .git or a
    thumbnail cache is not something to quarantine.
    """
    parts = rel.split("/")
    return bool(set(parts[:-1]) & set(skip)) or any(p.startswith(".") for p in parts)


DEFAULT_SKIP_EXT = {".py", ".ipynb", ".js", ".ts", ".sql", ".json", ".mwb",
                    ".lrcat", ".kdbx", ".bat", ".ps1", ".sst", ".db", ".ico",
                    ".dll", ".exe", ".lock", ".css", ".html", ".sln", ".csproj",
                    ".yml", ".yaml", ".cue", ".log", ".m3u", ".nfo", ".ffp",
                    ".md5", ".toml", ".cfg", ".conf", ".ini", ".qbtheme",
                    ".bak", ".bak2", ".bak3", ".bak4", ".dot", ".pl", ".r"}

MEDIA_VIDEO = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv"}
MEDIA_IMAGE = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}


@dataclass
class Entry:
    path: Path
    rel: str
    size: int
    mtime: float
    is_dir: bool = False


def walk(root: Path, include_dirs: bool = False) -> Iterator[Entry]:
    root = root.resolve()
    for p in root.rglob("*"):
        try:
            st = p.stat()
        except OSError:
            continue
        if p.is_dir():
            if include_dirs:
                yield Entry(p, p.relative_to(root).as_posix(), 0, st.st_mtime, True)
            continue
        yield Entry(p, p.relative_to(root).as_posix(), st.st_size, st.st_mtime)


def date_from_name(name: str) -> datetime | None:
    for rx, groups in NAME_PATTERNS:
        m = rx.search(name)
        if not m:
            continue
        g = [int(x) for x in m.groups()[:groups]]
        try:
            if groups == 6:
                y, mo, d, h, mi, s = g
                if not (1990 <= y <= 2100):
                    continue
                return datetime(y, mo, d, min(h, 23), min(mi, 59), min(s, 59))
            y, mo, d = g
            if not (1990 <= y <= 2100):
                continue
            return datetime(y, mo, d)
        except ValueError:
            continue
    return None


def date_from_exif(path: Path) -> datetime | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            for tag in (36867, 36868, 306):      # DateTimeOriginal, Digitized, DateTime
                raw = exif.get(tag)
                if not raw:
                    continue
                try:
                    return datetime.strptime(str(raw).split(".")[0].strip(), "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    continue
    except Exception:
        return None
    return None


def date_from_media(path: Path, timeout: int = 20) -> datetime | None:
    """creation_time from the container — the only honest source for a video
    whose filename carries no timestamp."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time",
             "-of", "default=nk=1:nw=1", str(path)],
            capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if not out:
        return None
    try:
        return datetime.fromisoformat(out.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


def best_date(path: Path, allow_media: bool = True) -> tuple[datetime | None, str]:
    """Filename first (it is what the owner already saw), then embedded
    metadata, then filesystem mtime. Returns (date, source)."""
    d = date_from_name(path.name)
    if d:
        return d, "filename"
    if allow_media:
        if path.suffix.lower() in MEDIA_VIDEO:
            d = date_from_media(path)
            if d:
                return d, "media-metadata"
        elif path.suffix.lower() in MEDIA_IMAGE:
            d = date_from_exif(path)
            if d:
                return d, "exif"
    return datetime.fromtimestamp(path.stat().st_mtime), "mtime"


def head_bytes(path: Path, n: int = 16) -> bytes:
    try:
        with path.open("rb") as f:
            return f.read(n)
    except OSError:
        return b""


def magic_matches(path: Path) -> bool | None:
    """True/False when we recognise the signature, None when we cannot judge."""
    head = head_bytes(path)
    if not head:
        return None
    ext = path.suffix.lower()
    for sig, exts in MAGIC:
        if head.startswith(sig):
            return ext in exts
    if head[4:8] == b"ftyp":
        return ext in {".mp4", ".mov", ".m4v", ".heic", ".avif"}
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return ext == ".webp"
    return None


# What the bytes actually are, as a canonical extension. Renaming to the true
# extension is honest and lossless; re-encoding is neither.
TRUE_EXT = {
    "jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif",
    "pdf": ".pdf", "zip": ".zip", "rar": ".rar", "7z": ".7z", "gzip": None,
    "heic": ".heic", "avif": ".avif", "mp4": ".mp4", "quicktime": ".mov",
    "matroska": ".mkv", "flac": ".flac", "mp3": ".mp3", "ole": None,
}
MEDIA_LIKE = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".avif",
              ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".m4a"}


def detect_type(path: Path) -> str | None:
    """Return the container type the first bytes prove, or None."""
    head = head_bytes(path, 32)
    if not head:
        return None
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"GIF8"):
        return "gif"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"Rar!"):
        return "rar"
    if head.startswith(b"7z\xbc\xaf"):
        return "7z"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"ID3"):
        return "mp3"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "matroska"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis"):
            return "heic"
        if brand in (b"avif", b"avis"):
            return "avif"
        if brand in (b"qt  ",):
            return "quicktime"
        return "mp4"
    return None


def true_extension(path: Path) -> str | None:
    kind = detect_type(path)
    if kind is None or kind in ("gzip", "ole"):
        return None
    return TRUE_EXT.get(kind)


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def duplicates(entries: Iterable[Entry], min_size: int = 1) -> list[dict]:
    """Size-group first, hash only the groups that can actually collide."""
    by_size: dict[int, list[Entry]] = defaultdict(list)
    for e in entries:
        if e.size >= min_size:
            by_size[e.size].append(e)
    groups: list[dict] = []
    hashed = 0
    for size, bucket in by_size.items():
        if len(bucket) < 2:
            continue
        seen: dict[str, list[str]] = defaultdict(list)
        for e in bucket:
            try:
                seen[sha256(e.path)].append(e.rel)
            except OSError:
                continue
            hashed += 1
        for digest, rels in seen.items():
            if len(rels) > 1:
                groups.append({"sha256": digest, "size": size, "count": len(rels), "paths": rels})
    groups.sort(key=lambda g: -g["size"] * g["count"])
    return groups


def audit(root: Path, exempt_dirs: set[str] | None = None,
          exempt_ext: set[str] | None = None, hash_dupes: bool = True) -> dict:
    """One pass, everything the report needs. Read-only."""
    from .convention import is_compliant

    exempt_dirs = DEFAULT_SKIP_DIRS if exempt_dirs is None else exempt_dirs
    exempt_ext = DEFAULT_SKIP_EXT if exempt_ext is None else exempt_ext
    entries = list(walk(root, include_dirs=True))
    files = [e for e in entries if not e.is_dir]
    dirs = [e for e in entries if e.is_dir]
    managed = [e for e in files if not is_ignored(e.rel, exempt_dirs)]

    by_area: dict[str, dict] = defaultdict(lambda: {"files": 0, "bytes": 0})
    for e in files:
        top = e.rel.split("/")[0]
        by_area[top]["files"] += 1
        by_area[top]["bytes"] += e.size

    offenders = [
        e.rel for e in managed
        if e.path.suffix.lower() not in exempt_ext
        and not e.path.name.startswith(".")
        and not is_compliant(e.path.name)
    ]

    ext_mismatch = [
        e.rel for e in managed
        if e.size and magic_matches(e.path) is False
    ]

    zero = [e.rel for e in files if e.size == 0]
    illegal = [e.rel for e in files if re.search(r'[<>:"|?*]', e.path.name)]
    long_names = [e.rel for e in files if len(e.path.name) > 180]
    empty_dirs = [d.rel for d in dirs if not any(d.path.iterdir())]

    report = {
        "root": str(root),
        "files": len(files),
        "dirs": len(dirs),
        "bytes": sum(e.size for e in files),
        "by_area": dict(sorted(by_area.items(), key=lambda kv: -kv[1]["bytes"])),
        "naming": {"non_compliant": len(offenders), "sample": offenders[:40]},
        "integrity": {"zero_bytes": len(zero), "sample_zero": zero[:20],
                      "extension_mismatch": len(ext_mismatch), "sample_mismatch": ext_mismatch[:20]},
        "hygiene": {"illegal_chars": len(illegal), "sample_chars": illegal[:10],
                    "long_names": len(long_names), "empty_dirs": len(empty_dirs),
                    "sample_empty_dirs": empty_dirs[:10]},
    }
    if hash_dupes:
        dupes = duplicates(managed)
        report["duplicates"] = {
            "groups": len(dupes),
            "wasted_bytes": sum(g["size"] * (g["count"] - 1) for g in dupes),
            "top": dupes[:25],
        }
    return report


def write_report(report: dict, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    return out


def summary_lines(report: dict) -> list[str]:
    mb = report["bytes"] / 1e9
    lines = [
        f"{report['files']} files · {report['dirs']} dirs · {mb:.2f} GB",
        f"naming: {report['naming']['non_compliant']} files off-convention",
        (f"integrity: {report['integrity']['zero_bytes']} empty, "
         f"{report['integrity']['extension_mismatch']} extension/content mismatches"),
    ]
    if "duplicates" in report:
        d = report["duplicates"]
        lines.append(f"duplicates: {d['groups']} groups, "
                     f"{d['wasted_bytes'] / 1e9:.2f} GB reclaimable")
    return lines
