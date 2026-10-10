"""CLI:  python -m dashboard [--port 8765] [--root PATH] [--open]

Serves a read-only dashboard over the screener's run history on http://127.0.0.1:<port>.
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from .data import Store
from .server import serve

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8765  # clear of the usual dev-server ports (8000, 8080, 5173)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dashboard", description=__doc__)
    ap.add_argument("--root", default=str(ROOT), help="screener checkout holding data/history and config.toml")
    ap.add_argument("--host", default="127.0.0.1", help="bind address; anything but loopback needs --unsafe-bind")
    ap.add_argument("--unsafe-bind", action="store_true",
                    help="allow a non-loopback --host: everyone who can reach it can read your data (no login)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                    help="also accept this Host header, e.g. the machine's Tailscale name (repeatable)")
    ap.add_argument("--base-path", default="", help="serve under a path prefix, e.g. /screener, behind a reverse proxy")
    ap.add_argument("--open", action="store_true", help="open the dashboard in the default browser")
    ap.add_argument("--verbose", action="store_true", help="log every request")
    ap.add_argument("--read-only", action="store_true", help="disable config editing entirely")
    ap.add_argument("--allow-remote-edit", action="store_true",
                    help="accept config edits through a proxy / from --allow-host names too: anyone who can reach the "
                         "dashboard can then change config.toml (default: only from this machine)")
    args = ap.parse_args(argv)

    store = Store(Path(args.root))
    try:
        httpd = serve(store, args.host, args.port, quiet=not args.verbose, allowed_hosts=tuple(args.allow_host),
                      base_path=args.base_path, read_only=args.read_only, allow_remote_edit=args.allow_remote_edit,
                      unsafe_bind=args.unsafe_bind)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Cannot bind {args.host}:{args.port} ({exc}). Is it already running? Try --port.", file=sys.stderr)
        return 1
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{args.port}{args.base_path.rstrip('/')}/"
    runs = store.run_dates()
    print(f"Tripwire dashboard on {url}  ({len(runs)} run(s) in {store.history_dir}; Ctrl+C to stop)", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
