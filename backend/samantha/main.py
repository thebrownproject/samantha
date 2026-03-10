"""Entry point: start WebSocket server and initialize agents."""

import asyncio


async def _run() -> None:
    """Start the backend server and agent runtime."""
    ...


def main() -> None:
    """Synchronous entry point for the samantha CLI."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
