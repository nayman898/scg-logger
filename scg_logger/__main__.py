import argparse
import json
import sqlite3
import sys


def main():
    parser = argparse.ArgumentParser(description="SCG Logger — read-only acquisition")
    sub = parser.add_subparsers(dest="command")
    worker = sub.add_parser("worker")
    worker.add_argument("--session", required=True)
    worker.add_argument("--port", default="")
    worker.add_argument("--identity", default="")
    worker.add_argument("--demo", action="store_true")
    worker.add_argument("--duration", type=float)
    worker.add_argument(
        "--resume",
        action="store_true",
        help="Internal supervisor recovery; same device/profile only",
    )
    export = sub.add_parser("export")
    export.add_argument("session")
    export.add_argument("csv")
    sub.add_parser("ports")
    args = parser.parse_args()
    if args.command == "worker":
        from .worker import main as work

        work(args)
    elif args.command == "export":
        from .store import export_csv

        export_csv(args.session, args.csv)
        print(json.dumps({"exported": args.csv}), flush=True)
    elif args.command == "ports":
        from .transport import available_ports

        print(json.dumps({"ports": available_ports()}), flush=True)
    else:
        from .ui import main as ui

        ui()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"SCG Logger: {exc}", file=sys.stderr)
        sys.exit(1)
