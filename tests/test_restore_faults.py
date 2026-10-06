"""Synthetic-only restore consistency and publication fault acceptance tests."""
import copy
import errno
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from saveharbor import core
from saveharbor.__main__ import main


class RestoreFaultTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.store = self.root / "source", self.root / "store"
        self.source.mkdir()
        core.init(self.store)
        (self.source / "save.bin").write_bytes(b"old-save")
        self.manifest = core.backup(self.source, self.store)
        self.snapshot = self.manifest["snapshot"]
        self.target = self.root / "restored"

    def stored_bytes(self):
        return {p.relative_to(self.store).as_posix(): p.read_bytes()
                for p in self.store.rglob("*") if p.is_file()}

    def test_restore_pins_the_manifest_that_was_preflighted(self):
        (self.source / "save.bin").write_bytes(b"new-save")
        newer = core.backup(self.source, self.store)
        replacement = copy.deepcopy(self.manifest)
        replacement["files"] = newer["files"]
        path = self.store / "snapshots" / f"{self.snapshot}.json"
        original_digest = core._digest
        changed = False

        def change_manifest_after_verification(object_path):
            nonlocal changed
            result = original_digest(object_path)
            if not changed:
                changed = True
                path.write_text(json.dumps(replacement), encoding="utf-8")
            return result

        with patch.object(core, "_digest", side_effect=change_manifest_after_verification):
            result = core.restore(self.store, self.snapshot, self.target)
        self.assertTrue(changed)
        self.assertTrue(result["restored"])
        self.assertEqual((self.target / "save.bin").read_bytes(), b"old-save")
        self.assertEqual((self.source / "save.bin").read_bytes(), b"new-save")
        self.assertTrue(core.verify(self.store, newer["snapshot"])["verified"])

    def test_restore_loads_exactly_one_validated_manifest(self):
        before = self.stored_bytes()
        with patch.object(core, "load_snapshot", wraps=core.load_snapshot) as loader:
            core.restore(self.store, self.snapshot, self.target)
        self.assertEqual(loader.call_count, 1)
        self.assertEqual(self.stored_bytes(), before)

    def test_all_objects_are_preflighted_before_target_creation(self):
        (self.source / "z-last.bin").write_bytes(b"last-object")
        manifest = core.backup(self.source, self.store)
        path = self.store / "objects" / manifest["files"]["z-last.bin"]["sha256"]
        path.write_bytes(b"damaged")
        before = self.stored_bytes()
        with self.assertRaisesRegex(core.BackupError, "mismatch"):
            core.restore(self.store, manifest["snapshot"], self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.stored_bytes(), before)
        self.assertEqual((self.source / "z-last.bin").read_bytes(), b"last-object")

    def test_object_change_after_preflight_is_still_rejected(self):
        path = self.store / "objects" / self.manifest["files"]["save.bin"]["sha256"]
        original_mkdir = Path.mkdir

        def mutate_at_target_creation(directory, *args, **kwargs):
            if directory == self.target:
                path.write_bytes(b"bad-save")  # Same size: digest, not just size, must fail.
            return original_mkdir(directory, *args, **kwargs)

        with patch.object(Path, "mkdir", new=mutate_at_target_creation):
            with self.assertRaisesRegex(core.BackupError, "changed during restore"):
                core.restore(self.store, self.snapshot, self.target)
        self.assertEqual((self.target / "save.bin").read_bytes(), b"bad-save")
        self.assertEqual((self.source / "save.bin").read_bytes(), b"old-save")
        with self.assertRaises(core.BackupError):
            core.verify(self.store, self.snapshot)

    def test_partial_write_error_retains_failed_new_target_without_overwrite(self):
        before = self.stored_bytes()
        original_open = Path.open

        class FailingOutput:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def write(self, content):
                self.stream.write(content[:3])
                self.stream.flush()
                raise OSError(errno.ENOSPC, "injected disk full; no host disk filled")

        def faulting_open(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            if path == self.target / "save.bin" and mode == "xb":
                return FailingOutput(stream)
            return stream

        with patch.object(Path, "open", new=faulting_open):
            with self.assertRaisesRegex(core.BackupError, "incomplete"):
                core.restore(self.store, self.snapshot, self.target)
        self.assertEqual((self.target / "save.bin").read_bytes(), b"old")
        with self.assertRaises(core.BackupError):
            core.restore(self.store, self.snapshot, self.target)
        self.assertEqual((self.target / "save.bin").read_bytes(), b"old")
        self.assertEqual(self.stored_bytes(), before)
        self.assertTrue(core.verify(self.store, self.snapshot)["verified"])

    def test_restore_fsync_error_is_cli_failure_not_success(self):
        before = self.stored_bytes()
        output = io.StringIO()
        with patch.object(core.os, "fsync", side_effect=OSError(errno.EIO, "injected sync failure")):
            with redirect_stdout(output):
                result = main(["restore", str(self.store), self.snapshot, str(self.target)])
        self.assertEqual(result, 2)
        report = json.loads(output.getvalue())
        self.assertFalse(report["complete"])
        self.assertNotIn("restored", report)
        self.assertIn("incomplete", report["error"])
        self.assertEqual((self.target / "save.bin").read_bytes(), b"old-save")
        self.assertEqual(self.stored_bytes(), before)
        self.assertTrue(core.verify(self.store, self.snapshot)["verified"])

    def test_manifest_sync_failure_preserves_previous_snapshot(self):
        before = self.stored_bytes()
        # Duplicate backup has only a manifest fsync, not an object fsync.
        with patch.object(core.os, "fsync", side_effect=OSError(errno.EIO, "injected manifest sync failure")):
            with self.assertRaises(core.BackupError):
                core.backup(self.source, self.store)
        self.assertEqual(self.stored_bytes(), before)
        self.assertTrue(core.verify(self.store, self.snapshot)["verified"])

    def test_manifest_publication_failure_preserves_previous_snapshot(self):
        before = self.stored_bytes()
        original_link = os.link
        hit = False

        def faulting_link(source, target, *args, **kwargs):
            nonlocal hit
            if Path(target).parent == self.store / "snapshots":
                hit = True
                raise OSError(errno.EIO, "injected manifest publication failure")
            return original_link(source, target, *args, **kwargs)

        with patch.object(core.os, "link", side_effect=faulting_link):
            with self.assertRaises(core.BackupError):
                core.backup(self.source, self.store)
        self.assertTrue(hit)
        self.assertEqual(self.stored_bytes(), before)
        self.assertTrue(core.verify(self.store, self.snapshot)["verified"])


if __name__ == "__main__":
    unittest.main()
