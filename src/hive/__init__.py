"""HIVE — Honeypot for Intelligence, Verdict & Evidence.

A deceptive anti-scam agent that takes over a Telegram conversation with a
suspected scammer, wastes their time in a believable persona, harvests
High-Value Indicators, safely analyses malicious links, and produces a
tamper-evident forensic evidence bundle.

Layer map (see fyp.txt):
    L1 middleware  — human emulation (typos, delays)
    L2 agent       — deceptive ReAct engine + personas
    L3 extraction  — GLiNER2 + regex + media pipeline
    L4 sandbox     — disposable Playwright-in-Docker URL analysis
    L5 vault       — SHA-256 hash chain + RSA signature + PDF bundle
    S6 verdict     — continuous hybrid scam scoring
    S7 guardrails  — prompt-injection defence
    S8 orchestrator— session lifecycle state machine
"""

__version__ = "0.1.0"
