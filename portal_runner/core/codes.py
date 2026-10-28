"""Second-factor codes. Rule: a code is never logged or put in an error message."""

from __future__ import annotations

import asyncio
import binascii
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

import pyotp

from portal_runner.core.errors import ConfigError, FinalError

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # one retry; more risks an account lockout


class InvalidSecretError(ConfigError):
    pass


class CodeRejectedError(FinalError):
    pass


class CodeSubmitError(FinalError):
    pass


@dataclass(frozen=True)
class Challenge:
    kind: str = "totp"
    attempt: int = 1
    portal: str = ""


class CodeProvider(Protocol):
    async def get_code(self, challenge: Challenge) -> str: ...


@dataclass
class TotpProvider:
    """RFC 6238 codes from the authenticator-app secret.

    Waits for a fresh window when the current one is about to expire, and on a
    retry so the rejected code is never resent. ``clock``/``sleep`` are injectable for tests.
    """

    secret: str = field(repr=False)
    period: int = 30
    min_remaining: float = 3.0
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    _totp: pyotp.TOTP = field(init=False, repr=False)
    _last_window: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.secret = self.secret.replace(" ", "").upper()
        try:
            pyotp.TOTP(self.secret).byte_secret()
        except (binascii.Error, ValueError):
            raise InvalidSecretError("the TOTP secret is not valid base32 (check .env)") from None
        self._totp = pyotp.TOTP(self.secret, interval=self.period)

    async def get_code(self, challenge: Challenge) -> str:
        now = self.clock()
        remaining = self.period - now % self.period
        same_window_retry = challenge.attempt > 1 and self._last_window == now // self.period
        if remaining < self.min_remaining or same_window_retry:
            log.info("2FA: waiting %.1fs for a fresh TOTP window", remaining + 0.5)
            await self.sleep(remaining + 0.5)
            now = self.clock()
        self._last_window = int(now // self.period)
        log.info("2FA: code generated (portal=%s, attempt=%d)", challenge.portal, challenge.attempt)
        return self._totp.at(int(now))


async def submit_with_one_retry(
    provider: CodeProvider, submit: Callable[[str], Awaitable[bool]], *, portal: str = ""
) -> None:
    """Get a code, ``submit`` it (returns True if accepted), retry exactly once."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        code = await provider.get_code(Challenge(attempt=attempt, portal=portal))
        try:
            accepted = await submit(code)
        except Exception as e:
            # Libraries echo arguments in errors (Playwright: 'fill("123456")'): scrub the code.
            message = str(e).replace(code, "******")
            raise CodeSubmitError(f"{type(e).__name__} while submitting the code: {message}") from None
        if accepted:
            log.info("2FA: accepted on attempt %d", attempt)
            return
        log.warning("2FA: code rejected on attempt %d", attempt)
    raise CodeRejectedError(f"{portal or 'the portal'} rejected the code {MAX_ATTEMPTS} times")
