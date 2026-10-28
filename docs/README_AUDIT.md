# Original README audit

Audit date: 28 October 2026. Baseline commit: `f0289211a2d98fa1b29bb4f04ed84f49b5d95184`.

The original project contained a large financial engine and useful domain rules. Several advertised capabilities were only partly wired into the application. The audit compared the README, active request handlers, schema and live public landing page. No private records from the old live server were read.

| Original claim | What the baseline actually did | Version 2 change / evidence |
| --- | --- | --- |
| Multi-bank PDF and Excel imports | Many helpers existed, but the active path was heavily layout-specific; password-protected Excel was rejected. | New bounded parser dispatch, correct-password PDF/Office decryption, XLS/XLSX/CSV and multi-page PDF tests. Eight bank-labelled tabular fixtures pass; arbitrary bank layouts remain unsupported. |
| Scanned banking documents | OCR helpers were present but not reliably connected to the active upload route. | Scanned PDF detection invokes real Tesseract OCR. An image-only PDF regression test confirms extraction. Docker installs the OCR executable. |
| ML fallback | Model training depended on absent local/private workbook or dataset paths. | A reproducible bootstrap trains TF-IDF + calibrated LinearSVC from the repository's authored fictional generator. No uploaded statement trains the shared model. |
| Learning from corrections | Shared correction files could pool users' private narrations. | Reviewed examples and personal rules are stored with a user ID and queried within that account only. Exact reviewed examples have priority. |
| Personal classification rules | Rule storage and the active classifier were not consistently connected. | Rules affect future imports; regression tests verify matching, correction precedence and isolation. |
| Review before posting | Uploads could clear the existing pending queue, and posting did not guarantee a whole-selection transaction. | Imports preserve other pending work. Approval validates all rows, inserts ledger/journals/status changes and commits once. Repeating an approval does not duplicate it. |
| Duplicate handling | Business-field unique indexes collapsed legitimate same-value repeated payments; startup cleanup could delete similar historical rows. | Exact file hashes and occurrence-aware fingerprints replace the lossy indexes. Startup never deletes historical payments. Duplicate/restart/concurrency tests protect this behaviour. |
| Balance verification | Raw running balances could be discarded, and reports did not persist independent balanced journal lines. | Original balances survive ingestion. Integer-paise journals and running-balance checks are independent. Confirmed and inferred openings have different statuses. |
| Correct financial statements | Full EMI/card settlements could become expenditure; missing initial holdings distort net worth. | EMI review requires an explicit principal/interest split. Card settlements reduce liabilities. Opening assets/debt are editable. Results are labelled recorded-cost rather than true market net worth. |
| Trading receivables/payables | Useful broker cash-flow categories existed, but transfers could be mistaken for portfolio performance. | Broker movements share a net cash account. Negative broker balance is presented as a payable. The UI explicitly says this is not market portfolio value or realised trading profit. |
| Source traceability | Some original row/balance details were lost, and old file downloads lacked a reliable authentication boundary. | New imports atomically retain original bytes, source rows, file hashes, classification and review events. Downloads check ownership. Missing historical evidence is disclosed. |
| Year/account reporting | Existing text-report helpers existed but were not a coherent interactive workspace. | Shared account/date filters, a posted ledger, charts, income statement, balance sheet and CSV/XLSX/PDF exports. Reports test as-of balances separately from period income. |
| Local processing | No external model was needed, but hosted-processing privacy language was unclear. | No external LLM/financial API. The security guide distinguishes processing on the server from keeping data on the user's device. |
| Enterprise-grade / audit-grade | A process-global signed-in user and public reset token flow made those claims unsafe. | Per-browser opaque sessions, ownership checks, scrypt, recovery-code reset, CSRF protection, secure cookies and tests. The old public handlers are removed. No claim of security certification remains. |
| Modern interface | A large inline HTML/JavaScript page lived in the Python file. | React 19 + TypeScript, data caching, interactive tables, charts, accessible dialogs and a responsive custom design; real API-driven values. |
| Production hosting | The inspected Render service used a free ephemeral filesystem with SQLite. | Durable Docker/Render configuration and a SQLite backup script are provided. A paid persistent disk still requires explicit billing approval and deployment; code alone cannot make a free disk durable. |

The original “Future Roadmap” was not current functionality. Tax automation, trade-lot capital gains, market-price portfolio tracking and family-office consolidation remain future work. They are not simulated or advertised as complete.

Tests live in `tests/test_workflows.py`, `tests/test_ingestion.py` and `frontend/tests/workspace.e2e.ts`. See `VALIDATION.md` for the exact executed checks.
