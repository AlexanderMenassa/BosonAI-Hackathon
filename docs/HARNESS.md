# Development harness

A harness is the repository's instructions, checks, and records that make development repeatable. This adapts the supplied Kira harness guide to one small Python hackathon project.

## What is implemented

| Layer | Repository mechanism | Enforcement |
| --- | --- | --- |
| Task scope | GitHub task template with acceptance checks and dependencies | Human/agent review |
| Shared context | `AGENTS.md`, thin `CLAUDE.md`, roadmap, decisions | Read by contributors; not a security boundary |
| Ownership | Engine, domain types, and storage each have one canonical module | Review and existing behavioral tests; no file locks |
| Verification | `scripts/verify.py` and GitHub Actions | Nonzero exit on failure |
| Capture | Timestamped local verification report; decision/handoff templates | Verification report automatic; authored notes manual |

There are no installed agent hooks, permission overrides, autonomous merge jobs, external orchestration plugins, or GitHub Projects requirements. CI runs after these files are pushed. Making its check required for merging needs repository branch protection; adding a workflow alone does not do that.

## One-task work loop

1. **Shape:** state the problem, outcome, likely files, dependencies, and Given/When/Then checks. Existing Phase 1 issues #1–#4 remain valid; no retroactive title migration is required.
2. **Inspect:** read relevant code/tests and prior decisions. Distinguish present behavior from roadmap targets.
3. **Implement:** keep the change focused; add regression tests when behavior changes. Document unavoidable larger scope.
4. **Review:** inspect the diff, including timeout/error handling, routing authority, data isolation, and whether tests establish the claimed behavior.
5. **Verify:** run the shared offline command. Run explicit live/browser checks only when relevant and available.
6. **Capture:** summarize results in the PR and record durable decisions or unfinished handoffs. Do not automatically publish private logs.

Use branches such as `codex/phase-1-baseline` when a new branch is needed. Issue prefixes such as `[FIX]`, `[FEATURE]`, and `[CHORE]` are optional organizational aids. Keep issue progress on GitHub and product phase scope in the README; avoid duplicating mutable status in multiple agent files.

## Local verification

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/verify.py
```

The command runs pytest, then launches the actual CLI for all three synthetic scenarios and a repeated blocked caller. CLI runs use a temporary working directory and clean provider configuration so the repository `.env`, existing database, and reports are not loaded. It checks persisted outcomes and blocking rather than trusting process exit status. The existing pytest suite includes local fake servers; it needs permission to bind loopback ports but no external provider connection.

A summary is written to ignored `artifacts/verification.json`, including timestamp, revision, dirty-worktree state, stages, and failures. This is evidence for that run, not a permanent certification of the current tree. Rerun after relevant changes. CI uploads the same summary even on failure. Console logs stay in the runner; secrets and real calls must never be test fixtures.

For targeted iteration use `python -m pytest tests/test_engine.py -q`, then run the shared command before completing a code change. No formatter or type checker is configured yet; do not claim those checks passed. `requirements.txt` currently uses lower bounds, not a reproducible lockfile. Dependency locking is a follow-up, not silently solved by CI.

## Live and browser verification

Follow the live commands in `README.md` after confirming credits and access. Record the actual model, whether fallback occurred, observed result, and redacted error details. A fake server, scripted agent, or heuristic fallback does not establish live-provider success.

Browser verification becomes required for browser features once a browser app exists. Check microphone permissions, audio round trip, interruptions, disconnects, late classifications, and visible simulated outcomes. Do not add placeholder browser checks that pass without exercising a browser.

## Context map

- `README.md`: product roadmap and target acceptance scenarios.
- `docs/ARCHITECTURE.md`: current code ownership and known gaps.
- `hangon/browser/`: browser rooms, signaling, and media adapters; `hangon/server.py` remains the launch entry point.
- `docs/decisions/`: durable choices and tradeoffs; start with `TEMPLATE.md`.
- `docs/handoffs/`: unfinished work, verification evidence, and next action.
- `.github/ISSUE_TEMPLATE/task.md`: implementation-ready task outline.
- `.github/pull_request_template.md`: reviewer-facing result and validation.

Use dated descriptive filenames for decisions/handoffs. Mark superseded notes and link replacements. No per-user memory files are modified by this harness.

## Adaptation from Kira

Preserved: narrow tasks, on-demand context, canonical ownership, staged review, and evidence capture. Simplified: one repo and two contributors do not need enterprise sprint-capacity formulas, mandatory Projects fields, many provider-specific skills, or separate migration services. Kira's completion percentages and line limits are not guarantees here. Automatic capture stores check results only; design decisions still need a deliberate written explanation.
