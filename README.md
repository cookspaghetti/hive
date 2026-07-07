# HIVE - Honeypot for Intelligence, Verdict & Evidence

HIVE is a research prototype for Telegram anti-scam operations. When an operator
chooses to take over a suspicious chat, HIVE replies as a believable Malaysian
persona, slows the scammer down, extracts High-Value Indicators (HVIs), analyses
links in a disposable browser sandbox, scores the conversation, and produces a
signed evidence bundle.

> Final Year Project. This is not a production service, legal advice, or a tool
> for impersonation or harassment. Use it only for consented anti-scam research
> and evidence preservation.

## What Is Implemented

- Dual Telegram runtime:
  - Telethon userbot data plane, logged in with the user's encrypted
    `StringSession`.
  - Bot API control plane restricted to `HIVE_OPERATOR_ID`.
- Turn pipeline in `HiveEngine`:
  - S7 prompt-injection and bot-probe screening.
  - L3 regex + optional GLiNER extraction.
  - L4 Playwright-in-Docker URL analysis.
  - S6 hybrid verdict scoring.
  - L2 persona reply generation through the cost-tiered LLM router, with
    in-session memory recall of earlier disclosures.
  - L1 linguistic middleware and tarpit delays.
- Evidence vault:
  - SHA-256 hash chain.
  - ReportLab PDF bundle.
  - RSA-PSS/SHA-256 detached signature.
  - Section 90A certificate template text.
- Safeguards:
  - Encrypted-at-rest Telethon session storage with AES-GCM and scrypt.
  - Early hand-back for likely benign conversations.
  - Prompt defense note injected into the system prompt when S7 flags a probe.
- Offline regression tests for the core pipeline, guardrails, sandbox analysis,
  session encryption, evidence bundle, in-session memory recall, userbot
  hand-back, and control bot validation.

## Architecture

HIVE has two Telegram planes:

- **Data plane**: `hive.transports.userbot.UserbotTransport`
  connects through Telethon and sends replies as the user's own account for
  chats under active takeover.
- **Control plane**: `hive.transports.control_bot.ControlBot`
  connects through the Telegram Bot API and lets the operator start, inspect,
  and stop takeovers.

The runtime entrypoint is `python -m hive`, which loads settings, decrypts the
Telethon session, builds a `HiveEngine`, starts both transports, and waits on the
Telethon client.

| Layer | Module | Role |
| --- | --- | --- |
| L1 Human emulation | `src/hive/middleware/` | Typo injection, Manglish markers, tarpit delay |
| L2 Deceptive agent | `src/hive/agent/` | Persona prompts, message construction, model-tier routing |
| L3 Extraction | `src/hive/extraction/` | Regex HVIs, optional GLiNER NER, local QR decoding |
| L4 Sandbox | `src/hive/sandbox/` | Disposable Playwright Docker runner and URL verdict signals |
| L5 Evidence vault | `src/hive/vault/` | Hash chain, PDF bundle, RSA signature |
| S6 Verdict | `src/hive/verdict/` | Hard + soft signal scoring |
| S7 Guardrails | `src/hive/guardrails/`, `src/hive/security/` | Prompt-injection defense and encrypted session storage |
| S8 Runtime | `src/hive/runtime.py`, `src/hive/transports/` | Per-turn orchestration and Telegram IO |

Session state moves through:

```text
IDLE -> ARMED -> ACTIVE -> PROBING -> CLOSING -> SEALED
```

## Repository Layout

```text
src/hive/
  __main__.py              Runtime entrypoint
  config.py                Environment-backed settings
  runtime.py               Integrated per-turn engine
  state.py                 Session, message, HVI, and lifecycle schema
  agent/                   Personas and LLM message assembly
  extraction/              Regex, GLiNER, and media extraction
  guardrails/              Prompt-injection and bot-probe screening
  llm/                     OpenAI-compatible client and tier router
  middleware/              Human-emulation text and delay middleware
  sandbox/                 Docker Playwright URL analysis
  security/                Encrypted Telethon session store
  transports/              Telethon userbot and Bot API control bot
  vault/                   Hash chain, signing, and PDF evidence bundle
  verdict/                 Hybrid scam scoring
tests/                     Offline unit and integration tests
docker/sandbox/Dockerfile  Disposable browser image
docker-compose.yml         Qdrant support service
```

