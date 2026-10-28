import logging

import pyotp
import pytest

from portal_runner.core.codes import (
    Challenge,
    CodeRejectedError,
    CodeSubmitError,
    InvalidSecretError,
    TotpProvider,
    submit_with_one_retry,
)

SECRET = "JBSWY3DPEHPK3PXP"  # public example secret from the pyotp docs
WINDOW_START = 1_700_000_010.0  # divisible by 30: a TOTP window starts here


class FakeTime:
    def __init__(self, t: float) -> None:
        self.t = t
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def provider_at(t: float) -> tuple[TotpProvider, FakeTime]:
    ft = FakeTime(t)
    return TotpProvider(SECRET, clock=ft.clock, sleep=ft.sleep), ft


async def test_code_matches_reference_implementation():
    p, ft = provider_at(WINDOW_START + 10)
    assert await p.get_code(Challenge()) == pyotp.TOTP(SECRET).at(WINDOW_START + 10)
    assert ft.slept == []


async def test_waits_when_window_is_about_to_expire():
    p, ft = provider_at(WINDOW_START + 28.5)
    code = await p.get_code(Challenge())
    assert ft.t >= WINDOW_START + 30
    assert code == pyotp.TOTP(SECRET).at(ft.t)


async def test_retry_never_reuses_the_rejected_code():
    p, ft = provider_at(WINDOW_START + 5)
    first = await p.get_code(Challenge(attempt=1))
    second = await p.get_code(Challenge(attempt=2))
    assert ft.slept
    assert first != second


def test_secret_with_spaces_and_lowercase_is_accepted():
    TotpProvider("jbsw y3dp ehpk 3pxp")


def test_bad_secret_error_does_not_leak_the_secret():
    with pytest.raises(InvalidSecretError) as e:
        TotpProvider("NOT-BASE32-!!secretish")
    assert "secretish" not in str(e.value).lower()


def test_secret_hidden_from_repr():
    assert SECRET not in repr(TotpProvider(SECRET))


async def test_retry_once_then_succeed():
    p, _ = provider_at(WINDOW_START + 5)
    seen: list[str] = []

    async def submit(code: str) -> bool:
        seen.append(code)
        return len(seen) == 2

    await submit_with_one_retry(p, submit)
    assert len(seen) == 2
    assert seen[0] != seen[1]


async def test_two_rejections_is_final():
    p, _ = provider_at(WINDOW_START + 5)
    calls: list[str] = []

    async def submit(code: str) -> bool:
        calls.append(code)
        return False

    with pytest.raises(CodeRejectedError):
        await submit_with_one_retry(p, submit)
    assert len(calls) == 2


async def test_code_never_appears_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    p, _ = provider_at(WINDOW_START + 5)
    codes: list[str] = []

    async def submit(code: str) -> bool:
        codes.append(code)
        return False

    with pytest.raises(CodeRejectedError):
        await submit_with_one_retry(p, submit)
    assert not any(c in caplog.text for c in codes)


async def test_library_error_containing_the_code_is_scrubbed():
    """Regression: Playwright put 'fill("298886")' in its error message."""
    p, _ = provider_at(WINDOW_START + 5)
    used: list[str] = []

    async def submit(code: str) -> bool:
        used.append(code)
        raise RuntimeError(f'Locator.fill: not an input\n  - fill("{code}")')

    with pytest.raises(CodeSubmitError) as e:
        await submit_with_one_retry(p, submit)
    assert used[0] not in str(e.value)
    assert e.value.__suppress_context__
