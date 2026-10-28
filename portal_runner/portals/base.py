"""The contract between the generic runner and one portal."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from playwright.async_api import Page

from portal_runner.core.popups import Popup, PopupGuard
from portal_runner.core.record import RunRecord
from portal_runner.core.redact import Redactor
from portal_runner.core.resolver import Resolver, Target
from portal_runner.core.session import SessionRules


@dataclass(frozen=True)
class FlowContext:
    page: Page
    resolver: Resolver
    record: RunRecord
    guard: PopupGuard
    redactor: Redactor
    out_dir: Path


class Portal(ABC):
    name: ClassVar[str]
    offline_targets: ClassVar[Sequence[Target]]  # what `offline` resolves in saved pages

    @classmethod
    @abstractmethod
    def from_env(cls, inputs: dict[str, Any]) -> Portal:
        """Read settings from the environment and validate inputs (ConfigError / InputError)."""

    @property
    @abstractmethod
    def secrets(self) -> Sequence[str]: ...

    @property
    @abstractmethod
    def session_rules(self) -> SessionRules: ...

    @property
    @abstractmethod
    def popups(self) -> Sequence[Popup]: ...

    @abstractmethod
    def describe(self) -> str:
        """One line for the log, e.g. 'invoice ASSIGN-000101 for Test Customer A'."""

    @abstractmethod
    async def login(self, ctx: FlowContext) -> None:
        """Full login including the second factor; ends on a logged-in page."""

    @abstractmethod
    async def run(self, ctx: FlowContext) -> Path:
        """Navigate, fill, submit and capture. Returns the captured document."""
