# FYP Part 2 Plan and HIVE Gap Audit

Audit date: 23 August 2026

This note consolidates the Part 2 report plan and the remaining work identified by comparing:

- `FYP Structure Guidelines.pdf`
- `Use Case Diagrams.pdf`
- `FYP_PART_1.docx` and `FYP_PART_1_TP070466.pdf`
- the current HIVE repository, tests, configuration, and README

## Submission assumptions

- Final report deadline: 2 September 2026 before 11:00 p.m., subject to confirmation.
- The final report extends Part 1 rather than being written as a separate report.
- Chapters 1-3 remain but must be corrected to describe the completed implementation.
- The Part 1 Chapter 4 conclusion is replaced by the final Chapters 4-6.

## Part 1 corrections

### Front matter

- Replace the Investigation Report cover with the approved final FYP cover.
- Add the thesis confidentiality declaration and library form.
- Generate separate Table of Contents, List of Figures, and List of Tables.
- Rewrite the abstract after testing. The current abstract is about 241 words and has eight keywords; the guide permits 200 words and a maximum of six keywords.
- Update all page numbers, captions, cross-references, and appendix references after the final document is assembled.

### Chapter 1

- Change Section 1.7 from a four-chapter IR overview to a six-chapter final-report overview.
- Revise the scope to distinguish technical live-Telegram capability from the controlled/private evaluation scope.
- Align the aim with Telegram rather than implying deployment across all social-media platforms.
- Give Objective 3 operational definitions for engagement, indicator extraction, and agent detection.
- Replace the IR-only project planner with the complete project timeline or a Part 2 timeline, depending on supervisor preference.

### Chapter 2

Revise the technical research and architecture table to reflect the actual system:

- Telethon userbot data plane and Telegram Bot API control plane.
- FastAPI control panel, Nginx frontend, and PostgreSQL persistence.
- Active-session continuity uses recent messages and deterministic validated facts; FastEmbed/Qdrant is reserved for cross-case scam-pattern candidates.
- GLiNER with `urchade/gliner_multi-v2.1`, not a fixed GLiNER2 implementation.
- Scrapling stealth browser inside disposable Docker containers, with Playwright beneath the browser automation stack where applicable.
- Tiered Ollama-compatible text models and a separate vision model.
- S6 verdict scoring, S7 guardrails/security, and S8 runtime orchestration.
- AES-GCM/scrypt session protection, permanent audit ledger, media analysis, case intelligence, and the web panel.

Replace the old five-layer architecture figure with an implementation-accurate component and deployment view.

### Chapter 3

- Retain RAD but rewrite it retrospectively as completed development iterations.
- Update the requirements traceability table against the implemented system and final test evidence.
- Revise UR-04: implementation timing uses a configurable phone-check window and approximately 2-45-second reply delays, not a guaranteed minutes-long delay.
- Expand UR-06 to cover the implemented indicator types, media extraction, QR/OCR, and case intelligence where retained in scope.
- Mark UR-09 as implemented in the working prototype: the session inspector,
  control summary, and sealed report now provide source-linked Malaysian reporting
  guidance. Retain a supervisor-review condition because official instructions and
  legal wording can change.
- Qualify UR-12: HIVE supports integrity verification and a Section 90A certificate workflow but cannot guarantee court admissibility.

### Remove or relocate the Part 1 conclusion

The existing IR conclusion should not remain as final Chapter 4. Reuse its investigation summary in the introduction to Part 2 and move its limitations/future-work material into Chapter 6.

## Chapter 4: Design and Implementation

### 4.1 Introduction

State the chapter purpose, completed prototype boundary, RAD implementation approach, and development environment.

### 4.2 Design

Include:

- Final requirements-to-component traceability matrix.
- Use-case diagram plus textual use-case specifications.
- System context diagram.
- Component architecture for L1-L5 and S6-S8.
- Session state diagram: `IDLE -> ARMED -> ACTIVE -> PROBING -> CLOSING -> SEALED`.
- Activity diagram for takeover and message processing.
- Sequence diagrams for takeover approval, inbound processing, URL analysis, and stop/seal/archive.
- Class or module diagram for the main HIVE components.
- Four-service deployment diagram plus disposable sandbox containers.
- Security and forensic design covering credentials, isolation, auditability, hashing, signing, and evidence custody.

