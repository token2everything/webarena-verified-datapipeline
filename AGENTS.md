# WebArena-Verified pipeline execution contract

- Keep the existing script owners and task vocabulary. Read the owning module before editing.
- The source of truth for the frozen v0.2 pilot is `releases/v0.2.0-pilot/source-manifest.json`.
- Do not change frozen release receipts when changing current code. Record subsequent releases separately.
- Do not stage, commit, push, or publish without an explicit user instruction.
- Do not modify installed dependencies, generated caches, or resources belonging to other sessions.
- Python execution uses `uv run`; disable bytecode and pytest caching during verification.
- Official 812-task payloads are post-generation contamination inputs only. Never use them as authoring examples.
- Public tasks must contain no private evaluator payloads, GT, credentials, storage state, HAR, or trace.
- Tasks are single-site. Generated numeric task and template IDs must follow `contract.py`, currently at least 2,000,000.
- Reference execution receives only the public task; private scoring remains outside model input.
- Retain semantic failures and historical attempts. A fresh attempt needs a proven execution or infrastructure defect.
- Human review, canonical stage and target-model export must pass before training promotion.
- Run the existing pipeline suite, isolated evaluator suite, and relevant runtime tests for behavioral changes.
- Preserve the frozen original AGENTS.md in the pipeline source snapshot. Its old numeric ID floor is superseded by the current code contract above.
