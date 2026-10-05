import copy
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from saveharbor.core import BackupError, backup, diff, init, list_snapshots, load_snapshot, restore, verify


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source, self.store = self.root / "source", self.root / "store"
        self.source.mkdir()
        init(self.store)
        (self.source / "save.json").write_text('{"level":1}', encoding="utf-8")
        (self.source / "empty").mkdir()

    def snapshot(self):
        return backup(self.source, self.store)["snapshot"]

    def test_round_trip_files_unicode_and_empty_directories(self):
        (self.source / "目录").mkdir()
        (self.source / "目录/zero.bin").write_bytes(b"")
        (self.source / "目录/data.bin").write_bytes(bytes(range(256)))
        snapshot = self.snapshot()
        self.assertTrue(verify(self.store, snapshot)["verified"])
        target = self.root / "restored"
        self.assertTrue(restore(self.store, snapshot, target)["restored"])
        for path in self.source.rglob("*"):
            other = target / path.relative_to(self.source)
            if path.is_file():
                self.assertEqual(path.read_bytes(), other.read_bytes())
            else:
                self.assertTrue(other.is_dir())

    def test_randomized_round_trips(self):
        rng = random.Random(617)
        for number in range(50):
            directory = self.source / f"folder-{number % 5}"
            directory.mkdir(exist_ok=True)
            (directory / f"data-{number}.bin").write_bytes(rng.randbytes(rng.randint(0, 3000)))
        target = self.root / "restored"
        restore(self.store, self.snapshot(), target)
        self.assertEqual({p.relative_to(self.source).as_posix(): p.read_bytes() for p in self.source.rglob("*") if p.is_file()},
                         {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()})

    def test_content_deduplication_and_versions(self):
        first = self.snapshot()
        second = self.snapshot()
        self.assertNotEqual(first, second)
        self.assertEqual(len(list((self.store / "objects").iterdir())), 1)
        self.assertEqual(diff(self.store, first, second)["changed"], [])
        listed = list_snapshots(self.store)["snapshots"]
        self.assertEqual([item["snapshot"] for item in listed], [first, second])
        self.assertEqual(listed[0]["files"], 1)

    def test_path_and_handle_ctime_semantics_can_differ(self):
        original = os.fstat
        def different_ctime(descriptor):
            info = original(descriptor)
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_size=info.st_size,
                                   st_mtime_ns=info.st_mtime_ns, st_ctime_ns=info.st_ctime_ns + 10000)
        with patch("saveharbor.core.os.fstat", side_effect=different_ctime):
            snapshot = self.snapshot()
        self.assertTrue(verify(self.store, snapshot)["verified"])

    def test_diff_tracks_add_remove_change_and_directories(self):
        first = self.snapshot()
        (self.source / "save.json").write_text("new")
        (self.source / "other").write_text("added")
        (self.source / "newfolder").mkdir()
        second = self.snapshot()
        report = diff(self.store, first, second)
        self.assertEqual(report["changed"], ["save.json"])
        self.assertEqual(report["added"], ["other"])
        self.assertEqual(report["directories_added"], ["newfolder"])
        (self.source / "other").unlink()
        third = self.snapshot()
        self.assertEqual(diff(self.store, second, third)["removed"], ["other"])

    def test_exact_prefix_exclusion(self):
        (self.source / "cache").mkdir()
        (self.source / "cache/temp").write_bytes(b"ignored")
        (self.source / "cache-other").write_bytes(b"keep")
        manifest = backup(self.source, self.store, exclude=["cache"])
        self.assertNotIn("cache", manifest["directories"])
        self.assertNotIn("cache/temp", manifest["files"])
        self.assertIn("cache-other", manifest["files"])

    def test_existing_destination_never_overwritten(self):
        target = self.root / "existing"
        target.mkdir()
        (target / "save.json").write_text("original")
        with self.assertRaises(BackupError):
            restore(self.store, self.snapshot(), target)
        self.assertEqual((target / "save.json").read_text(), "original")

    def test_corrupt_object_rejected_before_destination_created(self):
        snapshot = self.snapshot()
        content = next((self.store / "objects").iterdir())
        content.write_bytes(b"broken")
        target = self.root / "target"
        with self.assertRaises(BackupError):
            verify(self.store, snapshot)
        with self.assertRaises(BackupError):
            restore(self.store, snapshot, target)
        self.assertFalse(target.exists())

    def test_missing_object_rejected(self):
        snapshot = self.snapshot()
        next((self.store / "objects").iterdir()).unlink()
        with self.assertRaises(BackupError):
            verify(self.store, snapshot)

    def test_corrupt_deduplicated_object_prevents_new_snapshot(self):
        self.snapshot()
        next((self.store / "objects").iterdir()).write_bytes(b"corrupt")
        with self.assertRaises(BackupError):
            self.snapshot()
        self.assertEqual(len(list((self.store / "snapshots").glob("*.json"))), 1)

    def test_source_change_after_reading_rejects_manifest_publication(self):
        original_link = os.link
        changed = False
        def mutate(source, target):
            nonlocal changed
            result = original_link(source, target)
            if not changed:
                changed = True
                (self.source / "save.json").write_text("changed")
            return result
        with patch("saveharbor.core.os.link", side_effect=mutate):
            with self.assertRaisesRegex(BackupError, "tree changed"):
                self.snapshot()
        self.assertEqual(list((self.store / "snapshots").glob("*.json")), [])

    def test_source_added_file_rejects_manifest_publication(self):
        original_link = os.link
        def mutate(source, target):
            result = original_link(source, target)
            (self.source / "new-file").write_text("new")
            return result
        with patch("saveharbor.core.os.link", side_effect=mutate):
            with self.assertRaisesRegex(BackupError, "tree changed"):
                self.snapshot()

    def test_unsafe_and_conflicting_manifest_paths(self):
        snapshot = self.snapshot()
        path = self.store / "snapshots" / f"{snapshot}.json"
        original = json.loads(path.read_text())
        for name in ("../escape", "/absolute", "C:/escape", "a\\b", "a//b", "CON", "name.", "dir/file"):
            candidate = copy.deepcopy(original)
            candidate["files"] = {name: next(iter(original["files"].values()))}
            path.write_text(json.dumps(candidate))
            with self.subTest(name=name), self.assertRaises(BackupError):
                load_snapshot(self.store, snapshot)

    def test_snapshot_version_and_digest_validation(self):
        snapshot = self.snapshot()
        path = self.store / "snapshots" / f"{snapshot}.json"
        original = json.loads(path.read_text())
        for mutate in (lambda p: p.update(version=True),
                       lambda p: p["files"]["save.json"].update(sha256="../outside"),
                       lambda p: p["files"]["save.json"].update(size=True),
                       lambda p: p.update(directories=["save.json"])):
            candidate = copy.deepcopy(original)
            mutate(candidate)
            path.write_text(json.dumps(candidate))
            with self.assertRaises(BackupError):
                load_snapshot(self.store, snapshot)

    def test_invalid_snapshot_id(self):
        for value in ("../escape", "", "a" * 33, None):
            with self.subTest(value=value), self.assertRaises(BackupError):
                load_snapshot(self.store, value)

    def test_store_and_source_overlap_rejected(self):
        with self.assertRaises(BackupError):
            backup(self.source, self.source)
        nested = self.source / "store"
        init(nested)
        with self.assertRaises(BackupError):
            backup(self.source, nested)
        with self.assertRaises(BackupError):
            restore(self.store, self.snapshot(), self.store / "inside")

    def test_initialization_refuses_existing_empty_directory(self):
        existing = self.root / "existing"
        existing.mkdir()
        with self.assertRaises(BackupError):
            init(existing)

    @unittest.skipIf(os.name == "nt", "Symlink creation requires Windows privileges")
    def test_symlink_files_store_and_source_rejected(self):
        (self.source / "link").symlink_to(self.root / "outside")
        with self.assertRaises(BackupError):
            self.snapshot()
        (self.source / "link").unlink()
        link = self.root / "source-link"
        link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(BackupError):
            backup(link, self.store)
        link = self.root / "store-link"
        link.symlink_to(self.store, target_is_directory=True)
        with self.assertRaises(BackupError):
            backup(self.source, link)

    def test_io_failure_does_not_publish_snapshot(self):
        with patch("saveharbor.core.os.link", side_effect=OSError("disk failure")):
            with self.assertRaises(BackupError):
                self.snapshot()
        self.assertEqual(list((self.store / "snapshots").glob("*.json")), [])
        self.assertEqual(list((self.store / "objects").glob(".pending-*")), [])

    def test_duplicate_manifest_keys_rejected(self):
        snapshot = self.snapshot()
        path = self.store / "snapshots" / f"{snapshot}.json"
        path.write_text(path.read_text().replace('"version": 1', '"version": 0, "version": 1'))
        with self.assertRaises(BackupError):
            load_snapshot(self.store, snapshot)

    def test_case_collision_manifest_rejected(self):
        snapshot = self.snapshot()
        path = self.store / "snapshots" / f"{snapshot}.json"
        data = json.loads(path.read_text())
        data["files"]["SAVE.JSON"] = data["files"]["save.json"]
        path.write_text(json.dumps(data))
        with self.assertRaises(BackupError):
            load_snapshot(self.store, snapshot)

    def test_cli_end_to_end_and_failure_code(self):
        def run(*args):
            return subprocess.run([sys.executable, "-m", "saveharbor", *map(str, args)], capture_output=True, text=True)
        result = run("backup", self.source, self.store)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        snapshot = json.loads(result.stdout)["snapshot"]
        self.assertEqual(run("verify", self.store, snapshot).returncode, 0)
        target = self.root / "cli-restore"
        self.assertEqual(run("restore", self.store, snapshot, target).returncode, 0)
        self.assertEqual(run("restore", self.store, snapshot, target).returncode, 2)


if __name__ == "__main__":
    unittest.main()
