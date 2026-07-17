# Changelog

This file records release-level behavior changes. Individual fixes and implementation details remain available through `git log`.

## Unreleased

### Changed

- Consolidated active documentation around the architecture Wiki and removed completed audit, benchmark, and phase-plan reports from the working tree.
- Removed duplicate pytest collection through the legacy invariant re-export module and documented the canonical test layout.
- Classified supported maintenance scripts and removed one-off benchmark and migration drivers.

## 2026-07

### Added

- Long-form continuity support: structured worldbuilding snapshots, foreshadowing operations, cross-arc memory inheritance, and final-chapter handling.
- A zero-cost chapter rule checker feeding the six-dimension LLM quality review.
- Multi-user authentication hardening, project ownership isolation, login rate limiting, and production startup validation.
- Atomic persistence, backup/restore support, corruption recovery, and cross-storage reconciliation tools.

### Changed

- Unified the standalone writing engine into `backend/engine` and made the web bridge its supported execution path.
- Split structural invariant tests by domain and made test path discovery independent of the current working directory.
- Standardized the backend development port on `8132` and the frontend development port on `5293`.

For earlier detail, use `git log --all -- CHANGELOG.md` or inspect the relevant source file history.
