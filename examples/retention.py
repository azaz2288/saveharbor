"""Real restores of retained synthetic versions; no deletion and no user files."""
import json
from pathlib import Path
import sys
import tempfile

if not sys.flags.isolated:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from saveharbor.core import backup, init, rehearse_retention


def main():
    with tempfile.TemporaryDirectory(prefix='saveharbor-retention-') as directory:
        root = Path(directory)
        source, store = root / 'source', root / 'store'
        source.mkdir()
        init(store)
        versions = []
        (source / 'shared').write_bytes(b'same-content')
        for level in range(3):
            (source / 'character.json').write_text(json.dumps({'level': level}), encoding='utf-8')
            versions.append(backup(source, store)['snapshot'])
        before = {p.relative_to(store).as_posix(): p.read_bytes() for p in store.rglob('*') if p.is_file()}
        report = rehearse_retention(store, root / 'restores', keep_latest=1, protect=[versions[0]])
        assert report['restore_rehearsal']['verified']
        assert len(report['retire_candidates']) == 1
        for index in (0, 2):
            value = json.loads((root / 'restores' / versions[index] / 'character.json').read_text())
            assert value['level'] == index
        after = {p.relative_to(store).as_posix(): p.read_bytes() for p in store.rglob('*') if p.is_file()}
        assert before == after
        print(json.dumps({'synthetic_only': True, 'kept_restored': 2, 'retire_candidates': 1,
                          'store_unchanged': True, 'deletion_executable': report['executable']}))


if __name__ == '__main__':
    main()
