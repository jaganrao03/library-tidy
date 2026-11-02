"""Planning and applying renames — the part that must never lose a file.

Two rules are baked in because ignoring them is how batch renamers destroy data:

1. Every target is claimed before anything moves, so two sources can never map
   onto one destination. On POSIX `Path.rename` silently replaces an existing
   file; there is no error to catch afterwards.
2. Every applied rename is journalled with both paths relative to the root, so
   `undo` can walk it backwards, including files that were renamed twice.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import scan
from .convention import build_name

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".mpg", ".mpeg"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".tif", ".tiff"}

DOC_TYPES = {
    ".pdf": "Document", ".docx": "Document", ".doc": "Document", ".odt": "Document",
    ".rtf": "Document", ".txt": "Notes", ".md": "Notes", ".one": "Notes",
    ".xlsx": "Spreadsheet", ".xls": "Spreadsheet", ".ods": "Spreadsheet", ".csv": "Data",
    ".pptx": "Presentation", ".ppt": "Presentation", ".odp": "Presentation",
    ".zip": "Archive", ".rar": "Archive", ".7z": "Archive", ".tar": "Archive",
    ".gz": "Archive", ".tgz": "Archive", ".zst": "Archive",
    ".drawio": "Diagram", ".odg": "Diagram", ".svg": "Diagram",
    ".eml": "Email", ".indd": "Design Source", ".prproj": "Project",
}

# Directory name -> the noun used in the name, so folders drive vocabulary
# instead of a hardcoded guess.
FOLDER_KINDS = {
    "wallpapers": "Wallpaper", "screenshots": "Screenshot", "screen recordings": "Screen Recording",
    "saved pictures": "Saved Picture", "designs": "Design", "captures": "Capture",
    "recordings": "Recording", "telegram": "Telegram", "downloads": "Download",
    "whatsapp": "WhatsApp", "camera": "Photo", "documents": "Document", "scans": "Scan",
}


@dataclass
class Item:
    src: Path
    dst: Path


@dataclass
class Plan:
    root: Path
    items: list[Item] = field(default_factory=list)
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.items)

    def check(self) -> None:
        targets = [i.dst for i in self.items]
        if len(set(targets)) != len(targets):
            dupes = {t for t in targets if targets.count(t) > 1}
            raise RuntimeError(f"plan would overwrite {len(dupes)} target(s): {list(dupes)[:3]}")
        for i in self.items:
            if i.dst.exists() and i.dst != i.src:
                raise RuntimeError(f"target already exists on disk: {i.dst}")


def _claim(target: Path, src: Path, claimed: set[Path]) -> Path:
    base, n = target, 1
    while (base.exists() and base != src) or base in claimed:
        base = target.with_name(f"{target.stem} ({n}){target.suffix}")
        n += 1
    claimed.add(base)
    return base


def build_plan(root: Path, namer: Callable[[scan.Entry, Path], str | None],
               select: Callable[[scan.Entry], bool] = lambda e: True,
               skip_dirs: Iterable[str] = ()) -> Plan:
    """Walk the tree, let `namer` propose a new filename, plan it collision-safely."""
    root = root.resolve()
    skip = set(skip_dirs)
    plan = Plan(root=root)
    claimed: set[Path] = set()
    for entry in scan.walk(root):
        rel_parts = entry.rel.split("/")[:-1]
        if skip & set(rel_parts):
            continue
        if not select(entry):
            plan.skipped += 1
            continue
        proposed = namer(entry, root)
        if not proposed or proposed == entry.path.name:
            plan.skipped += 1
            continue
        target = entry.path.with_name(proposed)
        plan.items.append(Item(entry.path, _claim(target, entry.path, claimed)))
    plan.check()
    return plan


# --------------------------------------------------------------------------- namers

def photo_namer(device: str = "Camera", fallback_party: str = "") -> Callable:
    def namer(entry: scan.Entry, root: Path) -> str | None:
        p = entry.path
        if p.suffix.lower() not in IMAGE_EXT:
            return None
        parents = [x.lower() for x in entry.rel.split("/")[:-1]]
        kind = next((FOLDER_KINDS[x] for x in reversed(parents) if x in FOLDER_KINDS), "Photo")
        date, source = scan.best_date(p)
        party = fallback_party or device
        topic = ""
        if source == "mtime" and kind in ("Photo", "Screenshot"):
            # no honest date anywhere: keep it undated rather than invent one
            date = None
        if kind not in ("Photo", "Screenshot", "Screen Recording"):
            topic = p.stem
        return build_name(date, party, kind, topic, p.suffix)
    return namer


def video_namer(source_hint: str = "") -> Callable:
    def namer(entry: scan.Entry, root: Path) -> str | None:
        p = entry.path
        if p.suffix.lower() not in VIDEO_EXT:
            return None
        parents = [x.lower() for x in entry.rel.split("/")[:-1]]
        kind = next((FOLDER_KINDS[x] for x in reversed(parents) if x in FOLDER_KINDS), "Video")
        hint = source_hint or next((FOLDER_KINDS[x] for x in reversed(parents)
                                    if x in ("telegram", "whatsapp", "captures", "recordings")), "")
        date, source = scan.best_date(p)
        if source == "mtime":
            date = None
        topic = "" if kind in ("Capture", "Recording", "Screen Recording") else p.stem
        return build_name(date, hint or "Video", kind, topic, p.suffix)
    return namer


def document_namer(party: str = "Me") -> Callable:
    def namer(entry: scan.Entry, root: Path) -> str | None:
        p = entry.path
        typ = DOC_TYPES.get(p.suffix.lower())
        if not typ:
            return None
        date, source = scan.best_date(p, allow_media=False)
        if source == "mtime":
            date = None
        return build_name(date, party, typ, p.stem, p.suffix)
    return namer


# --------------------------------------------------------------------------- apply / undo

def apply(plan: Plan, journal_dir: Path) -> Path:
    """Execute a plan. Refuses to start if the plan is unsafe."""
    plan.check()
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal = journal_dir / f"rename-{datetime.now():%Y%m%dT%H%M%S}.jsonl"
    with journal.open("w") as fh:
        fh.write(json.dumps({"type": "header", "root": str(plan.root),
                             "when": datetime.now().isoformat(timespec="seconds"),
                             "count": len(plan.items)}) + "\n")
        for item in plan.items:
            if item.dst.parent != item.src.parent:
                item.dst.parent.mkdir(parents=True, exist_ok=True)
            item.src.rename(item.dst)
            fh.write(json.dumps({
                "type": "rename",
                "old": item.src.relative_to(plan.root).as_posix(),
                "new": item.dst.relative_to(plan.root).as_posix(),
            }) + "\n")
    return journal


def undo(journal: Path, dry_run: bool = True) -> list[tuple[Path, Path]]:
    """Reverse a journal, newest rename first, so double-renamed files land back."""
    root = None
    pairs: list[tuple[Path, Path]] = []
    with journal.open() as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("type") == "header":
                root = Path(rec["root"])
                continue
            if rec.get("type") != "rename":
                continue
            cur = root / rec["new"]
            back = root / rec["old"]
            if cur.exists():
                pairs.append((cur, back))
    if dry_run:
        return pairs
    for cur, back in reversed(pairs):
        if back.exists():
            back = back.with_name(f"{back.stem} (restored){back.suffix}")
        cur.rename(back)
    return pairs


# --------------------------------------------------------------------------- repairs

def ext_fix_plan(root: Path, skip_dirs: Iterable[str] = ()) -> Plan:
    """Rename files to the extension their bytes actually prove.

    A .jpg that is really WebP or HEIC is the single most common reason a photo
    shows as a blank tile on a phone: the client decodes by extension.
    """
    root = root.resolve()
    skip = set(skip_dirs)
    plan = Plan(root=root)
    claimed: set[Path] = set()
    for entry in scan.walk(root):
        if skip & set(entry.rel.split("/")[:-1]):
            continue
        p = entry.path
        current = p.suffix.lower()
        if current not in scan.MEDIA_LIKE:
            continue
        true_ext = scan.true_extension(p)
        if not true_ext or true_ext == current:
            continue
        target = p.with_name(p.stem + true_ext)
        plan.items.append(Item(p, _claim(target, p, claimed)))
    plan.check()
    return plan


DATE_DIR = __import__("re").compile(r"^(20\d{2})(?:-(0[1-9]|1[0-2]))?$")


def resort_plan(root: Path, skip_dirs: Iterable[str] = ()) -> Plan:
    """Correct a file whose date-shaped parent folder disagrees with its own date.

    Only the nearest date-shaped ancestor is touched, so a tree like
    Photos/<device>/2024-10/ keeps its shape and just stops lying.
    """
    from .convention import parse_stamp  # local import keeps module deps flat

    root = root.resolve()
    skip = set(skip_dirs)
    plan = Plan(root=root)
    claimed: set[Path] = set()
    for entry in scan.walk(root):
        parts = entry.rel.split("/")
        if skip & set(parts[:-1]):
            continue
        when = parse_stamp(entry.path.name)
        if not when:
            continue
        idx = None
        for i in range(len(parts) - 2, -1, -1):
            if DATE_DIR.match(parts[i]):
                idx = i
                break
        if idx is None:
            continue
        folder = parts[idx]
        m = DATE_DIR.match(folder)
        want = f"{when:%Y}" if not m.group(2) else f"{when:%Y-%m}"
        if folder == want:
            continue
        new_parts = list(parts)
        new_parts[idx] = want
        target = root.joinpath(*new_parts)
        plan.items.append(Item(entry.path, _claim(target, entry.path, claimed)))
    plan.check()
    return plan


def dedupe_plan(root: Path, groups: list[dict]) -> Plan:
    """Keep the shortest path in each duplicate group, plan the rest for removal
    into a quarantine folder — never delete."""
    root = root.resolve()
    plan = Plan(root=root)
    claimed: set[Path] = set()
    qdir = root / "_Duplicates"
    for g in groups:
        keep = min(g["paths"], key=lambda r: (len(r), r))
        for rel in g["paths"]:
            if rel == keep:
                continue
            src = root / rel
            dst = qdir / rel
            plan.items.append(Item(src, _claim(dst, src, claimed)))
    return plan
