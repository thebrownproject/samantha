"""Helpers for the local Swift <-> Python IPC protocol."""

from __future__ import annotations

from typing import Any

IPC_PROTOCOL_VERSION = 1
SUPPORTED_PROTOCOL_VERSIONS = frozenset({IPC_PROTOCOL_VERSION})


def protocol_message(msg_type: str, /, **payload: Any) -> dict[str, Any]:
    """Build a versioned IPC message."""
    message: dict[str, Any] = {
        "protocol_version": IPC_PROTOCOL_VERSION,
        "type": msg_type,
    }
    message.update(payload)
    return message


def attach_protocol_version(message: dict[str, Any]) -> dict[str, Any]:
    """Ensure an arbitrary outbound message carries the current protocol version."""
    if message.get("protocol_version") == IPC_PROTOCOL_VERSION:
        return message
    versioned = dict(message)
    versioned["protocol_version"] = IPC_PROTOCOL_VERSION
    return versioned


def validate_protocol_message(message: Any) -> dict[str, Any]:
    """Validate the inbound websocket payload for protocol compatibility."""
    if not isinstance(message, dict):
        raise ValueError("IPC payload must be a JSON object")

    version = message.get("protocol_version")
    if version is None:
        raise ValueError("Missing 'protocol_version' field")
    if not isinstance(version, int):
        raise ValueError("'protocol_version' must be an integer")
    if version not in SUPPORTED_PROTOCOL_VERSIONS:
        supported = ", ".join(str(v) for v in sorted(SUPPORTED_PROTOCOL_VERSIONS))
        raise ValueError(
            f"Unsupported protocol_version: {version}. Supported versions: {supported}",
        )

    msg_type = message.get("type")
    if not isinstance(msg_type, str) or not msg_type:
        raise ValueError("Missing 'type' field")

    return message
