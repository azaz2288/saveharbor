from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import uuid


class BackupError(Exception):
    """Unsafe input, damaged backup or incomplete operation."""


def _manifest_bytes(manifest):
    content = json.dumps(manifest, ensure_ascii=True, sort_keys=True, allow_nan=False).encode("utf-8")
    if len(content) > 8 * 1024 * 1024:
        raise BackupError("Snapshot manifest exceeds readable 8 MiB limit")
    return content


def _regular(path: Path):
    info = path.lstat()
    if path.is_symlink() or path.is_junction() or not stat.S_ISREG(info.st_mode):
        raise BackupError("Expected a regular non-link file")
    return info


def _safe_path(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise BackupError("Unsafe backup path")
    parts = value.split("/")
    if PurePosixPath(value).is_absolute() or any(
        part in ("", ".", "..") or part.endswith((".", " ")) or
        any(ord(char) < 32 or char in ':<>"|?*' for char in part) or
        re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
        for part in parts
    ):
        raise BackupError("Backup path is not portable or escapes its root")
    return value


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BackupError("Duplicate JSON keys")
        result[key] = value
    return result


def _read_json(path):
    _regular(path)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise BackupError("Manifest exceeds size limit")
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise BackupError("Invalid backup JSON") from exc


def _directory(path):
    if path.is_symlink() or path.is_junction() or not path.is_dir():
        raise BackupError("Expected a real directory, not a link")


def _store(root: Path):
    _directory(root)
    marker = _read_json(root / "format.json")
    if not isinstance(marker, dict) or type(marker.get("version")) is not int or marker != {"version": 1, "type": "saveharbor"}:
        raise BackupError("Unsupported backup store")
    for name in ("objects", "snapshots"):
        _directory(root / name)


def init(root: Path):
    """Create a new store, refusing ANY existing path."""
    try:
        root.mkdir(parents=True, exist_ok=False)
        (root / "objects").mkdir()
        (root / "snapshots").mkdir()
        with (root / "format.json").open("x", encoding="utf-8") as stream:
            json.dump({"version": 1, "type": "saveharbor"}, stream)
    except OSError as exc:
        raise BackupError("Could not initialize a new store") from exc


def _tree(source, excluded):
    files, directories, folded = {}, [], set()
    def visit(path):
        _directory(path)
        for child in sorted(path.iterdir()):
            relative = child.relative_to(source).as_posix()
            if any(relative == prefix or relative.startswith(prefix + "/") for prefix in excluded):
                continue
            _safe_path(relative)
            if relative.casefold() in folded:
                raise BackupError("Case-insensitive path collision")
            folded.add(relative.casefold())
            if child.is_symlink() or child.is_junction():
                raise BackupError("Source contains a symlink or junction")
            if child.is_dir():
                directories.append(relative)
                visit(child)
            else:
                files[relative] = _fingerprint(_regular(child))
            if len(files) + len(directories) > 100000:
                raise BackupError("Source exceeds 100,000 entries")
    visit(source)
    return files, sorted(directories)


def _digest(path):
    _regular(path)
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def backup(source: Path, root: Path, *, exclude=()):
    """Publish a manifest only after all objects and source stability checks pass."""
    _directory(source)
    _directory(root)
    source, root = source.resolve(), root.resolve()
    if source == root or source.is_relative_to(root) or root.is_relative_to(source):
        raise BackupError("Source and store must not overlap")
    excluded = tuple(_safe_path(value) for value in exclude)
    try:
        _store(root)
        before, directories = _tree(source, excluded)
        entries = {}
        for name, fingerprint in before.items():
            path = source / name
            temporary = None
            try:
                with tempfile.NamedTemporaryFile("wb", dir=root / "objects", prefix=".pending-", delete=False) as output:
                    temporary = Path(output.name)
                    hasher, size = hashlib.sha256(), 0
                    with path.open("rb") as stream:
                        handle_before = _fingerprint(os.fstat(stream.fileno()))
                        # Windows path stat and handle fstat can expose different
                        # st_ctime semantics. Compare identity/size/mtime across
                        # APIs, full metadata within the SAME API before/after.
                        if handle_before[:4] != fingerprint[:4]:
                            raise BackupError("Source changed before reading")
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            hasher.update(chunk)
                            output.write(chunk)
                            size += len(chunk)
                        if _fingerprint(os.fstat(stream.fileno())) != handle_before:
                            raise BackupError("Source changed during reading")
                    output.flush()
                    digest = hasher.hexdigest()
                    object_path = root / "objects" / digest
                    already_present = object_path.exists() or object_path.is_symlink()
                    if already_present:
                        if _digest(object_path) != (digest, size):
                            raise BackupError("Existing content object is damaged")
                    else:
                        os.fsync(output.fileno())
                if not already_present:
                    try:
                        os.link(temporary, object_path)
                    except FileExistsError:
                        if _digest(object_path) != (digest, size):
                            raise BackupError("Existing content object is damaged")
                entries[name] = {"sha256": digest, "size": size}
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        if _tree(source, excluded) != (before, directories):
            raise BackupError("Source tree changed while backing up")
        snapshot = uuid.uuid4().hex
        manifest = {"version": 1, "snapshot": snapshot, "created_at": datetime.now(timezone.utc).isoformat(), "files": entries,
                    "directories": directories, "excluded": list(excluded)}
        content = _manifest_bytes(manifest)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=root / "snapshots", prefix=".pending-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, root / "snapshots" / f"{snapshot}.json")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return manifest
    except OSError as exc:
        raise BackupError("Backup I/O failed; no complete snapshot should be assumed") from exc


def load_snapshot(root: Path, snapshot: str):
    if not isinstance(snapshot, str) or not re.fullmatch(r"[a-f0-9]{32}", snapshot):
        raise BackupError("Invalid snapshot ID")
    _store(root)
    value = _read_json(root / "snapshots" / f"{snapshot}.json")
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1 or value.get("snapshot") != snapshot:
        raise BackupError("Invalid snapshot manifest")
    if not isinstance(value.get("files"), dict) or not isinstance(value.get("directories"), list):
        raise BackupError("Invalid snapshot entries")
    files, directories = value["files"], value["directories"]
    created_at = value.get("created_at")
    try:
        created = datetime.fromisoformat(created_at)
    except (TypeError, ValueError) as exc:
        raise BackupError("Snapshot needs a timezone-aware creation timestamp") from exc
    if created.tzinfo is None:
        raise BackupError("Snapshot creation timestamp needs a timezone")
    if len(files) + len(directories) > 100000 or any(not isinstance(name, str) for name in directories):
        raise BackupError("Invalid directory list")
    names = list(files) + directories
    if len({name.casefold() for name in names}) != len(names):
        raise BackupError("Duplicate or case-colliding backup path")
    for name in names:
        _safe_path(name)
        parent = PurePosixPath(name).parent
        while str(parent) != ".":
            if str(parent) not in directories:
                raise BackupError("Parent directory missing or occupied by a file")
            parent = parent.parent
    for entry in files.values():
        if not isinstance(entry, dict) or type(entry.get("size")) is not int or entry["size"] < 0:
            raise BackupError("Invalid file metadata")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise BackupError("Invalid content digest")
    return value


def verify(root: Path, snapshot: str):
    try:
        manifest = load_snapshot(root, snapshot)
        for entry in manifest["files"].values():
            if _digest(root / "objects" / entry["sha256"]) != (entry["sha256"], entry["size"]):
                raise BackupError("Content digest or size mismatch")
        return {"version": 1, "verified": True, "snapshot": snapshot, "files": len(manifest["files"])}
    except OSError as exc:
        raise BackupError("Snapshot object is missing or inaccessible") from exc


def restore(root: Path, snapshot: str, target: Path):
    """Verify first, then create an exclusive NEW destination. Never overwrite."""
    try:
        _directory(root)
        root = root.resolve()
        destination = target.resolve(strict=False)
        if destination == root or destination.is_relative_to(root) or root.is_relative_to(destination):
            raise BackupError("Restore destination and store must not overlap")
        verify(root, snapshot)
        manifest = load_snapshot(root, snapshot)
        target.mkdir(parents=True, exist_ok=False)
        for name in sorted(manifest["directories"], key=lambda p: (p.count("/"), p)):
            (target / name).mkdir(exist_ok=False)
        for name, entry in manifest["files"].items():
            source = root / "objects" / entry["sha256"]
            _regular(source)
            hasher, size = hashlib.sha256(), 0
            with source.open("rb") as stream, (target / name).open("xb") as output:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    output.write(chunk)
                    hasher.update(chunk)
                    size += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            if (hasher.hexdigest(), size) != (entry["sha256"], entry["size"]):
                raise BackupError("Content changed during restore; destination is incomplete")
        return {"version": 1, "restored": True, "snapshot": snapshot, "files": len(manifest["files"])}
    except OSError as exc:
        raise BackupError("Restore failed; new destination may be incomplete, existing destinations are not overwritten") from exc


def diff(root: Path, before: str, after: str):
    try:
        old, new = load_snapshot(root, before), load_snapshot(root, after)
        old_files, new_files = old["files"], new["files"]
        return {"version": 1, "added": sorted(new_files.keys() - old_files.keys()),
                "removed": sorted(old_files.keys() - new_files.keys()),
                "changed": sorted(name for name in old_files.keys() & new_files.keys() if old_files[name] != new_files[name]),
                "directories_added": sorted(set(new["directories"]) - set(old["directories"])),
                "directories_removed": sorted(set(old["directories"]) - set(new["directories"]))}
    except OSError as exc:
        raise BackupError("Could not read snapshots") from exc


def list_snapshots(root: Path):
    try:
        _store(root)
        result = []
        for path in sorted((root / "snapshots").glob("*.json")):
            manifest = load_snapshot(root, path.stem)
            result.append({"snapshot": path.stem, "created_at": manifest.get("created_at"),
                           "files": len(manifest["files"]),
                           "bytes": sum(entry["size"] for entry in manifest["files"].values())})
        return {"version": 1, "snapshots": sorted(result, key=lambda item: (item["created_at"] or "", item["snapshot"]))}
    except OSError as exc:
        raise BackupError("Could not list snapshots") from exc
