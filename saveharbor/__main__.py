import argparse
import json
from pathlib import Path
import sys
from .core import BackupError, backup, diff, init, list_snapshots, restore, verify


def main(argv=None):
    parser = argparse.ArgumentParser(description="Versioned local backups; restore only to a NEW directory")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init").add_argument("store", type=Path)
    sub.add_parser("list").add_argument("store", type=Path)
    command = sub.add_parser("backup")
    command.add_argument("source", type=Path)
    command.add_argument("store", type=Path)
    command.add_argument("--exclude", action="append", default=[])
    for name in ("verify", "restore"):
        command = sub.add_parser(name)
        command.add_argument("store", type=Path)
        command.add_argument("snapshot")
        if name == "restore":
            command.add_argument("target", type=Path)
    command = sub.add_parser("diff")
    command.add_argument("store", type=Path)
    command.add_argument("before")
    command.add_argument("after")
    args = parser.parse_args(argv)
    try:
        if args.action == "init":
            init(args.store)
            result = {"initialized": True}
        elif args.action == "backup":
            result = backup(args.source, args.store, exclude=args.exclude)
        elif args.action == "verify":
            result = verify(args.store, args.snapshot)
        elif args.action == "list":
            result = list_snapshots(args.store)
        elif args.action == "restore":
            result = restore(args.store, args.snapshot, args.target)
        else:
            result = diff(args.store, args.before, args.after)
    except (BackupError, OSError, UnicodeError) as exc:
        print(json.dumps({"complete": False, "error": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
