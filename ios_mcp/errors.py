"""Structured error taxonomy for the iOS automation stack.

Agents recover far better from a typed error carrying a machine-readable code
and a concrete suggested next action than from a stack trace. Every layer
raises one of these; the MCP surface serializes them into tool errors with the
``code`` and ``hint`` preserved.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    # Layer 1: device fabric
    TOOLCHAIN_MISSING = "toolchain_missing"
    DEVICE_UNAVAILABLE = "device_unavailable"
    DEVICE_NOT_READY = "device_not_ready"
    DEVICE_LOCKED = "device_locked"
    APP_NOT_FOUND = "app_not_found"
    TUNNEL_DOWN = "tunnel_down"
    SIGNING_INVALID = "signing_invalid"

    # Layer 2: WDA client
    RUNNER_CRASHED = "runner_crashed"
    SESSION_LOST = "session_lost"
    WDA_ERROR = "wda_error"

    # Layer 3: perception
    ELEMENT_NOT_FOUND = "element_not_found"
    ELEMENT_STALE = "element_stale"
    ELEMENT_AMBIGUOUS = "element_ambiguous"
    NO_SNAPSHOT = "no_snapshot"

    # Layer 4: actions
    ELEMENT_NOT_INTERACTABLE = "element_not_interactable"
    UNEXPECTED_ALERT = "unexpected_alert"
    TIMEOUT = "timeout"

    # Layer 6: policy
    ACTION_REQUIRES_APPROVAL = "action_requires_approval"
    ACTION_REJECTED_BY_POLICY = "action_rejected_by_policy"
    APP_NOT_ALLOWED = "app_not_allowed"
    SESSION_HALTED = "session_halted"
    SECRET_NOT_FOUND = "secret_not_found"

    # Generic
    INVALID_ARGUMENT = "invalid_argument"
    NOT_SUPPORTED = "not_supported"
    INTERNAL = "internal"


class IosAutomationError(Exception):
    """Base class for every error this package raises deliberately."""

    code: ErrorCode = ErrorCode.INTERNAL

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
        recoverable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        if code is not None:
            self.code = code
        self.details = details or {}
        self.recoverable = recoverable

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": self.code.value,
            "message": self.message,
            "recoverable": self.recoverable,
        }
        if self.hint:
            payload["hint"] = self.hint
        if self.details:
            payload["details"] = self.details
        return payload

    def __str__(self) -> str:
        base = f"[{self.code.value}] {self.message}"
        return f"{base} Hint: {self.hint}" if self.hint else base


# --- Layer 1 ---------------------------------------------------------------


class AppNotFound(IosAutomationError):
    """A bundle id that is not installed on this device.

    Separate from `AppNotAllowed`, which is the policy gate refusing an app
    that does exist. This one is a typo or a wrong guess, and the fix is to
    look the id up rather than to change any setting.
    """

    code = ErrorCode.APP_NOT_FOUND


class ToolchainMissing(IosAutomationError):
    code = ErrorCode.TOOLCHAIN_MISSING


class DeviceUnavailable(IosAutomationError):
    code = ErrorCode.DEVICE_UNAVAILABLE


class DeviceNotReady(IosAutomationError):
    code = ErrorCode.DEVICE_NOT_READY


class DeviceLocked(IosAutomationError):
    code = ErrorCode.DEVICE_LOCKED


class TunnelDown(IosAutomationError):
    code = ErrorCode.TUNNEL_DOWN


class SigningInvalid(IosAutomationError):
    code = ErrorCode.SIGNING_INVALID


# --- Layer 2 ---------------------------------------------------------------


class RunnerCrashed(IosAutomationError):
    code = ErrorCode.RUNNER_CRASHED


class SessionLost(IosAutomationError):
    code = ErrorCode.SESSION_LOST


class WdaError(IosAutomationError):
    code = ErrorCode.WDA_ERROR


# --- Layer 3 ---------------------------------------------------------------


class ElementNotFound(IosAutomationError):
    code = ErrorCode.ELEMENT_NOT_FOUND


class ElementStale(IosAutomationError):
    code = ErrorCode.ELEMENT_STALE


class ElementAmbiguous(IosAutomationError):
    code = ErrorCode.ELEMENT_AMBIGUOUS


class NoSnapshot(IosAutomationError):
    code = ErrorCode.NO_SNAPSHOT


# --- Layer 4 ---------------------------------------------------------------


class ElementNotInteractable(IosAutomationError):
    code = ErrorCode.ELEMENT_NOT_INTERACTABLE


class UnexpectedAlert(IosAutomationError):
    code = ErrorCode.UNEXPECTED_ALERT


class ActionTimeout(IosAutomationError):
    code = ErrorCode.TIMEOUT


# --- Layer 6 ---------------------------------------------------------------


class ActionRequiresApproval(IosAutomationError):
    code = ErrorCode.ACTION_REQUIRES_APPROVAL


class ActionRejectedByPolicy(IosAutomationError):
    code = ErrorCode.ACTION_REJECTED_BY_POLICY


class AppNotAllowed(IosAutomationError):
    code = ErrorCode.APP_NOT_ALLOWED


class SessionHalted(IosAutomationError):
    code = ErrorCode.SESSION_HALTED


class SecretNotFound(IosAutomationError):
    code = ErrorCode.SECRET_NOT_FOUND


# --- Generic ---------------------------------------------------------------


class InvalidArgument(IosAutomationError):
    code = ErrorCode.INVALID_ARGUMENT


class NotSupported(IosAutomationError):
    code = ErrorCode.NOT_SUPPORTED
