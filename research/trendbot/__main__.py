"""One command for everything: ``python trendbot.pyz <command> ...`` (the single-file build),
or ``python -m research.trendbot <command> ...`` from the source tree.

Commands::

    bot        run / status / flatten a bot            (live_bot)
    dashboard  the web dashboard                       (dashboard)
    jobs       collect / retrain / traders / verify / lab   (retrain)
    traders    refresh / list the smart-money traders  (traders)
    dex        the DEX memecoin scanner                (dex_scan)
    lab        the strategy lab's research, now        (lab)
    fetch      download candles                        (fetch_data)
"""

from __future__ import annotations

import runpy
import sys

from .launch import COMMANDS


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    names = {v: k for k, v in COMMANDS.items()}
    if (
        not args
        or args[0] in ("-h", "--help", "help")
        or (args[0] not in names and args[0] not in COMMANDS)
    ):
        print(__doc__)
        return 0 if args and args[0] in ("-h", "--help", "help") else 2
    module = names.get(args[0], args[0])
    sys.argv = [f"trendbot {args[0]}", *args[1:]]
    try:
        runpy.run_module(f"research.trendbot.{module}", run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        if isinstance(exc.code, int) or exc.code is None:
            return exc.code or 0
        print(exc.code, file=sys.stderr)  # a message, e.g. a missing settings file
        return 1
    return 0


def cli() -> None:
    """The single-file entry point: exits with main()'s status."""
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
