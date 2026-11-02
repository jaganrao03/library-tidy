"""Command line entry point: `python -m library_tidy <command> …`."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, rename, scan
from .convention import is_compliant

PROTECTED = {Path("/"), Path.home(), Path.home() / ".hermes"}


def _check_root(p: Path) -> Path:
    root = p.expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")
    if root in PROTECTED or root in {x.parent for x in PROTECTED if x != Path("/")}:
        sys.exit(f"refusing to operate on {root} — point at the library root itself")
    return root


def cmd_audit(args) -> int:
    root = _check_root(Path(args.root))
    report = scan.audit(root, hash_dupes=not args.no_hash)
    report["generated"] = datetime.now().isoformat(timespec="seconds")
    report["version"] = __version__
    if args.json:
        out = scan.write_report(report, Path(args.json).expanduser())
        print(f"report: {out}")
    for line in scan.summary_lines(report):
        print(line)
    if report["naming"]["non_compliant"]:
        print("\noff-convention sample:")
        for rel in report["naming"]["sample"][: args.limit]:
            print("  ", rel)
    if report.get("duplicates", {}).get("top"):
        print("\nbiggest duplicate groups:")
        for g in report["duplicates"]["top"][: args.limit]:
            print(f"  {g['count']}× {g['size'] / 1e6:.1f} MB — {g['paths'][0]}")
    if args.strict and report["naming"]["non_compliant"]:
        return 1
    return 0


def _namer(args):
    if args.kind == "photo":
        return rename.photo_namer(device=args.device or "Camera")
    if args.kind == "video":
        return rename.video_namer(source_hint=args.source or "")
    return rename.document_namer(party=args.party or "Me")


def cmd_plan(args) -> int:
    root = _check_root(Path(args.root))
    plan = rename.build_plan(root, _namer(args), skip_dirs=args.skip_dir or ())
    print(f"{len(plan)} file(s) would be renamed, {plan.skipped} already fine")
    for item in plan.items[: args.limit]:
        print(f"  {item.src.relative_to(root).as_posix()}")
        print(f"    -> {item.dst.name}")
    print("\ndry run — add --apply to change anything")
    return 0


def cmd_apply(args) -> int:
    root = _check_root(Path(args.root))
    plan = rename.build_plan(root, _namer(args), skip_dirs=args.skip_dir or ())
    print(f"{len(plan)} file(s) to rename")
    for item in plan.items[:5]:
        print(f"  {item.src.name}  ->  {item.dst.name}")
    if not args.apply:
        print("dry run — pass --apply")
        return 0
    journal = rename.apply(plan, Path(args.journal or (root / "_Library Metadata")))
    print(f"renamed {len(plan)}; journal {journal}")
    print(f"undo with: python -m library_tidy undo {journal} --apply")
    return 0


def cmd_undo(args) -> int:
    pairs = rename.undo(Path(args.journal), dry_run=not args.apply)
    print(f"{len(pairs)} rename(s) can be reversed")
    for cur, back in pairs[: args.limit]:
        print(f"  {cur.name}  ->  {back.name}")
    if not args.apply:
        print("dry run — pass --apply")
        return 0
    rename.undo(Path(args.journal), dry_run=False)
    print("reversed")
    return 0


def cmd_dedupe(args) -> int:
    root = _check_root(Path(args.root))
    skip = set(args.skip_dir or scan.DEFAULT_SKIP_DIRS) | {"_Duplicates", "_Library Metadata"}
    entries = [e for e in scan.walk(root) if not scan.is_ignored(e.rel, skip)]
    groups = scan.duplicates(entries, min_size=max(args.min_size, 1))
    if not groups:
        print("no duplicates")
        return 0
    wasted = sum(g["size"] * (g["count"] - 1) for g in groups)
    print(f"{len(groups)} duplicate group(s), {wasted / 1e9:.2f} GB reclaimable")
    plan = rename.dedupe_plan(root, groups)
    print(f"keep first of each group; move {len(plan)} copy(ies) to _Duplicates/")
    for item in plan.items[: args.limit]:
        print(f"  {item.src.relative_to(root).as_posix()}")
    if not args.apply:
        print("\ndry run — pass --apply to quarantine (never deletes)")
        return 0
    journal = rename.apply(plan, root / "_Library Metadata")
    print(f"moved {len(plan)}; journal {journal}")
    return 0


def cmd_fixext(args) -> int:
    root = _check_root(Path(args.root))
    plan = rename.ext_fix_plan(root, skip_dirs=args.skip_dir or ())
    print(f"{len(plan)} file(s) have an extension their bytes contradict")
    for item in plan.items[: args.limit]:
        print(f"  {item.src.name}  ->  {item.dst.name}")
    if not args.apply:
        print("\ndry run — pass --apply")
        return 0
    journal = rename.apply(plan, Path(args.journal or (root / "_Library Metadata")))
    print(f"fixed {len(plan)}; journal {journal}")
    return 0


def cmd_resort(args) -> int:
    root = _check_root(Path(args.root))
    plan = rename.resort_plan(root, skip_dirs=args.skip_dir or ())
    print(f"{len(plan)} file(s) sit in a date folder that contradicts their name")
    for item in plan.items[: args.limit]:
        print(f"  {item.src.relative_to(root).as_posix()}")
        print(f"    -> {item.dst.relative_to(root).as_posix()}")
    if not args.apply:
        print("\ndry run — pass --apply")
        return 0
    journal = rename.apply(plan, Path(args.journal or (root / "_Library Metadata")))
    print(f"moved {len(plan)}; journal {journal}")
    return 0


def cmd_gallery(args) -> int:
    from .gallery import Gallery

    root = _check_root(Path(args.root))
    Gallery(root=root, port=args.port, title=args.title,
            user=args.user or "", password=args.password or "",
            host=args.host).serve_forever()
    return 0


def cmd_check(args) -> int:
    """Exit non-zero if the tree is not compliant — for cron/CI use."""
    root = _check_root(Path(args.root))
    bad = [e.rel for e in scan.walk(root) if not is_compliant(e.path.name)]
    print(f"{len(bad)} file(s) off-convention")
    for rel in bad[: args.limit]:
        print("  ", rel)
    return 1 if bad else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="library-tidy",
                                description="Turn a drive full of accumulated files into a dated, "
                                            "searchable library (DATE - PARTY - TYPE - TOPIC).")
    p.add_argument("--version", action="version", version=f"library-tidy {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("root", help="library root directory")
        sp.add_argument("--limit", type=int, default=15)
        sp.add_argument("--skip-dir", action="append", metavar="NAME",
                        help="directory name to leave alone (repeatable)")

    a = sub.add_parser("audit", help="report naming, integrity, duplicates")
    common(a)
    a.add_argument("--json", help="write the full report to this path")
    a.add_argument("--no-hash", action="store_true", help="skip duplicate hashing (fast)")
    a.add_argument("--strict", action="store_true", help="exit 1 if anything is off-convention")
    a.set_defaults(func=cmd_audit)

    for name, fn, helptext in (("plan", cmd_plan, "show what would be renamed"),
                               ("apply", cmd_apply, "rename (dry run unless --apply)")):
        sp = sub.add_parser(name, help=helptext)
        common(sp)
        sp.add_argument("--kind", choices=["photo", "video", "document"], required=True)
        sp.add_argument("--party", help="party segment for documents (default: Me)")
        sp.add_argument("--device", help="device segment for photos (default: Camera)")
        sp.add_argument("--source", help="source hint for videos (Telegram, WhatsApp, …)")
        sp.add_argument("--journal", help="journal directory")
        sp.add_argument("--apply", action="store_true")
        sp.set_defaults(func=fn)

    u = sub.add_parser("undo", help="reverse a rename journal")
    u.add_argument("journal")
    u.add_argument("--apply", action="store_true")
    u.add_argument("--limit", type=int, default=15)
    u.set_defaults(func=cmd_undo)

    d = sub.add_parser("dedupe", help="quarantine duplicate copies (never deletes)")
    common(d)
    d.add_argument("--min-size", type=int, default=1)
    d.add_argument("--apply", action="store_true")
    d.set_defaults(func=cmd_dedupe)

    for name, fn, helptext in (("fixext", cmd_fixext, "rename files to the extension their bytes prove"),
                               ("resort", cmd_resort, "move files into the date folder their name claims")):
        sp = sub.add_parser(name, help=helptext)
        common(sp)
        sp.add_argument("--journal", help="journal directory")
        sp.add_argument("--apply", action="store_true")
        sp.set_defaults(func=fn)

    g = sub.add_parser("gallery", help="serve a read-only phone/browser view")
    g.add_argument("root")
    g.add_argument("--port", type=int, default=8451)
    g.add_argument("--host", default="127.0.0.1")
    g.add_argument("--title", default="Library")
    g.add_argument("--user", default="")
    g.add_argument("--password", default="", help="HTTP Basic password (empty = no auth)")
    g.set_defaults(func=cmd_gallery)

    c = sub.add_parser("check", help="compliance gate for cron/CI")
    c.add_argument("root")
    c.add_argument("--limit", type=int, default=15)
    c.set_defaults(func=cmd_check)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
