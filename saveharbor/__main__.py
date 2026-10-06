import argparse
import json
from pathlib import Path
import sys
from .core import BackupError, backup, diff, init, list_snapshots, restore, verify, retention_plan, rehearse_retention


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
    command = sub.add_parser('retention', help='Read-only candidates; optionally restore kept versions to a NEW rehearsal directory')
    command.add_argument('store', type=Path)
    command.add_argument('--keep-latest', required=True, type=int)
    command.add_argument('--protect', action='append', default=[])
    command.add_argument('--rehearse', type=Path)
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
        elif args.action == 'retention':
            options = dict(keep_latest=args.keep_latest, protect=args.protect)
            result = (rehearse_retention(args.store, args.rehearse, **options) if args.rehearse
                      else retention_plan(args.store, **options))
        else:
            result = diff(args.store, args.before, args.after)
    except (BackupError, OSError, UnicodeError) as exc:
        print(json.dumps({"complete": False, "error": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
