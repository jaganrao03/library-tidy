"""Tests run only inside a temporary directory — never against a real library."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from library_tidy import rename, scan
from library_tidy.convention import build_name, is_compliant, parse_stamp, slug


class ConventionTests(unittest.TestCase):
    def test_dated_name_with_time(self):
        n = build_name(datetime(2024, 10, 1, 8, 24, 38), "OnePlus 11", "Photo", "", ".JPG")
        self.assertEqual(n, "2024-10-01 08.24.38 - OnePlus 11 - Photo.jpg")

    def test_month_only_and_undated(self):
        self.assertEqual(build_name(datetime(2022, 8, 1), "Me", "Archive", "Project", ".zip"),
                         "2022-08-01 - Me - Archive - Project.zip")
        self.assertEqual(build_name(None, "Me", "Document", "Notes", ".pdf"),
                         "Undated - Me - Document - Notes.pdf")

    def test_slug_removes_unsafe_characters(self):
        self.assertEqual(slug('Report "final" <2>.pdf'), "Report final 2 .pdf")
        self.assertEqual(slug("hollywood_ikkada-20240917-0001", 40), "hollywood ikkada")
        self.assertEqual(slug("   "), "Untitled")

    def test_compliance_and_parse(self):
        self.assertTrue(is_compliant("2024-10-01 08.24.38 - OnePlus 11 - Photo.jpg"))
        self.assertTrue(is_compliant("2021-Fall - University of Memphis - Notes - Week 1.txt"))
        self.assertTrue(is_compliant("Undated - Me - Archive - Old.zip"))
        self.assertFalse(is_compliant("IMG_2043.jpg"))
        self.assertEqual(parse_stamp("2024-10-01 08.24.38 - X - Photo.jpg"), datetime(2024, 10, 1))

    def test_dates_from_filenames(self):
        cases = {
            "VID_20241207193726.mp4": datetime(2024, 12, 7, 19, 37, 26),
            "Screenshot 2024-05-26 at 12.57.21.png": datetime(2024, 5, 26),
            "20241001_083045.jpg": datetime(2024, 10, 1, 8, 30, 45),
            "no-date-here.jpg": None,
        }
        for name, want in cases.items():
            got = scan.date_from_name(name)
            self.assertEqual(got, want, name)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "lib"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _touch(self, name, body=b"x"):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        return p

    def test_two_sources_never_share_a_target(self):
        """The bug this guards: Path.rename replaces silently on POSIX."""
        (self.root / "2026-06-08").mkdir()
        for i in (1, 2):
            (self.root / "2026-06-08" / f"clip-20260608-000{i}.mp4").write_bytes(b"v")

        def namer(entry, root):
            return "2026-06-08 - Video - Camera.mp4"

        plan = rename.build_plan(self.root, namer,
                                 select=lambda e: e.path.suffix == ".mp4")
        targets = [i.dst.name for i in plan.items]
        self.assertEqual(len(targets), len(set(targets)), "collision slipped through")
        self.assertIn("2026-06-08 - Video - Camera.mp4", targets)
        self.assertTrue(any("(1)" in t for t in targets))

    def test_apply_then_undo_restores_names(self):
        a = self._touch("photo/IMG_0001.jpg")
        b = self._touch("photo/IMG_0002.jpg")
        plan = rename.build_plan(
            self.root,
            lambda e, r: f"2024-01-01 - Cam - Photo{e.path.suffix}",
            select=lambda e: e.path.suffix == ".jpg")
        journal = rename.apply(plan, self.root / "_meta")
        self.assertEqual(len(list((self.root / "photo").glob("*.jpg"))), 2, "a file was lost")
        rename.undo(journal, dry_run=False)
        self.assertTrue(a.exists())
        self.assertTrue(b.exists())

    def test_two_apply_runs_never_share_a_journal(self):
        """Same-second runs used to overwrite each other's undo trail."""
        for i in range(2):
            self._touch(f"p{i}.txt")
            plan = rename.build_plan(self.root, lambda e, r, i=i: f"2024-01-0{i + 1} - Me - Note.txt",
                                     select=lambda e: e.path.suffix == ".txt")
            rename.apply(plan, self.root / "_j")
        self.assertEqual(len(list((self.root / "_j").glob("*.jsonl"))), 2)

    def test_audit_flags_off_convention(self):
        self._touch("2024-01-01 - Me - Note - Ok.txt")
        self._touch("IMG_9999.jpg")
        report = scan.audit(self.root, hash_dupes=False)
        self.assertEqual(report["naming"]["non_compliant"], 1)
        self.assertEqual(report["files"], 2)

    def test_duplicates_found_by_content(self):
        self._touch("a/one.bin", b"same bytes")
        self._touch("b/two.bin", b"same bytes")
        self._touch("c/other.bin", b"different")
        groups = scan.duplicates(list(scan.walk(self.root)))
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["count"], 2)

    def test_extension_mismatch_detected(self):
        p = self.root / "liar.jpg"
        p.write_bytes(b"%PDF-1.7 not an image")
        report = scan.audit(self.root, hash_dupes=False)
        self.assertEqual(report["integrity"]["extension_mismatch"], 1)


class IgnoreRuleTests(unittest.TestCase):
    def test_dot_directories_and_skip_dirs_are_ignored(self):
        skip = scan.DEFAULT_SKIP_DIRS
        self.assertTrue(scan.is_ignored(".git/objects/a", skip))
        self.assertTrue(scan.is_ignored("Docs/.thumbs/a.jpg", skip))
        self.assertTrue(scan.is_ignored("_Library Metadata/j.jsonl", skip))
        self.assertFalse(scan.is_ignored("Docs/a.pdf", skip))
        self.assertFalse(scan.is_ignored("_redirects", skip))

    def test_duplicate_inside_dot_dir_is_not_a_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("Docs", ".hidden"):
                (root / d).mkdir()
            (root / "Docs" / "a.pdf").write_bytes(b"same" * 100)
            (root / ".hidden" / "b.pdf").write_bytes(b"same" * 100)
            kept = [e for e in scan.walk(root) if not scan.is_ignored(e.rel, scan.DEFAULT_SKIP_DIRS)]
            self.assertEqual(scan.duplicates(kept, min_size=1), [])


class GalleryPathTests(unittest.TestCase):
    def test_traversal_is_refused(self):
        from library_tidy.gallery import Gallery
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "lib"
            root.mkdir()
            g = Gallery(root=root, cache_dir=Path(tmp) / "cache")
            with self.assertRaises(PermissionError):
                g.resolve("../../etc/passwd")
            self.assertEqual(g.resolve("sub/file.txt"), (root / "sub/file.txt").resolve())


if __name__ == "__main__":
    unittest.main()
