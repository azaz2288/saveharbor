"""Read-only policy and real restore rehearsals on synthetic stores only."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from saveharbor import core
from saveharbor.__main__ import main


class RetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.store, self.target = [self.root / name for name in ('source', 'store', 'rehearsal')]
        self.source.mkdir()
        (self.source / 'empty').mkdir()
        (self.source / '共享.txt').write_bytes(b'shared')
        core.init(self.store)
        self.snapshots = []
        for index in range(3):
            (self.source / 'save.bin').write_bytes(f'version-{index}'.encode())
            manifest = core.backup(self.source, self.store)
            self.snapshots.append(manifest)
            self.stamp(manifest, f'2026-10-0{index + 1}T00:00:00+00:00')

    def stamp(self, manifest, created):
        manifest['created_at'] = created
        (self.store / 'snapshots' / f"{manifest['snapshot']}.json").write_text(json.dumps(manifest), encoding='utf-8')

    def stored_bytes(self):
        return {p.relative_to(self.store).as_posix(): p.read_bytes() for p in self.store.rglob('*') if p.is_file()}

    def test_policy_keeps_latest_and_protected_without_modifying_store(self):
        before = self.stored_bytes()
        oldest, middle, newest = [m['snapshot'] for m in self.snapshots]
        report = core.retention_plan(self.store, keep_latest=1, protect=[oldest])
        self.assertEqual([m['snapshot'] for m in report['keep']], [newest, oldest])
        self.assertEqual([m['snapshot'] for m in report['retire_candidates']], [middle])
        self.assertFalse(report['executable'])
        self.assertFalse(report['store_modified'])
        self.assertEqual(self.stored_bytes(), before)
        shared = hashlib.sha256(b'shared').hexdigest()
        self.assertNotIn(shared, [o['sha256'] for o in report['object_candidates_if_retired']])
        self.assertEqual(report['candidate_content_bytes'], len(b'version-1'))

    def test_unique_snapshot_and_oversized_keep_never_retire_the_only_version(self):
        report = core.retention_plan(self.store, keep_latest=1000)
        self.assertEqual(len(report['keep']), 3)
        self.assertEqual(report['retire_candidates'], [])
        unique = self.root / 'unique'
        core.init(unique)
        manifest = core.backup(self.source, unique)
        report = core.retention_plan(unique, keep_latest=1)
        self.assertEqual([m['snapshot'] for m in report['keep']], [manifest['snapshot']])
        self.assertEqual(report['object_candidates_if_retired'], [])

    def test_utc_order_not_raw_offset_text_and_ties_are_deterministic(self):
        # Oct 3 01:00+14 is earlier than Oct 2 12:00Z, despite ISO text order.
        self.stamp(self.snapshots[0], '2026-10-03T01:00:00+14:00')
        self.stamp(self.snapshots[1], '2026-10-02T12:00:00+00:00')
        self.stamp(self.snapshots[2], '2026-10-02T12:00:00+00:00')
        chosen = max(self.snapshots[1]['snapshot'], self.snapshots[2]['snapshot'])
        self.assertEqual(core.retention_plan(self.store, keep_latest=1)['keep'][0]['snapshot'], chosen)
        self.assertEqual(core.retention_plan(self.store, keep_latest=1), core.retention_plan(self.store, keep_latest=1))

    def test_rehearsal_actually_restores_all_retained_versions_and_empty_directories(self):
        before = self.stored_bytes()
        oldest, _, newest = [m['snapshot'] for m in self.snapshots]
        report = core.rehearse_retention(self.store, self.target, keep_latest=1, protect=[oldest])
        self.assertTrue(report['restore_rehearsal']['verified'])
        self.assertEqual(report['restore_rehearsal']['snapshots'], [newest, oldest])
        for index in (0, 2):
            restored = self.target / self.snapshots[index]['snapshot']
            self.assertEqual((restored / 'save.bin').read_bytes(), f'version-{index}'.encode())
            self.assertEqual((restored / '共享.txt').read_bytes(), b'shared')
            self.assertTrue((restored / 'empty').is_dir())
        self.assertEqual(self.stored_bytes(), before)

    def test_invalid_policy_and_missing_protected_id_refused_before_target(self):
        for count in (0, -1, True, 1.5, '1', 1001):
            with self.subTest(count=count), self.assertRaises(core.BackupError):
                core.rehearse_retention(self.store, self.target, keep_latest=count)
        for protect in ('a' * 32, [True], ['../escape'], ['f' * 32], [self.snapshots[0]['snapshot']] * 2):
            with self.subTest(protect=protect), self.assertRaises(core.BackupError):
                core.rehearse_retention(self.store, self.target, keep_latest=1, protect=protect)
        self.assertFalse(self.target.exists())

    def test_empty_store_has_no_approved_policy(self):
        empty = self.root / 'empty-store'
        core.init(empty)
        with self.assertRaises(core.BackupError):
            core.rehearse_retention(empty, self.target, keep_latest=1)
        self.assertFalse(self.target.exists())

    def test_corrupt_retired_version_or_orphan_refuses_entire_plan(self):
        path = self.store / 'objects' / self.snapshots[0]['files']['save.bin']['sha256']
        path.write_bytes(b'broken-old')
        with self.assertRaises(core.BackupError):
            core.rehearse_retention(self.store, self.target, keep_latest=1)
        self.assertFalse(self.target.exists())
        path.write_bytes(b'version-0')
        orphan = self.store / 'objects' / ('f' * 64)
        orphan.write_bytes(b'not-matching-hash')
        with self.assertRaises(core.BackupError):
            core.retention_plan(self.store, keep_latest=1)

    def test_unreferenced_object_is_visible_but_never_deleted(self):
        digest = hashlib.sha256(b'orphan').hexdigest()
        (self.store / 'objects' / digest).write_bytes(b'orphan')
        before = self.stored_bytes()
        report = core.retention_plan(self.store, keep_latest=3)
        self.assertEqual(report['object_candidates_if_retired'], [{'sha256': digest, 'size': 6, 'reason': 'unreferenced'}])
        self.assertEqual(self.stored_bytes(), before)

    def test_pending_and_unexpected_entries_fail_closed(self):
        for relative in ('objects/.pending-test', 'snapshots/.pending-test', 'snapshots/unknown.json', 'objects/unknown'):
            path = self.store / relative
            path.write_bytes(b'incomplete')
            with self.subTest(path=relative), self.assertRaises(core.BackupError):
                core.retention_plan(self.store, keep_latest=1)
            path.unlink()  # Exact synthetic fixture only.

    def test_no_target_overwrite_or_overlap(self):
        self.target.mkdir()
        (self.target / 'sentinel').write_bytes(b'untouched')
        with self.assertRaises(core.BackupError):
            core.rehearse_retention(self.store, self.target, keep_latest=1)
        self.assertEqual((self.target / 'sentinel').read_bytes(), b'untouched')
        for target in (self.store / 'rehearsal', self.store, self.root):
            with self.subTest(target=target), self.assertRaises(core.BackupError):
                core.rehearse_retention(self.store, target, keep_latest=1)

    def test_plan_digest_changes_for_policy_or_catalog_content(self):
        first = core.retention_plan(self.store, keep_latest=1)
        self.assertNotEqual(first['plan_sha256'], core.retention_plan(self.store, keep_latest=2)['plan_sha256'])
        self.stamp(self.snapshots[0], '2026-09-01T00:00:00Z')
        self.assertNotEqual(first['plan_sha256'], core.retention_plan(self.store, keep_latest=1)['plan_sha256'])

    def test_store_change_after_preflight_cannot_produce_complete_rehearsal(self):
        original = core._digest
        changed = False
        def mutate(path):
            nonlocal changed
            result = original(path)
            if not changed:
                changed = True
                self.stamp(self.snapshots[0], '2026-09-01T00:00:00Z')
            return result
        with patch.object(core, '_digest', side_effect=mutate), self.assertRaises(core.BackupError):
            core.rehearse_retention(self.store, self.target, keep_latest=1)
        self.assertTrue(changed)
        self.assertFalse(self.target.exists())

    def test_cli_readonly_and_rehearsal_with_no_partial_failure_report(self):
        result = subprocess.run([sys.executable, '-m', 'saveharbor', 'retention', str(self.store), '--keep-latest', '1'], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['complete'])
        result = subprocess.run([sys.executable, '-m', 'saveharbor', 'retention', str(self.store), '--keep-latest', '1',
                                 '--rehearse', str(self.target)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['restore_rehearsal']['verified'])
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(['retention', str(self.store), '--keep-latest', '0'])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())['complete'])
        self.assertNotIn('object_candidates_if_retired', json.loads(output.getvalue()))

    def test_each_manifest_loaded_once_and_changed_catalog_is_not_restored(self):
        with patch.object(core, 'load_snapshot', wraps=core.load_snapshot) as loader:
            core.rehearse_retention(self.store, self.target, keep_latest=2)
        self.assertEqual(loader.call_count, 3)

    def test_copy_fault_returns_no_success_and_preserves_store_and_partial_target(self):
        before = self.stored_bytes()
        output = io.StringIO()
        with patch.object(core.os, 'fsync', side_effect=OSError('synthetic sync fault')), redirect_stdout(output):
            code = main(['retention', str(self.store), '--keep-latest', '1', '--rehearse', str(self.target)])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())['complete'])
        self.assertNotIn('restore_rehearsal', json.loads(output.getvalue()))
        self.assertTrue(self.target.exists())
        self.assertEqual(self.stored_bytes(), before)
        with self.assertRaises(core.BackupError):
            core.rehearse_retention(self.store, self.target, keep_latest=1)

    def test_change_during_actual_restore_is_not_verified_and_pinned_content_was_used(self):
        original = core._restore_manifest
        def mutate(root, manifest, target):
            copied = original(root, manifest, target)
            self.stamp(self.snapshots[-1], '2026-09-01T00:00:00Z')
            return copied
        with patch.object(core, '_restore_manifest', side_effect=mutate), self.assertRaises(core.BackupError):
            core.rehearse_retention(self.store, self.target, keep_latest=1)
        restored = self.target / self.snapshots[-1]['snapshot']
        self.assertEqual((restored / 'save.bin').read_bytes(), b'version-2')

    def test_written_destination_reaudit_rejects_post_copy_corruption(self):
        original = core._restore_manifest
        def corrupt(root, manifest, target):
            copied = original(root, manifest, target)
            (target / 'save.bin').write_bytes(b'bad')
            return copied
        with patch.object(core, '_restore_manifest', side_effect=corrupt), self.assertRaises(core.BackupError):
            core.rehearse_retention(self.store, self.target, keep_latest=1)

    def test_combined_inventory_limits_and_conflicting_object_size(self):
        # Real combined byte boundary via metadata injection, not 32 MiB fixture writes.
        info = core._regular
        def large(path):
            result = info(path)
            if path.parent.name == 'snapshots':
                from types import SimpleNamespace
                return SimpleNamespace(st_dev=result.st_dev, st_ino=result.st_ino,
                    st_size=32 * 1024 * 1024 + 1, st_mtime_ns=result.st_mtime_ns, st_ctime_ns=result.st_ctime_ns)
            return result
        with patch.object(core, '_regular', side_effect=large), self.assertRaises(core.BackupError):
            core.retention_plan(self.store, keep_latest=1)
        self.snapshots[0]['files']['共享.txt']['size'] = 99
        self.stamp(self.snapshots[0], self.snapshots[0]['created_at'])
        with self.assertRaises(core.BackupError):
            core.retention_plan(self.store, keep_latest=1)

    def test_version_metadata_agree(self):
        import saveharbor
        self.assertEqual(saveharbor.__version__, '0.2.0')

    def test_seeded_reference_set_oracle_for_shared_and_unique_objects(self):
        import random
        rng = random.Random(20261007)
        for index in range(8):
            (self.source / f'extra-{rng.randrange(3)}').write_bytes(bytes([rng.randrange(4)]))
            manifest = core.backup(self.source, self.store)
            self.stamp(manifest, f'2026-10-{index + 4:02d}T00:00:00Z')
        manifests = [core.load_snapshot(self.store, path.stem) for path in (self.store / 'snapshots').glob('*.json')]
        from datetime import datetime, timezone
        ordered = sorted(manifests, key=lambda m: (datetime.fromisoformat(m['created_at']).astimezone(timezone.utc), m['snapshot']), reverse=True)
        objects = {p.name for p in (self.store / 'objects').iterdir()}
        for _ in range(30):
            count = rng.randrange(1, 15)
            protect = rng.sample([m['snapshot'] for m in manifests], rng.randrange(4))
            keep = {m['snapshot'] for m in ordered[:count]} | set(protect)
            used = {e['sha256'] for m in manifests if m['snapshot'] in keep for e in m['files'].values()}
            report = core.retention_plan(self.store, keep_latest=count, protect=protect)
            self.assertEqual({m['snapshot'] for m in report['keep']}, keep)
            self.assertEqual({o['sha256'] for o in report['object_candidates_if_retired']}, objects - used)

    def test_unknown_root_entry_and_missing_object_refused(self):
        path = self.store / 'unexpected'
        path.mkdir()
        with self.assertRaises(core.BackupError):
            core.retention_plan(self.store, keep_latest=1)
        path.rmdir()  # Exact empty synthetic fixture.
        object_path = self.store / 'objects' / self.snapshots[0]['files']['save.bin']['sha256']
        object_path.unlink()
        with self.assertRaises(core.BackupError):
            core.rehearse_retention(self.store, self.target, keep_latest=1)
        self.assertFalse(self.target.exists())

    def test_linked_object_refused(self):
        original = self.store / 'objects' / self.snapshots[0]['files']['save.bin']['sha256']
        outside = self.root / 'outside'
        outside.write_bytes(original.read_bytes())
        probe = self.root / 'probe-link'
        try:
            probe.symlink_to(outside)
        except OSError:
            self.skipTest('Symlink creation requires Windows privileges')
        probe.unlink()
        original.unlink()
        original.symlink_to(outside)
        with self.assertRaises(core.BackupError):
            core.retention_plan(self.store, keep_latest=1)

    def test_later_restore_cannot_hide_corruption_of_an_earlier_destination(self):
        original = core._restore_manifest
        first = None
        def corrupt_earlier(root, manifest, target):
            nonlocal first
            result = original(root, manifest, target)
            if first is None:
                first = target
            else:
                (first / 'save.bin').write_bytes(b'later-corruption')
            return result
        with patch.object(core, '_restore_manifest', side_effect=corrupt_earlier), self.assertRaises(core.BackupError):
            core.rehearse_retention(self.store, self.target, keep_latest=2)

    def test_exact_plan_digest_binding_with_independent_raw_manifest_oracle(self):
        protect = [m['snapshot'] for m in self.snapshots[:2]]
        report = core.retention_plan(self.store, keep_latest=1, protect=protect)
        catalog = [{'snapshot': p.stem, 'manifest_sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                   for p in sorted((self.store / 'snapshots').iterdir())]
        objects = {p.name: len(p.read_bytes()) for p in (self.store / 'objects').iterdir()}
        binding = {'policy': {'keep_latest': 1, 'protected': sorted(protect)}, 'catalog': catalog, 'objects': objects}
        digest = hashlib.sha256(json.dumps(binding, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(report['plan_sha256'], digest)
        self.assertEqual(report['plan_sha256'], core.retention_plan(self.store, keep_latest=1, protect=list(reversed(protect)))['plan_sha256'])

    def test_maximum_snapshot_count_and_one_over(self):
        store = self.root / 'many-empty-snapshots'
        core.init(store)
        for index in range(1000):
            snapshot = f'{index:032x}'
            manifest = {'version': 1, 'snapshot': snapshot, 'created_at': '2026-10-07T00:00:00Z',
                        'files': {}, 'directories': [], 'excluded': []}
            (store / 'snapshots' / f'{snapshot}.json').write_text(json.dumps(manifest))
        report = core.retention_plan(store, keep_latest=1)
        self.assertEqual(len(report['retire_candidates']), 999)
        (store / 'snapshots' / f'{1000:032x}.json').write_text('{}')
        with self.assertRaises(core.BackupError):
            core.retention_plan(store, keep_latest=1)

    def test_utc_normalization_overflow_has_failure_without_target(self):
        self.stamp(self.snapshots[0], '0001-01-01T00:00:00+14:00')
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(['retention', str(self.store), '--keep-latest', '1', '--rehearse', str(self.target)])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())['complete'])
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
