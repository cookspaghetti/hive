# HIVE — Honeypot for Intelligence, Verdict & Evidence

A deceptive anti-scam agent for Telegram. When a suspected scammer messages the
user, HIVE takes over the conversation in a believable Malaysian persona,
wastes the scammer's time, harvests High-Value Indicators (HVIs), safely
analyses malicious links in a disposable sandbox, and produces a tamper-evident
forensic evidence bundle suitable for submission to MCMC / PDRM.

> Final Year Project. Not a production service. Built for anti-scam research,
> not for any illegal use.

## Architecture

Dual-channel Telegram (see fyp.txt):

- **Data plane** — Telethon MTProto userbot, logged in as the user's own
  account, performs the takeover and replies as the user.
- **Control plane** — Bot API bot the operator uses to configure/trigger the
  system and receive summaries + the signed evidence bundle.

One LangGraph state machine drives each conversation
(`IDLE → ARMED → ACTIVE → PROBING → CLOSING → SEALED`), one instance per peer.

| Layer | Module | Role |
|-------|--------|------|
| L1 Human-emulation | `middleware/` | typos, Manglish, tarpit delays |
| L2 Deceptive agent | `agent/` | ReAct + 4 personas + mem0/Qdrant |
| L3 Extraction | `extraction/` | GLiNER2 + regex + tiered media (local QR → vision fallback) |
| L4 Forensic sandbox | `sandbox/` | disposable network-locked Playwright |
| L5 Evidence vault | `vault/` | SHA-256 chain + RSA signature + ReportLab PDF |
| S6 Verdict | `verdict/` | continuous hybrid scam scoring |
| S7 Guardrails | `guardrails/` + `security/` | prompt-injection defence + encrypted session |
| S8 Orchestrator | `orchestrator.py` | lifecycle state machine |

## Setup

```bash
uv sync --extra dev          # install deps
cp .env.example .env         # then fill in secrets
docker compose up -d qdrant  # start vector store
uv run pytest                # backbone + hash-chain tests should pass
```

## Security notes

- The Telethon `.session` file is a **full credential to the user's Telegram
  account** — encrypted at rest, never committed (see `.gitignore`).
- The RSA signing key and evidence output stay outside the repo.
- Scammer input is untrusted and passes through prompt-injection guardrails.

## Status

Scaffold: lifecycle state machine + hash chain are implemented and tested;
all layer modules are typed stubs pending the Part 2 build phase. See `fyp.txt`
for the full spec and `reference-mapping.md` for the OpenClaw/Hermes design
references.

## License

MIT.
