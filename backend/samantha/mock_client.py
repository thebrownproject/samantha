"""Mock websocket client harness for backend verification before the macOS app exists."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from samantha.protocol import protocol_message

DEFAULT_WS_URL = "ws://127.0.0.1:9090"
DEFAULT_AUDIO_CHUNK_SIZE = 1920
DEFAULT_AUDIO_CHUNK_DELAY_MS = 20
DEFAULT_IDLE_TIMEOUT = 1.5


@dataclass(slots=True)
class HarnessSummary:
    json_messages: list[dict[str, Any]] = field(default_factory=list)
    audio_frames: int = 0
    audio_bytes: int = 0
    auto_approved: list[str] = field(default_factory=list)
    auto_rejected: list[str] = field(default_factory=list)


def encode_control_message(msg_type: str, /, **payload: Any) -> str:
    return json.dumps(protocol_message(msg_type, **payload))


def chunk_audio_bytes(data: bytes, chunk_size: int) -> list[bytes]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    return [data[index:index + chunk_size] for index in range(0, len(data), chunk_size)]


async def send_audio_file(
    ws: ClientConnection,
    path: Path,
    *,
    chunk_size: int = DEFAULT_AUDIO_CHUNK_SIZE,
    chunk_delay_ms: int = DEFAULT_AUDIO_CHUNK_DELAY_MS,
) -> int:
    data = path.read_bytes()
    chunk_delay_s = max(chunk_delay_ms, 0) / 1000
    chunks = chunk_audio_bytes(data, chunk_size)
    for chunk in chunks:
        await ws.send(chunk)
        if chunk_delay_s:
            await asyncio.sleep(chunk_delay_s)
    return len(chunks)


async def receive_messages(
    ws: ClientConnection,
    *,
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
    auto_approve: bool = False,
    auto_reject: bool = False,
    verbose: bool = True,
) -> HarnessSummary:
    summary = HarnessSummary()

    while True:
        try:
            message = await asyncio.wait_for(ws.recv(), timeout=idle_timeout)
        except TimeoutError:
            break

        if isinstance(message, bytes):
            summary.audio_frames += 1
            summary.audio_bytes += len(message)
            if verbose:
                print(f"[audio] frame={summary.audio_frames} bytes={len(message)}")
            continue

        payload = json.loads(message)
        summary.json_messages.append(payload)
        if verbose:
            print(f"[json] {json.dumps(payload, sort_keys=True)}")

        if payload.get("type") != "tool_approval_required":
            continue

        call_id = payload.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            continue

        if auto_approve:
            await ws.send(encode_control_message("approve_tool_call", call_id=call_id, always=False))
            summary.auto_approved.append(call_id)
            if verbose:
                print(f"[auto-approve] {call_id}")
        elif auto_reject:
            await ws.send(encode_control_message("reject_tool_call", call_id=call_id, always=False))
            summary.auto_rejected.append(call_id)
            if verbose:
                print(f"[auto-reject] {call_id}")

    return summary


async def run_mock_session(
    *,
    url: str = DEFAULT_WS_URL,
    get_state: bool = False,
    start_listening: bool = False,
    stop_listening: bool = False,
    inject_context: str | None = None,
    audio_file: Path | None = None,
    approve_call_id: str | None = None,
    reject_call_id: str | None = None,
    auto_approve: bool = False,
    auto_reject: bool = False,
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
    audio_chunk_size: int = DEFAULT_AUDIO_CHUNK_SIZE,
    audio_chunk_delay_ms: int = DEFAULT_AUDIO_CHUNK_DELAY_MS,
    verbose: bool = True,
) -> HarnessSummary:
    async with connect(url) as ws:
        if get_state:
            await ws.send(encode_control_message("get_state"))
        if start_listening:
            await ws.send(encode_control_message("start_listening"))
        if inject_context:
            await ws.send(encode_control_message("inject_context", text=inject_context))
        if audio_file is not None:
            await send_audio_file(
                ws,
                audio_file,
                chunk_size=audio_chunk_size,
                chunk_delay_ms=audio_chunk_delay_ms,
            )
        if stop_listening:
            await ws.send(encode_control_message("stop_listening"))
        if approve_call_id:
            await ws.send(encode_control_message("approve_tool_call", call_id=approve_call_id, always=False))
        if reject_call_id:
            await ws.send(encode_control_message("reject_tool_call", call_id=reject_call_id, always=False))

        summary = await receive_messages(
            ws,
            idle_timeout=idle_timeout,
            auto_approve=auto_approve,
            auto_reject=auto_reject,
            verbose=verbose,
        )

    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_WS_URL, help="Backend websocket URL")
    parser.add_argument("--get-state", action="store_true", help="Request the current backend state")
    parser.add_argument("--start-listening", action="store_true", help="Send start_listening")
    parser.add_argument("--stop-listening", action="store_true", help="Send stop_listening")
    parser.add_argument("--inject-context", help="Inject extra context into the live session")
    parser.add_argument("--audio-file", type=Path, help="Raw PCM16 mono file to stream as binary frames")
    parser.add_argument("--audio-chunk-size", type=int, default=DEFAULT_AUDIO_CHUNK_SIZE)
    parser.add_argument("--audio-chunk-delay-ms", type=int, default=DEFAULT_AUDIO_CHUNK_DELAY_MS)
    parser.add_argument("--approve-call-id", help="Send a manual approve_tool_call")
    parser.add_argument("--reject-call-id", help="Send a manual reject_tool_call")
    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve any tool_approval_required events")
    parser.add_argument("--auto-reject", action="store_true", help="Auto-reject any tool_approval_required events")
    parser.add_argument("--idle-timeout", type=float, default=DEFAULT_IDLE_TIMEOUT, help="Seconds to wait after the last received message")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-message output and print only the summary")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.auto_approve and args.auto_reject:
        parser.error("--auto-approve and --auto-reject are mutually exclusive")

    summary = asyncio.run(
        run_mock_session(
            url=args.url,
            get_state=args.get_state,
            start_listening=args.start_listening,
            stop_listening=args.stop_listening,
            inject_context=args.inject_context,
            audio_file=args.audio_file,
            approve_call_id=args.approve_call_id,
            reject_call_id=args.reject_call_id,
            auto_approve=args.auto_approve,
            auto_reject=args.auto_reject,
            idle_timeout=args.idle_timeout,
            audio_chunk_size=args.audio_chunk_size,
            audio_chunk_delay_ms=args.audio_chunk_delay_ms,
            verbose=not args.quiet,
        )
    )

    print(
        json.dumps(
            {
                "json_messages": len(summary.json_messages),
                "audio_frames": summary.audio_frames,
                "audio_bytes": summary.audio_bytes,
                "auto_approved": summary.auto_approved,
                "auto_rejected": summary.auto_rejected,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
