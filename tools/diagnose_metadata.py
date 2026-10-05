"""Compare path-based and handle-based Windows metadata on synthetic files."""
import os
from pathlib import Path
import tempfile

def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    for index in range(1000):
        (root / str(index)).write_bytes(b"synthetic")
    before = {path: fingerprint(path.lstat()) for path in root.iterdir()}
    differences = []
    for path, initial in before.items():
        with path.open("rb") as stream:
            handle = fingerprint(os.fstat(stream.fileno()))
        if initial != handle:
            differences.append((path.name, initial, handle, fingerprint(path.lstat())))
    print("metadata_differences", len(differences))
    print("synthetic_examples", differences[:5])