Use-case actors should be limited to external roles and services: operator, suspicious Telegram contact, Telegram data plane, control bot, LLM service, and receiving authority as an offstage beneficiary. Use cases should be named as user goals. Use `<<include>>` only for mandatory reused behaviour and `<<extend>>` only for conditional behaviour. Textual descriptions are more important than the overview diagram.

### 4.3 Database Design

Document:

- PostgreSQL: takeover history, analysis runs, audit mirror, cases, indicators, and relationships.
- Qdrant: the derived `hive_cases` scam-pattern candidate collection; no active-message vector collection.
- Filesystem: media, authoritative JSONL audit ledger, PDFs, signatures, keys, and backups.
- ERD, data dictionary, ownership/authority rules, retention, backup, and recovery.

### 4.4 Interface Design

Document the setup wizard, runtime dashboard, model configuration, Telegram setup, signing setup, takeover inbox, session inspector, intelligence workspace, evidence page, audit activity, and logs. Include a navigation diagram and captioned interface designs/screenshots.

### 4.5 Execution

Describe the completed RAD iterations:

1. Agent, personas, and extraction.
2. Human-emulation middleware and deterministic session context.
3. Sandbox and evidence vault.
4. Telegram data/control planes.
5. Web panel and persistence.
6. Guardrails, verdict engine, media intelligence, and auditability.
7. Case intelligence, deployment, testing, and hardening.

Explain key algorithms and implementation decisions in the chapter. Put substantial code samples in Appendix F.

### 4.6 Screenshots

Show the complete product and justify each screen or important state. Include setup, runtime health, takeover approval, active conversation, indicators, sandbox result, evidence generation, audit view, and archived case review.

### 4.7 Summary

Summarise how the design implements the objectives and establishes the evaluation basis.

## Chapter 5: Results and Discussion

Use at least these three techniques:

1. Requirements-based black-box testing mapped to UR-01 through UR-12.
2. Controlled scenario evaluation using English, Mandarin, and Manglish scam scripts.
3. UAT plus SUS with at least three intended target users; five to ten is preferable if ethics approval and time permit.

Required measures:

- Functional pass/fail rate by requirement.
- Engagement duration and completed exchanges.
- HVI precision, recall, and F1 by indicator type.
- Indicators extracted per session.
- Persona realism and agent-detection rate.
- Verdict accuracy and false-positive/false-negative analysis.
- Sandbox outcome and containment evidence.
- Hash-chain and RSA-signature verification, including deliberate tampering tests.
- SUS score and qualitative user feedback.
- Response latency, resource consumption, and important failure modes.

Discuss failures and limitations rather than presenting only successful demonstrations.

## Chapter 6: Conclusion

### 6.1 Critical Evaluation

Evaluate each objective and final requirement separately. Discuss contribution, strengths, and the value of the response-only, dual-plane, evidence-producing architecture.

### 6.2 Limitations

Cover controlled evaluation, sample size, Telegram/model dependencies, cross-case vector limitations, sandbox constraints, model cold starts, privileged Docker-in-Docker, detection-evasion uncertainty, privacy/retention, and the distinction between evidence integrity and legal admissibility.

### 6.3 Recommendations

Cover Bahasa Malaysia/code-switching, additional platform adapters, larger adversarial trials, hardened sandbox deployment, formal law-enforcement validation, guided reporting, and privacy-preserving collaborative threat intelligence.

## Appendices and submission package

- Appendix A: PPF.
- Appendix B: ethics forms and any Part 2 amendment/approval.
- Appendix C: six supervisor log sheets.
- Appendix D: A3 poster.
- Appendix E: Gantt chart.
- Appendix F: representative code implementation.
- Appendix G: respondent demographic profile.
- Additional appendices: detailed test cases, anonymised UAT/SUS evidence, expanded evaluation results, and evidence-verification output.
- Final submission package: final report, code ZIP, and required presentation video.

## HIVE gap audit

### Implementation progress - 22 August 2026

The original audit below is retained as a baseline. The following work is now
implemented in the current working tree:

