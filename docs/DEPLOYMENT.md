# Deployment and backups

## Inspected existing deployment

On 28 October 2026 the connected Render workspace reported:

- Service: `Goldilocks--HNI-ledger-board-SDE-`.
- Runtime: Python; branch: `main`; auto-deploy on commit.
- Build: `pip install -r requirements.txt`.
- Start: `python hni_accounting_system.py`.
- Plan: Free; no persistent disk reported.

The version 2 startup command remains compatible. In production it starts Gunicorn, and the built React files are served from `web/`. This does **not** solve ephemeral storage by itself.

## Durable deployment

Use the included Dockerfile: it installs Python dependencies and Tesseract, builds React and runs the app as a non-root user. Use one service instance.

The `render.yaml` Blueprint describes a **new paid service**, not a silent replacement of the current free service:

- Compute: `0.5c-512mb`.
- Disk: 1 GB mounted at `/var/data`.
- Database: `/var/data/hni_ledger.db`.
- `APP_ENV=production`; HTTPS Secure cookies.
- Health check: `/api/health`.
- One Gunicorn worker with two threads; no external LLM keys.

Review Render's current compute, disk and usage pricing before applying. The file does not authorise a charge. Larger uploads or heavy concurrent OCR can require more memory and a job queue.

A directory alone is not evidence of durability. After mounting the disk, create a fictional test account/import, restart the service, sign in again and verify its records survive. Also test a backup restore in an isolated copy. The health endpoint reports configuration, not a guarantee that the provider has mounted the correct disk.

## Existing data: back up first

Never assume the old free service contains no records. Do not redeploy it just to retrieve its SQLite file: the redeploy can erase that same file. Free Render services cannot attach a persistent disk and may lose files on idle spin-down too.

If an existing database is still accessible through an authorised host shell, make a consistent backup **before** the release:

```bash
python scripts/backup.py /path/to/hni_ledger.db /secure-backup/pre-v2.sqlite
```

The script uses SQLite's online backup API, checks database integrity and refuses to overwrite an existing file. Copy the backup and any legacy `uploaded_files/` directory into protected durable storage. Do not commit either to GitHub.

For migration, start with a copy of the backup on the new durable volume, point `HNI_DB_PATH` at it and start version 2. Migration adds new tables, removes lossy duplicate indexes and creates journals for old valid approved rows. It does not delete similar-looking payments. Old classifications are preserved; review historical EMI/card classifications and opening positions before treating the reports as complete. Original files missing from the legacy database cannot be recreated.

Verify counts, balances, user login and representative owned entries before directing users to the new deployment. Keep the pre-upgrade backup for rollback. The older application does not know the new journal tables, so restoring only old code is not a complete rollback procedure.

## Python-only hosting

If using a Python-native service, keep the existing build/start commands or use:

```bash
gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 180
```

Set `APP_ENV=production` and a genuinely persistent `HNI_DATA_DIR`/`HNI_DB_PATH`. Tesseract must be provided by the host; adding `pytesseract` to pip requirements does not install the OCR executable. Docker is the reproducible option.

## Verification after release

1. Check the exact Render deploy ID and source commit until status is `live`.
2. Verify `/api/health` reports `2.0.0`, and `/` serves the React application.
3. Use only fictional files for signup → upload → review → post → report checks.
4. Test two separate users to confirm isolation.
5. Confirm OCR availability, HTTPS cookies and original-file downloads.
6. Test restart persistence and a restored backup before calling storage durable.

Official references used: [deployments](https://render.com/docs/deploys), [free-service limits](https://render.com/docs/free), [persistent disks](https://render.com/docs/disks), [health checks](https://render.com/docs/health-checks), [Blueprint specification](https://render.com/docs/blueprint-spec).
