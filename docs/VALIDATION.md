# Validation and release status

Audit/test date: **28 October 2026**.

## Baseline inspection

- Repository baseline: `f0289211a2d98fa1b29bb4f04ed84f49b5d95184`.
- Existing public Render homepage responded HTTP 200 and displayed the legacy `HNI Ledger v0655` interface.
- Render configuration was inspected through the connected workspace, with its owner explicitly selected.
- No private records from the existing live service were accessed. No old application account was created, changed or deleted.

## Executed locally

- **47 backend tests passed** with a real temporary SQLite database.
- Coverage includes separate users, old public routes, cookie flags, idle expiry, recovery-code rotation, legacy-password upgrade, atomic/idempotent approvals, concurrent duplicate imports, legitimate repeated payments, private learning/rules, allocation totals, integer-paise financial identities, posted corrections, reconciliation gaps, filtered CSV/XLSX/PDF exports, model bootstrap, encrypted PDF/XLSX, legacy XLS and actual Tesseract OCR.
- Eight bank-labelled canonical workbook fixtures pass. These are authored fixtures, not proof of compatibility with every real bank's layouts.
- **3 frontend API-boundary tests passed**, covering authentication expiry, multipart requests and encoded filters.
- TypeScript compilation and Vite production build passed.
- **The full Playwright browser workflow passed** against the real API: signup, recovery-code acknowledgement, sample import, batch approval, ledger search, original evidence, PDF download, multipart file upload, personal rule, settings, mobile navigation and sign-out.
- Desktop and mobile screenshots are saved in `docs/screenshots/`.
- `render.yaml` passed the current official Render JSON Schema. Platform-aware CLI validation has not been run; the Blueprint has not been applied.
- Docker is not available in the local executor. The container build must also pass on the deployment host.

## Hosting boundary

The existing free Render service has ephemeral storage. Updating code does not provide permanent records. A paid persistent-disk configuration is included but has not been applied without billing approval.

The presence of a README link, screenshot, passing local test or accepted deploy request does not establish that a particular commit is live. Deployment confirmation must identify the actual running release.
