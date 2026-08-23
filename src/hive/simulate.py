"""Direct, transport-free conversation simulator for HIVE.

This module deliberately enters at ``HiveEngine.process_messages``: everything
from guardrails through evidence recording runs normally, while Telegram's
takeover and delivery layers are left out.  It is intended for fast, repeatable
conversation checks during development.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from hive.agent.personas import PERSONAS
from hive.audit import configure_audit
from hive.config import Settings, load_settings
from hive.logging_setup import configure_logging
from hive.runtime import HiveEngine, TurnOutput, build_engine
from hive.state import Message
from hive.vault.paths import new_bundle_path

BURST_SEPARATOR = "|||"


def configure_console_encoding() -> None:
    """Make multilingual/model output safe in redirected Windows terminals."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


class SimulationHarness:
    """Own one synthetic conversation and feed bursts into the real engine."""

    def __init__(
        self,
        engine: HiveEngine,
        *,
        peer_id: int,
        persona: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if persona not in PERSONAS:
            raise ValueError(f"unknown persona: {persona}")
        self.engine = engine
        self.session, self.chain = engine.new_session(peer_id, persona)
        self._clock = clock
        self._next_message_id = 1

    def send(self, texts: Sequence[str]) -> TurnOutput:
        """Process one phone-check burst without Telegram or delivery sleeps."""
        clean = [text.strip() for text in texts if text.strip()]
        if not clean:
            raise ValueError("a burst must contain at least one non-empty message")
        messages: list[Message] = []
        for text in clean:
            messages.append(
                Message(
                    role="stranger",
                    text=text,
                    ts=self._clock(),
                    msg_id=self._next_message_id,
                )
            )
            self._next_message_id += 1
        return self.engine.process_messages(self.session, self.chain, messages)

    def status(self) -> str:
        kinds = sorted({item.kind for item in self.session.hvis})
        return (
            f"phase={self.session.phase.value} verdict={self.session.verdict} "
            f"score={self.session.verdict_score:.2f} inbound={self.session.turn_count} "
            f"exchanges={self.session.exchange_count} hvis={len(self.session.hvis)} "
            f"kinds={kinds or '-'} chain={'valid' if self.chain.verify() else 'broken'}"
        )

    def history(self) -> list[str]:
        return [
            f"{message.role:8} #{message.msg_id}: {message.text}"
            for message in self.session.messages
        ]

    def seal(self, evidence_dir: str | Path, key_path: str, operator_name: str = "") -> str:
        target = new_bundle_path(evidence_dir, self.session)
        return self.engine.close_session(
            self.session,
            self.chain,
            str(target),
            key_path,
            operator_name=operator_name,
        )


def parse_burst(raw: str) -> list[str]:
    """Split the CLI's explicit burst notation into individual messages."""
    messages = [part.strip() for part in raw.split(BURST_SEPARATOR) if part.strip()]
    if not messages:
        raise ValueError("enter at least one message")
    return messages


def render_turn(output: TurnOutput, stream: TextIO) -> None:
    """Show intended message bubbles and delays without actually waiting."""
    bubbles = output.messages or ((output.text,) if output.text else ())
    for index, bubble in enumerate(bubbles):
        delay = (
            output.message_delays_s[index]
            if index < len(output.message_delays_s)
            else output.delay_s if index == 0
            else 0.0
        )
        typing = (
            output.message_typing_s[index]
            if index < len(output.message_typing_s)
            else 0.0
        )
        print(
            f"HIVE [{output.pace}] (+{delay:.1f}s planned, {typing:.1f}s typing): {bubble}",
            file=stream,
        )
    if output.handed_back:
        print("HIVE handed the conversation back as likely benign.", file=stream)
    elif output.terminated:
        print(f"HIVE ended the simulation: {output.reason}.", file=stream)


def _print_help(stream: TextIO) -> None:
    print(
        "Commands: /status, /history, /seal, /help, /quit\n"
        "Use ||| between messages to deliver them as one phone-check burst.",
        file=stream,
    )


def _seal(
    harness: SimulationHarness,
    settings: Settings,
    evidence_dir: Path,
    stream: TextIO,
) -> None:
    path = harness.seal(evidence_dir, settings.signing_key_path, settings.operator_name)
    print(f"Sealed report: {path}", file=stream)


def interactive_loop(
    harness: SimulationHarness,
    settings: Settings,
    *,
    evidence_dir: Path,
    input_fn: Callable[[str], str] = input,
    stream: TextIO = sys.stdout,
) -> int:
    _print_help(stream)
    while True:
        try:
            raw = input_fn("SCAMMER> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("", file=stream)
            return 0
        if not raw:
            continue
        command = raw.lower()
        if command in {"/quit", "/exit"}:
            return 0
        if command == "/help":
            _print_help(stream)
            continue
        if command == "/status":
            print(harness.status(), file=stream)
            continue
        if command == "/history":
            lines = harness.history()
            print("\n".join(lines) if lines else "No messages yet.", file=stream)
            continue
        if command == "/seal":
            _seal(harness, settings, evidence_dir, stream)
            return 0
        if raw.startswith("/"):
            print("Unknown command. Use /help.", file=stream)
            continue

        try:
            output = harness.send(parse_burst(raw))
        except Exception as exc:  # noqa: BLE001 - keep an interactive test session usable
            print(f"Simulation error: {exc}", file=stream)
            continue
        render_turn(output, stream)
        print(f"STATE {harness.status()}", file=stream)
        if output.handed_back or output.terminated:
            return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exercise HIVE's real conversation pipeline without Telegram.",
    )
    parser.add_argument("--persona", choices=sorted(PERSONAS), default=None)
    parser.add_argument("--peer-id", type=int, default=None, help="Synthetic evidence peer ID")
    parser.add_argument(
        "--send",
        action="append",
        default=[],
        metavar="TEXT",
        help="Send one burst and exit; repeat for exchanges, use ||| within a burst",
    )
    parser.add_argument(
        "--no-ner",
        action="store_true",
        help="Skip loading the optional GLiNER model",
    )
    parser.add_argument("--seal", action="store_true", help="Seal scripted messages into a report")
    parser.add_argument("--evidence-dir", type=Path, default=Path("evidence/simulations"))
    return parser


def run(argv: Sequence[str] | None = None, *, stream: TextIO = sys.stdout) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    configure_audit(settings.audit_path, settings.database_url)
    configure_logging(settings.log_level)
    persona = args.persona or settings.default_persona
    peer_id = args.peer_id if args.peer_id is not None else -time.time_ns()

    try:
        engine = build_engine(settings, load_ner=not args.no_ner)
        harness = SimulationHarness(engine, peer_id=peer_id, persona=persona)
        print(
            f"HIVE simulator: peer={peer_id} persona={persona} telegram=off "
            f"case_similarity={'on' if settings.use_case_similarity else 'off'}",
            file=stream,
        )
        if not args.send:
            return interactive_loop(
                harness,
                settings,
                evidence_dir=args.evidence_dir,
                stream=stream,
            )

        for raw in args.send:
            output = harness.send(parse_burst(raw))
            render_turn(output, stream)
            print(f"STATE {harness.status()}", file=stream)
            if output.handed_back or output.terminated:
                break
        if args.seal:
            _seal(harness, settings, args.evidence_dir, stream)
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI should report a concise actionable failure
        print(f"Simulation failed: {exc}", file=stream)
        return 1


def main() -> None:
    configure_console_encoding()
    raise SystemExit(run())


if __name__ == "__main__":
    main()
