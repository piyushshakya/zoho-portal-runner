"""Command line.

    python -m portal_runner [--inputs inputs.json] [--headed] [--slow-mo MS] [--fresh-login]
                            [--trace] [--break TARGET_KEY]
    python -m portal_runner offline page.html ...        # resolver on saved HTML, no network
    python -m portal_runner mutate in.html out.html --all # make a "front-end changed" copy

Exit codes: 0 document captured, 1 run failed (see the evidence bundle), 2 bad .env or inputs.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from portal_runner.core import mutate, offline
from portal_runner.core.errors import ConfigError, ExitCode, InputError
from portal_runner.core.inputs import load_inputs
from portal_runner.portals import get_portal
from portal_runner.runner import LOG_FORMAT, Runner, RunOptions


def parse_run_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m portal_runner", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--inputs", type=Path, default=Path("inputs.json"), help="inputs file (default: inputs.json)")
    p.add_argument("--headed", action="store_true", help="show the browser window")
    p.add_argument("--slow-mo", type=int, default=0, metavar="MS", help="pause before every browser action")
    p.add_argument("--fresh-login", action="store_true", help="ignore the saved session")
    p.add_argument(
        "--trace",
        action="store_true",
        help="record runs/<id>/trace-N.zip (contains passwords and cookies: never share it)",
    )
    p.add_argument(
        "--break",
        dest="broken",
        action="append",
        default=[],
        metavar="TARGET_KEY",
        help="make one target unfindable on purpose, e.g. detail.pdf_print",
    )
    p.add_argument("--state", type=Path, help="session file (default: state/<portal>.json)")
    p.add_argument("--runs-dir", type=Path, default=Path("runs"))
    p.add_argument("--out-dir", type=Path, default=Path("output"))
    return p.parse_args(argv)


def run(argv: Sequence[str]) -> int:
    args = parse_run_args(argv)
    try:
        inputs = load_inputs(args.inputs)
        portal = get_portal(inputs["portal"]).from_env(inputs)
    except (ConfigError, InputError) as e:
        print(f"error: {e}", file=sys.stderr)
        return ExitCode.BAD_CONFIG
    options = RunOptions(
        state=args.state,
        runs_dir=args.runs_dir,
        out_dir=args.out_dir,
        headed=args.headed,
        slow_mo_ms=args.slow_mo,
        fresh_login=args.fresh_login,
        trace=args.trace,
        broken=frozenset(args.broken),
    )
    return asyncio.run(Runner(portal, inputs, options).run())


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    tool = argv[0] if argv[:1] in (["offline"], ["mutate"]) else None
    logging.basicConfig(level=logging.WARNING if tool else logging.INFO, format=LOG_FORMAT, datefmt="%H:%M:%S")
    if tool == "offline":
        return asyncio.run(offline.main(argv[1:], lambda name: get_portal(name).offline_targets))
    if tool == "mutate":
        return mutate.main(argv[1:])
    return run(argv)