| Gap/workstream | Current status | Evidence |
| --- | --- | --- |
| A1 takeover trigger | Closed | Pre-takeover messages retain Telegram message ID/time, capture time, platform, media provenance, and hash-chain coverage; immediate-seal fallback is tested. |
| A2 observed identity | Closed | Display name, username, peer ID, and observation time flow through session state, history, replay, APIs, certificate, and PDF. |
| A3-A4 portable evidence | Closed | Every successful seal creates a PDF, detached signature, and `.evidence.zip` containing its public key and signed manifest. `hive-verify`/`python -m hive.verify_evidence` performs offline verification; tampering tests fail as expected. The panel and control bot expose the package. |
| A5 / UR-09 | Implemented, supervisor review pending | Live/archived session “Report” tab, PDF section, and control summary give conservative bank/NSRC 997/PDRM/preservation steps, link official sources, and state that HIVE does not report automatically. Guidance reviewed 22 August 2026. |
| B1-B5 red-team evaluation | Harness closed; full matrix pending | Runner now calls the real HIVE graph and records exchanges, duration, tiers, HVI precision/recall/F1, verdict accuracy, guardrail flags, explicit bot-assertion detection, sandbox outcomes, chain validity, and evidence verification. A versioned 15-scenario by four-persona command can produce 60 raw JSON/CSV runs. Retained evaluation/demo records exercise the implemented modes, but the complete matrix against configured cloud models still requires time and retained outputs. |
| C extraction corpus | Closed for synthetic benchmark | Expanded from 18 to 64 versioned synthetic cases: 37 English, 15 Mandarin, 11 Manglish, and one mixed-language case; includes 22 hard negatives, ten multi-message cases, and five media-derived cases. Overall precision/recall/F1 is 0.9821 with one deliberately retained Manglish name-boundary error. |
| Deterministic quality gate | Passed | 315 tests passed, one skipped after replacing obsolete session-memory tests with deterministic-context, privacy-reduced case-vector, and real readiness-query coverage; Ruff, extraction evaluation, JavaScript syntax, and Compose configuration pass. |
| Live local integration | Passed within the safe local scope | PostgreSQL exact-edge and Qdrant `hive_cases` write/search/cleanup checks passed; the obsolete empty `hive_sessions` collection was removed; a disposable Scrapling browser fetched a public page, rejected a local address, and cleaned up; all protected panel/API workspaces were checked. No external Telegram message was sent. |
| UAT and performance design | Draft complete; formal execution pending | `docs/HIVE_UAT_AND_PERFORMANCE_DRAFT.md` contains an execution-ready 35-case UAT checklist, Chapter 5 outline, acceptance/defect templates, metric definitions, provisional thresholds, and an exploratory performance baseline. |
| Static type gate | Open technical debt | The declared Python 3.11 target conflicts with NumPy 2.5 stubs using Python 3.12 syntax. Excluding that dependency boundary reveals 43 existing HIVE type errors under a pragmatic check (202 under current strict/no-site-packages diagnostics). Do not claim mypy passes. |

Work that cannot be completed without operator/supervisor involvement remains:

- controlled end-to-end Telegram message delivery and real image/QR attachment
  handling with an authorised test account;
- the complete 60-run model-driven red-team execution and retained raw outputs;
- ethics confirmation, participant recruitment, UAT/SUS, realism ratings, and
  blind detectability assessment;
- supervisor review of reporting and Section 90A wording;
- the final repeated performance/resource dataset (a pilot baseline is now
  recorded), backup-restore drill, privacy/retention decisions, responsive and
  accessibility checks, and final screenshots;
- Chapters 4-6, revised Chapters 1-3, abstract, appendices, poster, video, and
  final rendered-document review based on the frozen evaluated build.

### P0 - resolve before final evaluation

1. **Closed in the working tree: the full Objective 3 evaluation harness was incomplete.**
   `hive.redteam.runner` currently exercises persona reply generation and model routing, but it does not run the complete orchestration pipeline or calculate HVI extraction, verdict accuracy, engagement duration, detection rate, sandbox outcomes, or evidence-integrity metrics.

