"""Names and safe resolution of source credential environment references."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

_ENVIRONMENT_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")


class LiveSessionError(ValueError):
    """Raised when live-session preparation violates a fixed boundary."""


@dataclass(frozen=True, slots=True)
class _ResolvedCredentials:
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CredentialEnvironmentReferences:
    """Names of environment variables; resolved secret values are never retained here."""

    api_key_variable: str
    api_secret_variable: str

    def __post_init__(self) -> None:
        for value in (self.api_key_variable, self.api_secret_variable):
            if not isinstance(value, str) or _ENVIRONMENT_NAME.fullmatch(value) is None:
                raise LiveSessionError("credential reference must be a safe environment name")
        if self.api_key_variable == self.api_secret_variable:
            raise LiveSessionError("credential references must be distinct")

    def resolve(self, environment: Mapping[str, str]) -> _ResolvedCredentials:
        if not isinstance(environment, Mapping):
            raise TypeError("environment must be a mapping")
        try:
            api_key = environment.get(self.api_key_variable)
            api_secret = environment.get(self.api_secret_variable)
        except Exception:  # noqa: BLE001 - untrusted mapping boundary is sanitized
            raise LiveSessionError("credential references could not be resolved") from None
        if not isinstance(api_key, str) or not api_key:
            raise LiveSessionError("API key credential is unavailable")
        if not isinstance(api_secret, str) or not api_secret:
            raise LiveSessionError("API secret credential is unavailable")
        if api_key == api_secret:
            raise LiveSessionError("resolved credentials must be distinct")
        return _ResolvedCredentials(api_key=api_key, api_secret=api_secret)
