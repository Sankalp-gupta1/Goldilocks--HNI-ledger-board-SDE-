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
- Docker was unavailable locally. The multi-stage Docker build passed on Render, and the live health endpoint confirms Tesseract is available.

## GitHub and live release

- Both push and pull-request GitHub Actions checks passed. [Verified workflow](https://github.com/Sankalp-gupta1/Goldilocks--HNI-ledger-board-SDE-/actions/runs/40731907237).
- Live application: **https://goldilocks-workspace.onrender.com**.
- Render service: `srv-dbh2h4bl550s73eq0li0`; Docker, Free plan, one instance.
- The initial release `558255ed48ba4f104efd52487c8fcb76fc92eae6` became live in deploy `dep-dbh2h53l550s73eq0mo0`.
- The full browser workflow passed against that public HTTPS URL, including real signup, source import, posting, evidence, PDF download, mobile navigation and logout.
- A separate live check created two fictional accounts. The second could not read the first account's queue, transaction evidence or original file. Anonymous reads returned 401; a mutation without the required header returned 403.
- Live posting produced balanced journals and expected income/expenditure; original-file bytes matched the uploaded fixture. Secure, HttpOnly and SameSite cookie flags were confirmed, and logout revoked both sessions.
- Browser verification used the executor's network proxy. Its embedded Chromium required a local certificate-trust override; independent HTTPS requests succeeded with normal system certificate validation. No certificate bypass exists in the application.
- A small banner/sidebar overlap discovered during the live visual check was fixed in `4bd1dde86647872040b18af087c1733a10b58dd2`. Render deploy `dep-dbh2mpou01pc73avqq8g` reached `live` on 28 October 2026 at 22:36 IST with that exact commit. The served HTML references the newly built CSS asset, and the HTTPS health check succeeds. The full live Playwright workflow was then rerun and passed again; the committed desktop/mobile screenshots come from this final deployment.

## Hosting boundary

The existing free Render service has ephemeral storage. Updating code does not provide permanent records. A paid persistent-disk configuration is included but has not been applied without billing approval.

The presence of a README link, screenshot, passing local test or accepted deploy request does not establish that a particular commit is live. Deployment confirmation must identify the actual running release.
