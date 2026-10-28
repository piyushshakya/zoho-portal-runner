"""Command line.

    python -m portal_runner [--inputs inputs.json] [--headed] [--slow-mo MS] [--fresh-login]
                            [--proxy URL] [--captcha-wait S] [--trace] [--break TARGET_KEY]
    python -m portal_runner offline page.html ...        # resolver on saved HTML, no network
    python -m portal_runner mutate in.html out.html --all # make a "front-end changed" copy
    python -m portal_runner serve [--workers 2]          # job API + UI on http://127.0.0.1:8000

Exit codes: 0 document captured, 1 run failed (see the evidence bundle), 2 bad .env or inputs.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from portal_runner.core import mutate, offline
from portal_runner.core.errors import ConfigError, ExitCode, InputError
from portal_runner.core.inputs import load_inputs
from portal_runner.core.proxy import parse_proxy
from portal_runner.portals import get_portal
from portal_runner.runner import CURRENT_RUN, LOG_FORMAT, Runner, RunOptions


def add_browser_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--headed", action="store_true", help="show the browser window")
    p.add_argument("--slow-mo", type=int, default=0, metavar="MS", help="pause before every browser action")
    p.add_argument("--proxy", help="route browser traffic through a proxy (default: $PROXY_URL)")
    p.add_argument(
        "--captcha-wait",
        type=int,
        default=180,
        metavar="S",
        help="with --headed: seconds to wait for you to solve a CAPTCHA (default 180)",
    )
    p.add_argument("--trace", action="store_true", help="record runs/<id>/trace-N.zip (contains passwords and cookies)")
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


def run_options(args: argparse.Namespace) -> RunOptions:
    proxy = args.proxy or os.getenv("PROXY_URL") or None
    if proxy:
        parse_proxy(proxy)  # fail early with a clear ConfigError
    return RunOptions(
        state=args.state,
        runs_dir=args.runs_dir,
        out_dir=args.out_dir,
        headed=args.headed,
        slow_mo_ms=args.slow_mo,
        fresh_login=getattr(args, "fresh_login", False),
        trace=args.trace,
        broken=frozenset(args.broken),
        proxy=proxy,
        captcha_wait_s=args.captcha_wait,
    )


def run(argv: Sequence[str]) -> int:
    p = argparse.ArgumentParser(
        prog="python -m portal_runner", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--inputs", type=Path, default=Path("inputs.json"), help="inputs file (default: inputs.json)")
    p.add_argument("--fresh-login", action="store_true", help="ignore the saved session")
    add_browser_args(p)
    args = p.parse_args(argv)
    try:
        inputs = load_inputs(args.inputs)
        portal = get_portal(inputs["portal"]).from_env(inputs)
        runner = Runner(portal, inputs, run_options(args))
    except (ConfigError, InputError) as e:
        print(f"error: {e}", file=sys.stderr)
        return ExitCode.BAD_CONFIG
    return asyncio.run(runner.run())


def serve(argv: Sequence[str]) -> int:
    import uvicorn  # only the service needs these

    from portal_runner.service.app import create_app, portal_job_runner
    from portal_runner.service.queue import JobQueue
    from portal_runner.service.store import JobStore

    p = argparse.ArgumentParser(prog="python -m portal_runner serve", description="Job API and UI.")
    p.add_argument("--host", default="127.0.0.1", help="interface to bind (default: this machine only)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--workers", type=int, default=2, help="jobs that may run at the same time")
    p.add_argument("--db", type=Path, default=Path("state/jobs.db"))
    add_browser_args(p)
    args = p.parse_args(argv)
    try:
        options = run_options(args)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return ExitCode.BAD_CONFIG
    queue = JobQueue(JobStore(args.db), portal_job_runner(options), workers=args.workers)
    print(f"Portal Runner service: http://{args.host}:{args.port}  (API docs: /docs)")
    uvicorn.run(
        create_app(queue, out_dir=options.out_dir), host=args.host, port=args.port, log_config=None, access_log=False
    )
    return ExitCode.OK


def _tag_run_id(record: logging.LogRecord) -> bool:
    record.run = CURRENT_RUN.get()
    return True


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv[:1] in (["offline"], ["mutate"], ["serve"]) else "run"
    if command == "serve":
        # Parallel jobs share one terminal: tag every line with its run id.
        logging.basicConfig(
            level=logging.INFO, format=LOG_FORMAT.replace("%(name)s", "[%(run)s] %(name)s"), datefmt="%H:%M:%S"
        )
        for handler in logging.getLogger().handlers:
            handler.addFilter(_tag_run_id)
        return serve(argv[1:])
    quiet = command in ("offline", "mutate")
    logging.basicConfig(level=logging.WARNING if quiet else logging.INFO, format=LOG_FORMAT, datefmt="%H:%M:%S")
    if command == "offline":
        return asyncio.run(offline.main(argv[1:], lambda name: get_portal(name).offline_targets))
    if command == "mutate":
        return mutate.main(argv[1:])
    return run(argv)