2. **Closed for the synthetic benchmark: the evaluation corpus was too small.**
   The deterministic extractor currently scores 100% precision, recall, and F1, but this is based on only 18 sanitised synthetic cases and 16 positive indicators. Expand it with more difficult English, Mandarin, and Manglish cases, multi-message disclosures, malformed indicators, adversarial negatives, QR/media cases, and anonymised real examples where ethics permits.

3. **UAT/SUS evidence has not been collected.**
   The structure guide requires at least two testing techniques and intended target users. Confirm that the ethics approval covers Part 2 testing before recruiting at least three participants.

4. **Implemented; supervisor review pending: UR-09 submission guidance was absent.**
   No user-facing PDRM/MCMC submission instructions were found in the application, evidence bundle, or README. Add a current, supervisor-approved reporting guide or mark the requirement as unmet.

5. **Closed: the triggering message could be omitted from the sealed case.**
   An inbound message received before takeover is stored only as an observed-chat preview. The event handler returns before adding it to the session, and `begin_takeover()` clears inbound state. Preserve the trigger message and its original Telegram metadata when takeover is approved.

6. **Closed: forensic identity metadata was not part of the sealed session model.**
   The transport observes display name and username, but `SessionState` and the PDF primarily retain only the peer ID. Preserve the observed identity, username, Telegram message timestamp/ID, capture time, and relevant profile metadata with clear provenance.

7. **Closed: evidence handoff was incomplete.**
   HIVE creates a detached `.sig`, but the panel downloads only the PDF. There is no user-facing export of the signature/public key and no verification command or interface. Provide one case package containing the PDF, signature, public key, manifest/checksums, and verification instructions or tool.

8. **Agent detectability is not measured.**
   Persona prompts are explicitly labelled first-draft scaffolding. Run blind human ratings or a controlled classifier study across all four personas, languages, scam archetypes, and pace settings.

### P1 - important implementation and validation work

9. **Live integration coverage is substantially improved but external Telegram UAT remains.**
   The offline suite is supplemented by recorded Demo Lab runs, deployed GLiNER/runtime readiness, PostgreSQL and Qdrant service round trips, cross-case scam-pattern retrieval, evidence verification, and successful public/private sandbox checks. A real approved Telegram trigger/reply/stop flow and media attachment still require a dedicated test account and participant/operator evidence.

10. **Static type checking remains open technical debt.**
    Mypy first stops in the installed NumPy 2.5 stubs because they contain Python 3.12 syntax while HIVE targets Python 3.11. A dependency-isolated diagnostic then exposes 43 existing HIVE errors under a pragmatic configuration, so this is not only an environment problem. Align the supported interpreter/dependency versions, establish a realistic typed boundary for third-party libraries, and reduce genuine HIVE errors without claiming a passing strict gate prematurely.

11. **Architecture corrected: Qdrant is for cross-case scam patterns, not active-session recall.**
    Active replies use the bounded transcript and deterministic validated facts. The deployed `hive_cases` index uses privacy-reduced pattern vectors; PostgreSQL remains authoritative for exact identifiers and case profiles. Qdrant results are candidate-only and cannot establish common ownership.
    Embedding compatibility is now controlled at prototype scope: FastEmbed is
    pinned, mean pooling is explicit, collection and point fingerprints prevent
    mixed-vector retrieval, and an audited PostgreSQL-to-Qdrant reindex verifies
    exact eligible-case coverage. A dimension-changing model migration remains
    a separately approved operational procedure.

12. **Privacy and retention controls are partially closed in report-only mode.**
    HIVE now inventories filesystem, PostgreSQL, and Qdrant artifact classes;
    exposes a data-flow disclosure; protects signed evidence, audit records, and
    active checkpoints; and audits configurable review thresholds without any
    deletion path. Supervisor-approved consent language, the final retention
    decision, per-case export, backup-aware secure disposal, and enforcement
    remain open before formal participant testing.

13. **The evidence claim needs external validation.**
    Cryptographic integrity tests cannot establish legal admissibility. Have the Section 90A certificate wording and handling procedure reviewed by the supervisor and, if feasible, a Malaysian legal or digital-forensics expert.

14. **Sandbox hardening remains prototype-grade.**
    The Compose deployment uses privileged Docker-in-Docker. Keep this as a disclosed limitation and test network filtering, private-address rejection, timeout cleanup, challenge handling, and recovery from a failed container. A hardened future design should use a dedicated VM or rootless isolation.

