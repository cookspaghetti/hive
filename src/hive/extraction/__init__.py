"""Layer 3 — Extraction & Forensic Engine (fyp.txt L3).

Harvests High-Value Indicators (HVIs) from text (GLiNER2 + regex) and media
(local QR decode first, vision model fallback). Each HVI is stored in the
vault and feeds the Verdict Engine as a hard signal.
"""
