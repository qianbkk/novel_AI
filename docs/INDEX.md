# Documentation

This directory documents the current system. Historical implementation plans, completed audits, and benchmark transcripts belong in Git history rather than the active documentation tree.

## Start here

- [Wiki home](wiki/00-Home.md): system overview and reading order.
- [Architecture](wiki/01-Architecture.md): process boundaries and request lifecycle.
- [Backend API](wiki/02-Backend-API.md): FastAPI routes and contracts.
- [Writing engine](wiki/03-Writing-Engine.md): orchestration, agents, memory, and quality gates.
- [Frontend](wiki/04-Frontend.md): React application structure and data flow.
- [Data model](wiki/05-Data-Model.md): persistence and entity relationships.
- [Development](wiki/06-Dev-Setup.md): local setup, scripts, tests, and deployment.
- [Architecture quick reference](wiki/ARCHITECTURE.md): concise operational view and invariants.

## Maintenance policy

- Update the owning document when behavior changes; do not create a new phase or iteration report.
- Keep one source of truth per subject. Link to it instead of copying sections between files.
- Describe the current state in present tense. Use Git history for previous designs and completed investigations.
- Put temporary drafts under `docs/drafts/` and benchmark output under `docs/runs/`; both are ignored.
- Do not record commit hashes or manually maintained “last updated” fields in documentation.
- Remove stale instructions in the same commit that removes or renames the referenced code.

The root [README](../README.md) remains the product and setup entry point. The root [CHANGELOG](../CHANGELOG.md) records only release-level changes, not every commit.
