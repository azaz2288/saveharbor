import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from saveharbor.core import backup, diff, init, restore, verify

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    source, store = root / "saves", root / "store"
    source.mkdir()
    (source / "character.json").write_text('{"level":1}', encoding="utf-8")
    init(store)
    first = backup(source, store)["snapshot"]
    (source / "character.json").write_text('{"level":10}', encoding="utf-8")
    second = backup(source, store)["snapshot"]
    assert diff(store, first, second)["changed"] == ["character.json"]
    assert verify(store, first)["verified"]
    restore(store, first, root / "recovered")
    recovered = json.loads((root / "recovered/character.json").read_text())
    assert recovered["level"] == 1
    print(json.dumps({"versions": 2, "changed": ["character.json"], "restored_old_level": recovered["level"]}))
