<p align="center"><img src="src/hive/webpanel/resources/logo.png" alt="HIVE logo" width="140"></p>

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
  provisioning/            Safe env storage and Telegram login state
  sandbox/                 Docker Playwright URL analysis
  security/                Encrypted Telethon session store
  transports/              Telethon userbot and Bot API control bot
  vault/                   Hash chain, signing, and PDF evidence bundle
  verdict/                 Hybrid scam scoring
tests/                     Offline unit and integration tests
docker/sandbox/Dockerfile  Disposable browser image
frontend/                  Nginx image and backend reverse proxy
docker-compose.yml         Frontend, backend, PostgreSQL, and Qdrant stack
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

For panel development, run the localhost-only server with hot reload:

```powershell
task dev
```

This watches the Python and web-panel assets under `src/` and restarts the
panel when they change. It does not auto-start Telegram or run Docker.

Start HIVE's localhost control panel:

```powershell
task run
```

Open `http://127.0.0.1:9130`. The same durable panel configures the LLM,
verifies the Bot API token, authorizes the Telethon user account (including
2FA), encrypts its session, and creates the evidence signing key. Telegram
starts automatically once the setup checklist is complete. Credential changes
can be applied by restarting only the managed agent runtime; the panel remains
available throughout.

For headless or terminal-only setup, copy `.env.example` to `.env`, fill in
the required values, then run `task bootstrap` for the interactive Telethon
login and signing key generation.

L2 memory uses an offline keyword recall backend by default. The Compose stack
still provisions Qdrant as its own service, but the backend only uses it when
`HIVE_USE_SEMANTIC_MEMORY=true` enables the mem0 semantic-memory upgrade:

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
The control panel provisions it in the browser; `task bootstrap` provides the
equivalent terminal flow. The session string is never written in plaintext.

The evidence signer expects an RSA private key at `HIVE_SIGNING_KEY_PATH`.
For development, `hive.vault.signer.generate_keypair()` can generate one.
Keep session files, `.env`, private keys, and evidence output out of git.
Compose stores completed takeover transcripts in PostgreSQL so the control
panel can reopen past chats. `task dev` falls back to local JSON records under
`evidence/history/` when `HIVE_DATABASE_URL` is unset.

## Running

The panel remains available before, during, and after setup:

```powershell
task run
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

## Docker Deployment

Compose runs four separate services:

| Service | Responsibility | Host exposure |
| --- | --- | --- |
| `frontend` | Nginx static control panel and same-origin API proxy | `127.0.0.1:9130` |
| `backend` | FastAPI, Telegram runtime, evidence, and sandbox orchestration | Internal only |
| `postgres` | Durable takeover transcripts and history index | Internal only |
| `qdrant` | Optional mem0 semantic-memory vectors | Internal only |

The backend still runs **Docker-in-Docker** because HIVE spawns disposable
Layer 4 sandbox containers. Its inner daemon remains isolated from the host
daemon.

```powershell
task run                  # build, run, and publish the panel on 127.0.0.1:9130
```

The equivalent Compose command is:

```powershell
docker compose up --build --remove-orphans
```

`--remove-orphans` replaces the legacy single `hive` service container on the
first launch. It does not remove the existing evidence, model cache, Qdrant
data, or named database volumes.

The `backend` container needs `privileged: true` so its inner daemon can run.
On first start the entrypoint launches `dockerd`, builds the `hive-sandbox`
image inside it, then runs `python -m hive`. Nginx proxies `/api/*` and
`/health` to that internal backend. Sealed evidence remains in the mounted
`./evidence` directory, while takeover history is stored in the
`hive_postgres` volume.

GLiNER/Hugging Face model files are stored in the persistent
`hive_model_cache` Docker volume. The first `task run` still downloads the
weights, but later container rebuilds and recreations reuse them. A normal
`task stack:down` preserves the model and PostgreSQL volumes;
`docker compose down --volumes` removes them.

> Security trade-off: a privileged Docker-in-Docker container has broad kernel
> capabilities on the host. This is an accepted cost for a self-contained
> research prototype and is documented here deliberately; a hardened deployment
> would instead use a rootless/sysbox runtime or a dedicated VM. The GLiNER
> dependency also pulls in PyTorch, so the image is large.

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
uv run pytest -q
```

Focused review-fix coverage:

```powershell
uv run pytest tests/test_sandbox_runner.py tests/test_userbot.py tests/test_prompt_defense.py tests/test_control_bot.py -q
```

## Current Limits

- Live Telegram behavior requires real credentials and is not exercised by the
  offline test suite.
- GLiNER model loading can be slow on first use while its weights populate the
  persistent `hive_model_cache` volume.
- `describe_image()` is still a placeholder for future vision fallback work.
- In-session memory uses an offline keyword-recall backend by default; the
  semantic mem0 + Qdrant backend (`HIVE_USE_SEMANTIC_MEMORY`) is opt-in and not
  exercised by the offline test suite.
- The Section 90A certificate text and signature tooling support evidence
  packaging; legal admissibility still depends on operator process, custody,
  and local legal requirements.

## License

MIT.