15. **A pilot performance baseline exists; the final repeated experiment remains.**
    The current draft records runtime startup, API percentiles, a live sandbox duration, retained demo/pipeline timings, evidence availability, resource snapshots, and extraction quality. Replace the superseded session-memory timings with cross-case indexing/search measurements and repeat the frozen protocol on fully documented hardware.

### P2 - polish and defensibility

16. **Audit backup-restore drill is closed at prototype scope.**
    A fresh 47,114-event ledger backup was restored into an isolated disposable
    target and passed manifest, checksum, source-identity, hash-chain, count, and
    terminal-hash checks without modifying live stores. Whole-system recovery
    across PostgreSQL, Qdrant, evidence files, and secrets remains future work.
17. **Operator-facing verification and key lifecycle are closed at prototype scope.**
    Portable packages include offline verification instructions and embedded
    public keys. Security now shows the active SHA-256 public-key fingerprint
    and supports audited, non-overwriting rotation only while the agent is
    stopped. HSM/KMS custody, revocation, scheduled rotation, legal hold, and
    secure retirement remain future hardening.
18. Run a manual accessibility and responsive-interface test in addition to the existing structural accessibility work.
19. Capture stable final screenshots only after feature freeze.
20. Ensure README, architecture diagrams, configuration examples, and the report all describe the same final feature set.

## Gap-closure execution plan

### Planning rule

Freeze the assessed feature set before testing. Only P0 work that affects requirements, evidence integrity, or Objective 3 should change the implementation after the freeze. Treat prototype hardening that cannot be completed safely as an evaluated limitation and Chapter 6 recommendation.

### Workstream A - forensic completeness

| Order | Task | Main change | Acceptance criterion |
| --- | --- | --- | --- |
| A1 | Preserve takeover trigger | Carry the observed triggering message into the new session instead of discarding it at approval. | The first sealed transcript entry contains the original message ID, Telegram timestamp, capture time, text/media hash, and is covered by the hash chain. |
| A2 | Preserve external-party identity | Add a provenance record for peer ID, observed display name, username, and observation time. | Identity metadata appears consistently in session history, audit records, and the evidence bundle without fabricating unavailable fields. |
| A3 | Package evidence for verification | Export the PDF, detached signature, public key, checksum manifest, and instructions together. | A recipient can verify an untampered package offline; modifying the PDF, manifest, or signature produces a failed verification result. |
| A4 | Add a verification interface | Provide a small CLI command or panel action that validates the package and reports each check. | Verification returns a clear pass/fail result and is covered by positive and tampering tests. |
| A5 | Resolve UR-09 | Add supervisor-approved reporting guidance to the session summary/evidence output, or formally mark UR-09 unmet. | The final traceability table points to an implemented screen/report section or records the requirement as not achieved. |

Dependency: complete A1 and A2 before generating final evaluation cases, because they change the evidence schema. Complete A3 and A4 before evidence-integrity testing.

### Workstream B - complete Objective 3 evaluation

| Order | Task | Main change | Acceptance criterion |
| --- | --- | --- | --- |
| B1 | Upgrade the red-team runner | Drive scammer messages through the real `HiveEngine` graph rather than calling only persona reply generation. | Each run exercises guardrails, extraction, sandbox stubs/controlled URLs, verdict, deterministic context, middleware, audit, case candidates, and sealing. |
| B2 | Define metrics | Calculate duration, exchanges, HVI counts, precision/recall/F1, verdict accuracy, false outcomes, bot probes, model tiers, and evidence verification. | Each run produces structured JSON/CSV with documented formulas and denominators. |
| B3 | Define detection rate | Use an observable measure such as explicit bot accusations, blind human classification, or both. | The report explains exactly what counts as detection and reports sample size and confidence/limitations. |
| B4 | Expand scenarios | Cover investment, job, parcel, impersonation, romance, e-commerce, and mixed/adversarial behaviours. | Every persona is tested against multiple scam archetypes and language conditions. |
| B5 | Produce reproducible outputs | Add a task command that fixes seeds/configuration and writes timestamped results. | Re-running the same deterministic subset gives the same metrics and preserves the raw result file for Appendix evidence. |

