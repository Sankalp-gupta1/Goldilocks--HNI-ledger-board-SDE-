# Security and data handling

## Where data goes

The browser sends the document to the server running this app. That server parses the file, stores its original bytes in SQLite and runs classification locally. No external LLM, bank API or market-data API is called. Fonts and browser assets are served by the application.

When you run it on your laptop, the server is your laptop. When you open a Render URL, Render hosts the server and storage. Authorised host/database administrators can access stored information. This is **not end-to-end encryption** and there is no promise that a breach is impossible.

## What is implemented

- New passwords use Werkzeug's scrypt hashing. Legacy PBKDF2 passwords can be verified and upgraded after a successful login.
- Session cookies contain random opaque tokens; the database holds their SHA-256 digests. Cookies are HttpOnly and SameSite=Strict, and Secure in production.
- Sessions expire after 30 minutes idle or seven days absolute. Logout revokes the current session; recovery revokes all of that account's sessions.
- Account recovery requires a high-entropy saved code, not just an email address. Only its hash is stored. A successful reset rotates the code. No reset token is returned just because someone knows an email.
- API ownership checks cover row reads, review changes, approvals, sources, rules, exports and reports. Batch approval rejects an inaccessible ID before posting any selected row.
- Mutations require a custom header and reject a mismatched Origin. There is no cross-origin API allowance.
- Login, signup, reset and upload rates are bounded. Uploads have file/page/row/storage limits; ZIP-expanded XLSX contents are bounded.
- Source downloads use owned database records and attachment responses, not user-controlled filesystem paths.
- CSP, anti-framing, no-sniff, no-referrer and production HSTS headers are set. Private responses use `Cache-Control: private, no-store`.
- CSV/XLSX exports neutralise formula execution. Rendered narrations use React text escaping, and report PDF text is escaped.
- Legacy financial debug prints are disabled. Unexpected API errors return a request ID and generic message, not stack traces or document content.
- Personal rules and reviewed examples are isolated. No shared private correction CSV is created.

## What still needs operational care

- Use HTTPS. Secure production cookies are intentionally not sent over plain HTTP.
- A custom data-directory environment variable does not make a cloud disk persistent. Attach and verify the actual volume.
- SQLite and source documents are not encrypted by the application. Protect the host and backups. A Render persistent disk provides provider-managed encryption at rest; verify the deployed plan/configuration rather than assuming it.
- Use a consistent database backup, stored outside the instance with restricted access. A source-code rollback does not roll back a database migration.
- No MFA, email ownership verification, malware scanner, independent penetration test or cryptographically tamper-evident audit chain is included.
- A saved account email is an identifier, not proof of ownership. Do not reuse important passwords in a preview.
- Recovery codes must be saved when shown. Existing legacy accounts without one need an administrator-assisted, independently verified reset; the insecure old email/phone-only reset is removed.
- Scans and model classifications require review. Security controls do not make financial interpretation automatically correct.

Report vulnerabilities privately to the repository owner. Do not publish real statements, credentials, cookies, recovery codes or personal details in a GitHub issue.
