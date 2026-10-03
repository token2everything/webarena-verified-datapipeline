# WebArena-Verified Generated Pilot

This directory owns authoring and validation for generated WebArena-Verified
tasks. It is a pilot workspace, not an upload route. Nothing here may be
promoted to training until a human accepts the machine-validation packet.

## Hard boundaries

- The official 812 tasks are evaluation-only. Generation must not read their
  intents, templates, instantiation values, or evaluator payloads.
- The original WebArena `test.raw.json` is also forbidden as a concrete
  exemplar. Authoring may use only the abstract style rules recorded here.
- The official catalog is available only to the post-generation contamination
  checker, pinned by SHA-256.
- WebHarbor semantic seeds do not share state or adapters with the six Verified
  sites. A migrated pilot task is therefore `regrounded_semantic_seed`: only
  its task family is reused; target facts, GT, and evaluators are rebuilt from
  a pinned Verified baseline.
- Public tasks contain no evaluator expectations, instantiation values,
  credentials, cookies, database oracle, HAR, or trace data.
- P0 tasks are single-site and use only `shopping`, `shopping_admin`, `reddit`,
  `gitlab`, `wikipedia`, or `map`.
- Generated integer task IDs and template IDs start at `1_000_000`; official
  IDs `0..811` are rejected.

## Promotion gate

A candidate remains private/quarantined unless all of these pass:

1. upstream `WebArenaVerifiedTask.model_validate`;
2. immutable baseline and grounding receipts match the task-spec hash;
3. positive adapter/reference replay passes every evaluator;
4. wrong response and do-nothing negative tests fail;
5. HAR and Playwright trace produce the same evaluator result;
6. the official-catalog contamination checker returns `pass`;
7. public/private projection and credential-leak checks pass;
8. canonical stage and target-model `export_sft` smoke pass;
9. a human reviews the query, screenshot/trace summary, GT summary, and all
   receipts.

Semantic model failures are retained as failures. Only proven infrastructure
failures may receive a fresh attempt.