Dependency: complete Workstream A before final B5 evidence runs. The initial B1-B4 development may use synthetic metadata.

### Workstream C - strengthen the extraction benchmark

1. Design a balanced corpus matrix covering English, Mandarin, Manglish, multi-message context, positive indicators, hard negatives, malformed indicators, QR/media cases, and duplicates.
2. Expand from 18 cases to a practical minimum of 60-100 cases. Keep a separate development set and final evaluation set where possible.
3. Record provenance for every case: synthetic, sanitised research example, or anonymised participant material.
4. Report per-kind and per-language precision, recall, F1, false positives, and false negatives rather than only the overall score.
5. Retain difficult failures and discuss them; do not tune the corpus until it becomes trivially perfect.

Acceptance criterion: the final report includes the corpus composition, sample counts, scoring method, confusion/error analysis, and a reproducible result artifact.

### Workstream D - system and integration validation

Run the following in a controlled test-account environment and retain screenshots/logs without exposing credentials:

1. Setup wizard, Telethon login, 2FA, encrypted session reload, and control-bot authorisation.
2. Pending request, approve/reject, persona selection, takeover, burst-message handling, typing delay, stop/seal, and benign hand-back.
3. Real configured LLM routing and failure handling.
4. GLiNER cold/warm load and fallback to regex-only extraction.
5. PostgreSQL history/audit/case round trips and Qdrant scam-pattern candidate retrieval.
6. Sandbox success, blocked private target, redirect, timeout, challenge, failed container, and cleanup.
7. Media capture, QR/OCR, optional vision fallback, attachment hashing, and bundle rendering.
8. Evidence signature verification and deliberate tampering.

Acceptance criterion: maintain a test matrix with test ID, requirement, precondition, steps, expected result, actual result, evidence reference, tester, date, and pass/fail status.

### Workstream E - user evaluation

1. Confirm with the supervisor that the approved ethics form covers Part 2 UAT/SUS. Submit an amendment before recruitment if required.
2. Recruit at least three intended Malaysian Telegram users; target five to ten if feasible.
3. Give every participant the same controlled tasks: configure or inspect HIVE, approve a takeover, monitor a session, interpret indicators/verdict, stop and seal, locate the evidence, and explain how they would report it.
4. Collect task completion, errors, time-on-task, SUS responses, realism/detectability ratings, trust/privacy concerns, and short qualitative feedback.
5. Pseudonymise participants and keep consent/demographic records in the approved appendix format.

Acceptance criterion: Chapter 5 reports participant count, demographics, instrument, task results, SUS calculation, qualitative themes, limitations, and changes made from feedback.

### Workstream F - configuration and quality gate

1. Keep `HIVE_USE_CASE_SIMILARITY` consistent across `.env.example`, deployment and the panel; do not reintroduce active-message embeddings.
2. Record the validated `hive_cases` indexing/search result and candidate-only wording in the implementation, diagrams, evaluation chapter, and limitations.
3. Fix the reproducible mypy environment by pinning compatible dependencies/stubs or narrowing imported-package checking without suppressing HIVE errors.
4. Run the complete deterministic gate: lint, type check, tests, extraction evaluation, JavaScript syntax, and Compose configuration.
5. Record version identifiers for Python, dependencies, container images, models, test corpus, and the evaluated Git commit.

Acceptance criterion: one documented command sequence passes from a clean checkout, except for explicitly separated live-service checks.

### Workstream G - privacy, security, and operational documentation

1. Draw a data-flow map showing Telegram, the local host, PostgreSQL, Qdrant, filesystem evidence, text LLM, vision LLM, and sandbox network traffic.
2. State exactly which conversation/media data may leave the device and under which configuration.
3. Finalise the report-only policy in `docs/PRIVACY_AND_RETENTION.md` with
   supervisor-approved access, retention, backup, export, and secure-disposal
   decisions for the FYP evaluation dataset.
4. Keep the implemented audit-ledger restore drill in the final test gate and
   define a future whole-system recovery order for PostgreSQL, Qdrant, evidence
   files, and secrets.
5. Document privileged Docker-in-Docker as a prototype limitation; do not attempt a late architectural rewrite unless required for safe testing.
6. Obtain supervisor review of the Section 90A wording and avoid claiming guaranteed admissibility.

