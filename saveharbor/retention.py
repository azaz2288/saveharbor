"""Non-executable retention candidates and actual pinned-manifest restore drills."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from . import core


def _inventory(root):
    core._store(root)
    allowed = {'format.json', 'objects', 'snapshots'}
    for child in root.iterdir():
        if child.name not in allowed:
            raise core.BackupError('Unexpected store entry; retention review requires a quiescent store')
    records = {'format.json': core._fingerprint(core._regular(root / 'format.json'))}
    records['.'] = core._fingerprint(root.stat())
    for directory, expression, limit in (('snapshots', r'[a-f0-9]{32}\.json', 1000),
                                          ('objects', r'[a-f0-9]{64}', 100000)):
        records[directory] = core._fingerprint((root / directory).stat())
        count = 0
        for child in (root / directory).iterdir():
            count += 1
            if count > limit or not re.fullmatch(expression, child.name):
                raise core.BackupError('Unexpected/pending entry or retention inventory limit exceeded')
            records[f'{directory}/{child.name}'] = core._fingerprint(core._regular(child))
    if sum(v[2] for k, v in records.items() if k.startswith('snapshots/')) > 32 * 1024 * 1024:
        raise core.BackupError('Retention catalog exceeds 32 MiB combined manifest limit')
    return records


def _policy(keep_latest, protect):
    if type(keep_latest) is not int or not 1 <= keep_latest <= 1000:
        raise core.BackupError('keep_latest must be an integer from 1 to 1000')
    if not isinstance(protect, (list, tuple)) or len(protect) > 1000:
        raise core.BackupError('protect must be a bounded explicit snapshot list')
    if any(not isinstance(item, str) or not re.fullmatch(r'[a-f0-9]{32}', item) for item in protect):
        raise core.BackupError('Invalid protected snapshot ID')
    if len(set(protect)) != len(protect):
        raise core.BackupError('Duplicate protected snapshot ID')
    return sorted(protect)


def _unchanged(root, inventory):
    if _inventory(root) != inventory:
        raise core.BackupError('Store changed during retention review/rehearsal; no complete result')


def _prepare(root, keep_latest, protect):
    protected = _policy(keep_latest, protect)
    core._directory(root)
    root = root.resolve()
    inventory = _inventory(root)
    manifests, catalog, entries = [], [], 0
    for relative in sorted(k for k in inventory if k.startswith('snapshots/')):
        snapshot = Path(relative).stem
        manifest = core.load_snapshot(root, snapshot)
        entries += len(manifest['files']) + len(manifest['directories'])
        if entries > 100000:
            raise core.BackupError('Retention catalog exceeds 100,000 combined entries')
        manifests.append(manifest)
        catalog.append({'snapshot': snapshot, 'manifest_sha256': core._digest(root / relative)[0]})
    if not manifests:
        raise core.BackupError('Retention needs at least one complete snapshot')
    ids = {m['snapshot'] for m in manifests}
    if set(protected) - ids:
        raise core.BackupError('Protected snapshot is absent')
    try:
        manifests.sort(key=lambda m: (datetime.fromisoformat(m['created_at']).astimezone(timezone.utc),
                                     m['snapshot']), reverse=True)
    except (ValueError, OverflowError) as exc:
        raise core.BackupError('Creation timestamp cannot be normalized to UTC') from exc
    kept = {m['snapshot'] for m in manifests[:keep_latest]} | set(protected)
    referenced, retained = {}, set()
    for manifest in manifests:
        for entry in manifest['files'].values():
            digest, size = entry['sha256'], entry['size']
            if digest in referenced and referenced[digest] != size:
                raise core.BackupError('Conflicting size declarations for one content object')
            referenced[digest] = size
            if manifest['snapshot'] in kept:
                retained.add(digest)
    objects = {}
    for relative in sorted(k for k in inventory if k.startswith('objects/')):
        digest = Path(relative).name
        actual, size = core._digest(root / relative)
        if actual != digest or (digest in referenced and referenced[digest] != size):
            raise core.BackupError('Content digest or size mismatch')
        objects[digest] = size
    if referenced.keys() - objects.keys():
        raise core.BackupError('Referenced content object is absent')
    _unchanged(root, inventory)
    def summary(manifest):
        return {'snapshot': manifest['snapshot'], 'created_at': manifest['created_at'],
                'files': len(manifest['files']), 'bytes': sum(e['size'] for e in manifest['files'].values())}
    candidates = [{'sha256': digest, 'size': size,
                   'reason': 'retired-only' if digest in referenced else 'unreferenced'}
                  for digest, size in objects.items() if digest not in retained]
    binding = {'policy': {'keep_latest': keep_latest, 'protected': protected}, 'catalog': catalog,
               'objects': objects}
    digest = hashlib.sha256(json.dumps(binding, sort_keys=True, ensure_ascii=True,
                                       separators=(',', ':')).encode('utf-8')).hexdigest()
    report = {'version': 1, 'complete': True, 'scope': 'retention-review-no-deletion',
              'executable': False, 'store_modified': False, 'plan_sha256': digest,
              'policy': binding['policy'], 'keep': [summary(m) for m in manifests if m['snapshot'] in kept],
              'retire_candidates': [summary(m) for m in manifests if m['snapshot'] not in kept],
              'object_candidates_if_retired': candidates,
              'candidate_content_bytes': sum(c['size'] for c in candidates),
              'restore_rehearsal': {'verified': False, 'snapshots': []}}
    return root, inventory, [m for m in manifests if m['snapshot'] in kept], report


def plan(root, *, keep_latest, protect=()):
    try:
        return _prepare(root, keep_latest, protect)[3]
    except OSError as exc:
        raise core.BackupError('Retention review incomplete; store was not modified') from exc


def _audit(target, manifest):
    files, directories = core._tree(target, ())
    if set(files) != set(manifest['files']) or directories != sorted(manifest['directories']):
        raise core.BackupError('Rehearsal destination structure mismatch')
    for name, entry in manifest['files'].items():
        if core._digest(target / name) != (entry['sha256'], entry['size']):
            raise core.BackupError('Rehearsal destination content mismatch')
    if core._tree(target, ()) != (files, directories):
        raise core.BackupError('Rehearsal destination changed during audit')


def rehearse(root, target, *, keep_latest, protect=()):
    try:
        core._directory(root)
        root = root.resolve()
        target = Path(target)
        destination = target.resolve(strict=False)
        if destination == root or destination.is_relative_to(root) or root.is_relative_to(destination):
            raise core.BackupError('Rehearsal target and store must not overlap')
        if target.exists() or target.is_symlink() or target.is_junction():
            raise core.BackupError('Rehearsal target must be a NEW directory')
        root, inventory, manifests, report = _prepare(root, keep_latest, protect)
        target.mkdir(parents=True, exist_ok=False)
        for manifest in manifests:
            restored = target / manifest['snapshot']
            core._restore_manifest(root, manifest, restored)
        # Audit ALL copies after the last write, not only each as it completes.
        if {p.name for p in target.iterdir()} != {m['snapshot'] for m in manifests}:
            raise core.BackupError('Rehearsal root has unexpected entries')
        for manifest in manifests:
            _audit(target / manifest['snapshot'], manifest)
        _unchanged(root, inventory)
        report['restore_rehearsal'] = {'verified': True, 'snapshots': [m['snapshot'] for m in manifests]}
        return report
    except OSError as exc:
        raise core.BackupError('Retention rehearsal incomplete; NEW target may be partial, store not modified') from exc