## Setup

Prerequisites:

- Python 3.11 or newer.
- Docker, for the sandbox image and optional Qdrant service.
- `uv`, or another Python environment manager.
- Telegram API credentials from `https://my.telegram.org`.
- Telegram Bot API token from BotFather.
- An OpenAI-compatible LLM endpoint. The default config targets Ollama Cloud.

Install dependencies:

```powershell
uv sync --extra dev
```

Create local config:

```powershell
Copy-Item .env.example .env
```

Then fill in the `HIVE_...` values in `.env`.

L2 memory works out of the box with an offline keyword recall backend, so no
external store is required. Qdrant is only needed for the optional semantic
memory upgrade (`HIVE_USE_SEMANTIC_MEMORY=true`, backed by mem0):

```powershell
docker compose up -d qdrant
```

Build the forensic sandbox image:

```powershell
docker build -t hive-sandbox:latest docker/sandbox
```

## Secrets

HIVE expects an encrypted Telethon `StringSession` at
`HIVE_TG_SESSION_PATH` and decrypts it with `HIVE_SESSION_PASSPHRASE`.
The repository provides the storage functions in
`hive.security.session_store`, but not a login/bootstrap CLI.

The evidence signer expects an RSA private key at `HIVE_SIGNING_KEY_PATH`.
For development, `hive.vault.signer.generate_keypair()` can generate one.
Keep session files, `.env`, private keys, and evidence output out of git.

## Running

After `.env`, the encrypted session, signing key, and sandbox image are ready:

```powershell
python -m hive
```

Control bot commands:

```text
/takeover <peer_id> [persona]
/persona <name>
/status <peer_id>
/stop <peer_id>
```

Valid personas:

```text
confused_elderly
naive_young_adult
overseas_worker
small_business_owner
```

`/stop` ends the takeover, seals the evidence bundle under `evidence/`, sends
the summary, and attaches the generated PDF. If the engine decides the chat is
likely benign after enough turns, the userbot ends the takeover automatically
and notifies the operator.

## Sandbox Notes

`PlaywrightDockerRunner` launches one short-lived Docker container per URL with:

- a dedicated Docker bridge network,
- read-only root filesystem,
- writable `/tmp` tmpfs,
- dropped Linux capabilities,
- no-new-privileges,
- memory and PID limits,
- a single bind mount for `/out` screenshots.

The runner creates the dedicated Docker network automatically if it is missing.
Docker bridge isolation is not a complete LAN/host egress firewall by itself.
For stronger host/LAN denial, add `DOCKER-USER` firewall rules for the sandbox
network subnet, as described by `EGRESS_FIREWALL_HINT` in
`src/hive/sandbox/runner.py`.

## Testing

Run the full suite:

```powershell
D:\hive\.venv\Scripts\python.exe -m pytest -q
```

In this workspace, using the explicit venv interpreter avoids the WindowsApps
Python shim. Expected current result:

```text
99 passed, 1 warning
```

Focused review-fix coverage:

```powershell
D:\hive\.venv\Scripts\python.exe -m pytest tests/test_sandbox_runner.py tests/test_userbot.py tests/test_prompt_defense.py tests/test_control_bot.py -q
```

## Current Limits

- Live Telegram behavior requires real credentials and is not exercised by the
  offline test suite.
- GLiNER model loading can be slow and may download model weights on first use.
- `describe_image()` is still a placeholder for future vision fallback work.
- In-session memory uses an offline keyword-recall backend by default; the
  semantic mem0 + Qdrant backend (`HIVE_USE_SEMANTIC_MEMORY`) is opt-in and not
  exercised by the offline test suite.
- The Section 90A certificate text and signature tooling support evidence
  packaging; legal admissibility still depends on operator process, custody,
  and local legal requirements.

## License

MIT.
