"""HIVE entry point.

Boots both Telegram transports and the orchestrator. Wiring is stubbed until
the transports are implemented.
"""

from __future__ import annotations

from hive.config import load_settings


def main() -> None:
    settings = load_settings()
    print(f"HIVE v0.1.0 — default persona: {settings.default_persona}")
    # TODO: decrypt session, start UserbotTransport + ControlBot, run event loop.
    raise SystemExit("HIVE runtime not yet implemented — see fyp.txt build phase.")


if __name__ == "__main__":
    main()
