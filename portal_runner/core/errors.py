"""Error types. Retryable vs final drives the retry policy in ``core.retry``."""

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    FAILED = 1
    BAD_CONFIG = 2


class RetryableError(Exception):
    """Transient: the page was not ready. Repeating the stage can succeed."""


class FinalError(Exception):
    """Repeating cannot help and may hurt (wrong password, CAPTCHA, duplicate record)."""


class ConfigError(Exception):
    """Missing or invalid settings in .env."""


class InputError(Exception):
    """Missing or invalid values in the inputs JSON."""
