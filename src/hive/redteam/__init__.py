"""Adversarial evaluation harness (fyp.txt S9).

A red-team LLM plays the scammer across archetypes; the conversation runner
pits it against the HIVE agent and records metrics (extraction, character consistency,
detection-evasion, verdict accuracy). Original contribution — no reference
framework provides this. Lives in `hive.` (not tests/) so it can also be run
as a manual demo tool.
"""