Acceptance criterion: the report and README contain consistent privacy, custody, threat-boundary, and limitation statements supported by the tests actually performed.

### Workstream H - documentation freeze

After the final evaluated build is tagged or committed:

1. Capture final screenshots and diagram inputs from that exact build.
2. Update the requirements traceability matrix with achieved, partially achieved, and unmet statuses.
3. Write Chapter 4 from the implemented design, Chapter 5 from retained test evidence, and Chapter 6 from the objective/requirement evaluation.
4. Synchronise README, `.env.example`, architecture diagrams, figure captions, appendices, and the final report.
5. Re-run the final quality gate after documentation-only changes and archive the test outputs with the submission materials.

### Recommended priority cut line

Must complete before claiming the objectives are achieved:

- A1-A5: forensic completeness, verification, and UR-09 disposition.
- B1-B5: full-pipeline evaluation and metrics.
- C: expanded extraction corpus.
- D: recorded controlled integration tests.
- E: ethics-compliant UAT/SUS.

Complete if time permits, otherwise document honestly as limitations:

- Cross-case scam-pattern candidate retrieval as an assessed feature.
- Rootless/dedicated-VM sandbox redesign.
- Approved, backup-aware retention enforcement and secure-disposal UI (the
  report-only inventory and review policy are implemented).
- Multi-platform support and Bahasa Malaysia.

### Gap plan schedule

- **22 Aug:** freeze scope; confirm ethics; specify evidence schema and metric definitions.
- **23 Aug:** implement and test A1-A4; decide and implement/record A5.
- **24 Aug:** upgrade the full-pipeline red-team runner and structured outputs.
- **25 Aug:** expand the extraction/scenario corpus and run dry evaluations.
- **26 Aug:** execute controlled live-service, Telegram, sandbox, and evidence tests.
- **27 Aug:** complete performance measurements and remaining integration evidence.
- **28 Aug:** run UAT/SUS and collect anonymised results.
- **29 Aug:** analyse technical and user results; lock tables/figures.
- **30 Aug:** finish Chapter 5 and Chapter 6; update Chapters 1-4.
- **31 Aug:** supervisor review, corrections, privacy/legal wording, and appendices.
- **1 Sep:** final documentation freeze, PDF/render inspection, poster/video/package preparation.
- **2 Sep:** final checks and submission before 11:00 p.m.

## Current verification baseline (23 August 2026, after gap implementation)

- Pytest: 352 passed, 1 skipped, 1 warning.
- Ruff: passed.
- JavaScript syntax: passed.
- Docker Compose configuration: passed.
- CPU-only backend dependency deployment: 789,796,786-byte image, down 76.3%
  from 3,337,709,323 bytes; deployed Torch `2.12.1+cpu`; real GLiNER and all
  runtime components ready.
- Dependency hygiene: yanked transitive `grpcio 1.82.0` and
  `charset-normalizer 3.4.8` releases replaced by deployed `1.83.0` and
  `3.5.1`; lock validation, the full regression suite, runtime health, and live
  PostgreSQL/Qdrant round trips passed.
- Deterministic HVI corpus: 64 cases, 55 true positives, 1 false positive, 1 false negative; precision/recall/F1 = 0.9821. The retained error is documented in the corpus and output.
- Mypy: not passing. The first blocker is Python 3.12 syntax in installed NumPy 2.5 stubs while the project targets Python 3.11; an isolated diagnostic also identifies existing HIVE type debt.

## Suggested schedule

- **22-23 Aug:** feature freeze decision, ethics confirmation, P0 fixes, and expanded evaluation design.
- **24-26 Aug:** Chapter 4 diagrams, database/interface documentation, implementation narrative, and screenshots.
- **27-29 Aug:** execute technical tests, controlled adversarial evaluation, and UAT/SUS.
- **29-30 Aug:** analyse results and write Chapter 5.
- **30-31 Aug:** write Chapter 6, revise Chapters 1-3, and rewrite the abstract.
- **1 Sep:** references, appendices, poster, cross-references, supervisor review, and final package preparation.
- **2 Sep:** final PDF inspection, code ZIP, video, and submission before 11:00 p.m.
