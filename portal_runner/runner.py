"""Run one portal end to end: session -> login if needed -> flow -> manifest (+ evidence on failure)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, ViewportSize, async_playwright

from portal_runner.core.errors import ExitCode
from portal_runner.core.evidence import write_bundle
from portal_runner.core.popups import PopupGuard
from portal_runner.core.record import RunRecord
from portal_runner.core.redact import RedactingFilter, Redactor
from portal_runner.core.resolver import Resolver
from portal_runner.core.session import SessionStore, Verdict, probe
from portal_runner.portals.base import FlowContext, Portal

log = logging.getLogger(__name__)

RESOLVE_TIMEOUT_S = 15
VIEWPORT: ViewportSize = {"width": 1440, "height": 900}
LOG_FORMAT = "%(asctime)s %(levelname)-5s %(name)s %(message)s"


@dataclass(frozen=True)
class RunOptions:
    state: Path | None = None  # default: state/<portal>.json
    runs_dir: Path = Path("runs")
    out_dir: Path = Path("output")
    headed: bool = False
    slow_mo_ms: int = 0
    fresh_login: bool = False
    trace: bool = False
    broken: frozenset[str] = frozenset()


class Runner:
    def __init__(self, portal: Portal, inputs: dict[str, Any], options: RunOptions) -> None:
        self.portal = portal
        self.options = options
        self.redactor = Redactor(portal.secrets)
        self.record = RunRecord(options.runs_dir, portal.name, inputs, self.redactor)
        state = options.state or Path("state") / f"{portal.name}.json"
        self.store = SessionStore(state, portal.session_rules.cookie_domain)
        self.guard = PopupGuard(portal.popups)
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.resolver: Resolver | None = None
        self._traces = 0

    async def run(self) -> ExitCode:
        with self._run_log():
            log.info("run %s: %s", self.record.id, self.portal.describe())
            if self.options.broken:
                log.warning("--break: deliberately unfindable: %s", ", ".join(sorted(self.options.broken)))
                self.record.manifest["broken_on_purpose"] = sorted(self.options.broken)
            if self.options.fresh_login:
                self.store.discard("--fresh-login")
            async with async_playwright() as pw:
                self.browser = await pw.chromium.launch(
                    headless=not self.options.headed, slow_mo=self.options.slow_mo_ms
                )
                try:
                    return await self._run()
                finally:
                    await self._close_context()
                    await self.browser.close()

    async def _run(self) -> ExitCode:
        try:
            verdict = await self.record.step("session.check", self._check_session)
            if verdict.alive:
                self.record.visit("dashboard", self._page.url)
            else:
                self.store.discard(verdict.reason)
                await self._open_context()
                await self.record.step("login", self._login, attempts=2, reset=self._clear_cookies)
                await self.store.save(self._context)
            document = await self.portal.run(self._flow_context())
            await self.store.save(self._context)  # keep refreshed cookies for next time
        except Exception as e:
            return await self._fail(e)
        manifest = self._finish("success", document=str(document))
        reused = "reused" if self.record.manifest["session"]["reused"] else "fresh login"
        print(f"\nOK  {document}  ({self.record.elapsed_ms / 1000:.1f}s, session {reused})\n    manifest: {manifest}")
        return ExitCode.OK

    async def _check_session(self) -> Verdict:
        verdict = self.store.preflight()
        if verdict.alive:
            await self._open_context(storage_state=self.store.path)
            verdict = await probe(self._page, self.portal.session_rules, self._resolver)
        self.record.manifest["session"] = {"reused": verdict.alive, "reason": verdict.reason}
        log.info("session: %s (%s)", "REUSED" if verdict.alive else "DEAD", verdict.reason)
        return verdict

    async def _login(self) -> None:
        await self.portal.login(self._flow_context())

    async def _clear_cookies(self) -> None:
        await self._context.clear_cookies()

    async def _fail(self, error: Exception) -> ExitCode:
        failed = self.record.last_failed_stage()
        stage = failed["key"] if failed else None
        bundle = await write_bundle(
            self.record.dir,
            self.page,
            stage=stage,
            error=error,
            redactor=self.redactor,
            console=self.record.console,
            attempts=failed.get("attempt", 1) if failed else 1,
        )
        manifest = self._finish("failed", failed_stage=stage, evidence=str(bundle), error=self.record.error_text(error))
        first_line = self.redactor.text(str(error)).splitlines()[0] if str(error) else type(error).__name__
        print(f"\nFAILED in stage {stage}: {first_line}\n    evidence: {bundle}\n    manifest: {manifest}")
        return ExitCode.FAILED

    def _finish(self, outcome: str, **extra: Any) -> Path:
        self.record.manifest["popups_dismissed"] = self.guard.dismissed
        self.record.manifest["resolver"] = self.resolver.history if self.resolver else []
        return self.record.finish(outcome, **extra)

    # -- browser lifecycle ---------------------------------------------------
    async def _open_context(self, storage_state: Path | None = None) -> None:
        await self._close_context()
        if self.browser is None:
            raise RuntimeError("browser not started")
        self.context = await self.browser.new_context(
            storage_state=str(storage_state) if storage_state else None,
            accept_downloads=True,
            viewport=VIEWPORT,
            # Headless Chromium announces itself as "HeadlessChrome"; look like a regular browser.
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{self.browser.version} Safari/537.36"
            ),
        )
        if self.options.trace:
            await self.context.tracing.start(screenshots=True, snapshots=True)
        self.page = await self.context.new_page()
        self.page.on("console", lambda m: self.record.console.append(f"[{m.type}] {m.text}"))
        self.page.on("pageerror", lambda e: self.record.console.append(f"[pageerror] {e}"))
        await self.guard.install(self.page)
        self.resolver = Resolver(self.page, default_timeout=RESOLVE_TIMEOUT_S, broken=self.options.broken)

    async def _close_context(self) -> None:
        if self.context is None:
            return
        if self.options.trace:
            self._traces += 1
            path = self.record.dir / f"trace-{self._traces}.zip"
            await self.context.tracing.stop(path=str(path))
            log.info("trace saved: %s (view: playwright show-trace %s)", path, path)
        await self.context.close()
        self.context = self.page = None

    def _flow_context(self) -> FlowContext:
        return FlowContext(self._page, self._resolver, self.record, self.guard, self.redactor, self.options.out_dir)

    @property
    def _page(self) -> Page:
        if self.page is None:
            raise RuntimeError("no browser page open")
        return self.page

    @property
    def _context(self) -> BrowserContext:
        if self.context is None:
            raise RuntimeError("no browser context open")
        return self.context

    @property
    def _resolver(self) -> Resolver:
        if self.resolver is None:
            raise RuntimeError("no resolver: open a browser context first")
        return self.resolver

    @contextmanager
    def _run_log(self) -> Iterator[None]:
        """Write runs/<id>/run.log and redact every log line for the duration of the run."""
        root = logging.getLogger()
        file_handler = logging.FileHandler(self.record.dir / "run.log", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(file_handler)
        redacting = RedactingFilter(self.redactor)
        for handler in root.handlers:
            handler.addFilter(redacting)
        try:
            yield
        finally:
            for handler in root.handlers:
                handler.removeFilter(redacting)
            root.removeHandler(file_handler)
            file_handler.close()
