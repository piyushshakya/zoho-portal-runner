import pytest

from portal_runner.core.codes import CodeRejectedError
from portal_runner.core.errors import FinalError, InputError, RetryableError
from portal_runner.core.resolver import ResolveError, Target
from portal_runner.core.retry import backoff_delay, is_retryable, with_retries


async def no_sleep(_: float) -> None:
    return None


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (RetryableError("slow"), True),
        (ResolveError(Target("x"), []), True),
        (FinalError("captcha"), False),
        (InputError("bad json"), False),
        (CodeRejectedError("2fa twice"), False),
        (ValueError("a bug"), False),
    ],
)
def test_classification(error, retryable):
    assert is_retryable(error) is retryable


def test_backoff_grows_and_is_capped():
    assert [backoff_delay(n, jitter=0) for n in range(1, 7)] == [1, 2, 4, 8, 8, 8]


async def test_retryable_is_retried_until_success():
    calls: list[int] = []

    async def fn(attempt: int) -> str:
        calls.append(attempt)
        if attempt < 3:
            raise RetryableError("not yet")
        return "ok"

    assert await with_retries(fn, what="t", attempts=3, sleep=no_sleep) == "ok"
    assert calls == [1, 2, 3]


async def test_final_error_is_never_retried():
    calls: list[int] = []

    async def fn(attempt: int) -> None:
        calls.append(attempt)
        raise FinalError("wrong password")

    with pytest.raises(FinalError):
        await with_retries(fn, what="t", attempts=5, sleep=no_sleep)
    assert calls == [1]


async def test_retries_are_bounded():
    calls: list[int] = []

    async def fn(attempt: int) -> None:
        calls.append(attempt)
        raise RetryableError("never ready")

    with pytest.raises(RetryableError):
        await with_retries(fn, what="t", attempts=3, sleep=no_sleep)
    assert calls == [1, 2, 3]
