# HIVE extraction knowledge

HIVE uses `urchade/gliner_multi-v2.1` as a zero-shot multilingual NER model. The
model does not learn from live conversations. Runtime domain knowledge is kept
separately in `src/hive/extraction/knowledge.yaml`, validated on import, and
combined with deterministic regex and contextual extraction.

## Knowledge registry

The YAML registry is the source of truth for:

- the default GLiNER model and global threshold;
- GLiNER labels, HIVE indicator kinds, and per-label acceptance thresholds;
- Malaysian bank canonical names and aliases;
- bank-account digit bounds, context terms, and explicit negative contexts;
- person-name stop words, Manglish boundary particles, and English/Mandarin
  introduction phrases.

Each registry change must update `knowledge_version`, include focused positive
and hard-negative tests, and pass `task evaluate`. Analysis metadata records both
the version and SHA-256 of the exact YAML bytes so archived and replayed results
can identify the knowledge used.

Structural formats remain implemented as deterministic Python regexes. The YAML
stores domain vocabulary and calibrated policy rather than executable regular
expressions. This keeps configuration reviewable and prevents configuration from
becoming an arbitrary-code path.

## Updating knowledge safely

1. Add only reviewed, non-secret, generalisable knowledge. Do not add live names,
   account numbers, phone numbers, tokens, or other case-specific identifiers.
2. Increment `knowledge_version`.
3. Add positive and adversarial-negative examples under `tests/` and, where
   appropriate, the sanitised `evaluation/indicator_cases.json` corpus.
4. Compare per-kind and per-language precision, recall, and F1. A global score
   must not hide a regression in a rare indicator type.
5. Commit the YAML, tests, code, and resulting documentation together.

The registry is loaded once per process. A production change therefore requires
a backend restart. Invalid schemas, duplicate labels, invalid thresholds, and
invalid numeric bounds fail startup instead of silently falling back.

## Known boundaries

- YAML knowledge improves validation and consistent domain handling; it does not
  change the pretrained model weights.
- Regex remains authoritative for strongly structured indicators such as URLs,
  Malaysian phone numbers, cryptocurrency addresses, and Telegram handles.
- GLiNER remains useful for fuzzy entities such as people, organisations, banks,
  and locations, subject to deterministic validation.
- Qdrant case similarity is separate investigative retrieval. It must not be
  treated as GLiNER training data or proof of identity or common ownership.

## Future enhancement: fine-tuning

Fine-tuning is deliberately deferred. A future version may store reviewed,
anonymised operator corrections as a versioned labelled dataset, split it by
conversation into training/validation/untouched test sets, and train a separate
HIVE-specific GLiNER checkpoint. Such a checkpoint must run in shadow mode and
beat the approved baseline per entity type and language before deployment. Live
operator corrections must never update model weights automatically.
