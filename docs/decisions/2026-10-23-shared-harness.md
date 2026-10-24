# Decision: one shared harness for the Python prototype

Date: 2026-10-23
Status: accepted for this implementation
Related work: repository harness requested using the supplied Kira guide

## Context

The repository is a small Python call-screening prototype with an existing test suite and a browser-first roadmap. Separate, duplicated instructions for different agents would drift. Enterprise project-management and deployment machinery would add overhead without validating the current product.

## Decision

Keep contributor rules in `AGENTS.md`, with `CLAUDE.md` pointing to them. Keep the roadmap in `README.md` and durable engineering context in `docs/`. Use `python scripts/verify.py` locally and in CI for pytest plus isolated CLI scenario checks. Capture a timestamped local summary automatically; write decisions and handoffs deliberately.

## Alternatives and consequences

We did not copy Kira's runtime, Projects-only metadata, fixed task-size limits, multi-agent orchestration, permission overrides, or merge/release automation. These are not prerequisites for this project. Rough task-size targets guide decomposition without forcing fragmented fixes. No new application dependencies were added. Live providers remain separate from CI, so offline success does not establish service access or response quality.

## Evidence

The initial verification attempt could not bind local ports for existing fake-server tests. Rerunning with local test-server access passed all 39 tests and all four CLI runs (three scenarios plus blocked repeat caller). No live model calls were made. GitHub Actions has not run yet; it becomes active after publication. The exact local run summary is generated in ignored `artifacts/verification.json`.
