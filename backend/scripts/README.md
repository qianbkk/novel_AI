# Maintenance Scripts

Scripts are CLI entry points, not a storage area for experiment code.

## Supported

- `run_mvp.py`: run the complete bridge workflow for an existing project.
- `audit_project.py`: verify cross-storage project invariants.
- `backup_cli.py`: create and inspect database backups.
- `cleanup_test_projects.py`: remove test project data.
- `export_openapi.py`: export the backend OpenAPI schema.
- `generate_master_key.py` / `rotate_master_key.py`: manage provider encryption keys.
- `reconcile_storage.py`: compare database and engine output state.

## Manual repair and diagnostics

- `monitor_run.py`: monitor a long-running local engine job.
- `rewrite_length.py`: repair chapter lengths with an LLM.
- `strip_chapter_headers.py`: inspect explicitly named chapter directories and, with `--apply`, clean legacy headers.

Manual repair scripts must support an explicit target, avoid hard-coded project IDs, and default to a non-destructive or dry-run mode where practical.

Example header cleanup (the first command only previews):

```powershell
python -m scripts.strip_chapter_headers --chapters-dir data/engine/output/chapters
python -m scripts.strip_chapter_headers --chapters-dir data/engine/output/chapters --apply
```

One-off benchmark and migration scripts should use a local `local_*.py` filename and must not be committed. Delete them after the investigation is complete; durable behavior belongs in the application or test suite.
