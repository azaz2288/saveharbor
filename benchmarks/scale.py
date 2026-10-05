"""Measure synthetic versioning and recovery; never opens user directories."""
import json
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from saveharbor.core import backup, init, restore, verify

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    source, store = root / "synthetic", root / "store"
    source.mkdir()
    for index in range(1000):
        (source / f"file-{index:04d}.bin").write_bytes(index.to_bytes(4, "big") + b"x" * 1020)
    init(store)
    tracemalloc.start()
    timings = {}
    started = time.perf_counter()
    first = backup(source, store)["snapshot"]
    timings["first_backup"] = round(time.perf_counter() - started, 3)
    started = time.perf_counter()
    backup(source, store)
    timings["deduplicated_backup"] = round(time.perf_counter() - started, 3)
    started = time.perf_counter()
    verify(store, first)
    timings["verify"] = round(time.perf_counter() - started, 3)
    started = time.perf_counter()
    restore(store, first, root / "restored")
    timings["restore"] = round(time.perf_counter() - started, 3)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(list((store / "objects").iterdir())) == 1000
    assert len(list((root / "restored").iterdir())) == 1000
    assert (root / "restored/file-0999.bin").read_bytes() == (source / "file-0999.bin").read_bytes()
    print(json.dumps({"files":1000, "source_bytes":1024000, "versions":2,
                      "unique_objects":1000, "seconds":timings, "python_peak_bytes":peak,
                      "note":"One synthetic local run, Python allocations not process RSS; not a production guarantee"}))
