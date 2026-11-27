# Folio

A client document workspace: ask questions across one client's folder, open highlighted evidence, and review changes before accepting them.

[Live workspace](https://folio-client-workspace.vercel.app) · [Source](https://github.com/sivaabishikth2025-byte/folio-client-workspace)

Claude runs through the **Anthropic API** when `ANTHROPIC_API_KEY` is set (default model `claude-sonnet-5-5`), and through AWS Bedrock otherwise. The hosted demo uses AWS Bedrock (Claude Sonnet 4.6) for cost reasons; no Anthropic key is set there. The submitted source excludes private operational records, deployment-specific provisioning scripts and the original instruction/mock-up pack. The 17 fictional client documents remain unchanged as application data.

## Run locally

Install Node.js 20.19+ and Python 3.11+, then run:

```sh
npm start
```

The launcher creates a virtual environment, installs dependencies, copies `.env.example` to an ignored `.env`, builds the frontend and serves http://127.0.0.1:8000. The first run needs internet access.

**Claude through the Anthropic API (simplest):** put your key in `.env`:

```sh
ANTHROPIC_API_KEY=your-anthropic-api-key
```

Restart and ask a question. The model is `claude-sonnet-5-5` unless you set `ANTHROPIC_MODEL`. When `ANTHROPIC_API_KEY` is set it is always used; when it is not set, Folio uses AWS Bedrock.

**Or Claude through AWS Bedrock:** configure an authorized AWS CLI/SDK profile and set `AWS_BEDROCK_ENABLED=1` in `.env`. Defaults are `BEDROCK_REGION=us-east-1` and `BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-6`. Alternatively provide a Bedrock API credential through `AWS_BEARER_TOKEN_BEDROCK`; omit that variable entirely when using profile credentials. Do not set `FOLIO_AWS_ROLE_ARN` for local profile access. Keep all secrets in ignored local configuration or your hosting provider's secret manager. Never put credentials in source, chat, screenshots or the submission.

Without AWS configuration, document browsing/editing and memory remain available; chat reports a configuration error. Localhost can run without authentication for isolated development. Hosted deployments require Cognito.

## Workflows

- Select a client; its files, chats, memory and proposals stay isolated. Create an empty client from the client menu. Search files or conversations in the sidebar.
- Ask Northwind to compare the payment terms in its agreement and renewal notes. A cited answer automatically opens its supporting document. Numbered references and source cards select paragraphs, cells or PDF rectangles. Scanned PDFs use OCR line boxes when available; blank/unreadable scans and standalone images remain honestly labeled as whole-source evidence. Older saved answers retain their original citation detail.
- Ask for gross margin and overdue debtors. The workbook supplies calculated values and formulas, including its existing broken prior-year reference.
- Edit Word paragraphs or spreadsheet cells, review the before/after changes, then accept or reject. Spreadsheet downloads include calculated results alongside their formulas, so previewers can read totals without recalculating. This covers existing files, new uploads and accepted edits. The main Download action prepares these results; History’s Download stored file returns the exact saved bytes, including untouched originals. Native downloads preserve formulas, paragraph properties and immutable earlier versions. AI proposals and memory changes never apply automatically.
- Ask to fill a supplier form from the selected client's files. Unknown fields remain blank; expired certificates must not be treated as valid. Certificate attachment assembly is not implemented.
- Move a file to Trash, restore it with its versions, or confirm Empty Trash. Permanent cleanup targets exact owned versions and retains retry jobs if storage is unavailable. Old chat text, downloaded copies and backups are outside that deletion operation.
- Each chat row has a visible options menu; right-click and Shift+F10 also open it. Confirming deletion removes that conversation and its pending suggestions. Accepted edits and memory remain; model-use limits do not reset.

New questions clear old evidence. General answers open no unrelated file. Manually hiding/changing the panel or switching clients/conversations overrides delayed navigation. Mobile provides Back to answer. Drag desktop dividers to resize; arrow keys resize and double-click resets. Workspace settings offer Light, Dark and System appearance.

## Accounts and voice

Create an account, verify the code delivered to your email, then sign in. Resend, recovery, password changes and Sign out are provided. There are no published example login details or default reviewer credentials. Access details, if required, must be supplied separately.

Cognito access tokens use one-hour HttpOnly, Secure, SameSite=Strict cookies; the server stores session hashes and validates tokens. Refresh tokens are not retained. Sign out also invokes Cognito global sign-out. New accounts receive private state. Optional explicitly configured Cognito subject IDs may share an assessment workspace; this is not a team-role system.

Dictate requests microphone permission and uses Transcribe to put editable English text into the composer, without sending it automatically. Recording is limited to two minutes and 8 MB, with PCM duration checked server-side. Read aloud uses Polly and caps each request at 3,000 cleaned characters. Stop controls are included. Synthetic transcription and playback were tested; physical microphone capture was not.

## Architecture and hosting

React/Vite provides the UI; FastAPI handles authorization, documents and persistence. Bedrock ConverseStream uses constrained JSON with selected-client source identifiers and independent server validation. Selected-client source text, formulas, fields and images are included within explicit context limits. Large contexts fail clearly instead of silently omitting evidence.

Read answers stream from one generation. Actual edit/memory proposals receive an additional evidence review before the user can accept them. Citation actions activate only after validation. Input caching can reuse unchanged document context for five minutes; completed answers are not cached across accounts. Accuracy still requires human review.

SQLite and immutable files provide local persistence. Production uses private Vercel Blob with conditional writes, private versioned S3 uploads and Cognito. Deploy this directory. Configure `BLOB_READ_WRITE_TOKEN`, `COGNITO_CLIENT_ID`, `FOLIO_S3_BUCKET`, `BEDROCK_REGION`, `BEDROCK_MODEL_ID` and `FOLIO_AWS_ROLE_ARN` through Vercel. Provision the corresponding services with an authorized administrator; this source does not contain account-specific infrastructure definitions. Restrict OIDC trust to the intended team/project/environment, and scope IAM to the selected model and owned storage prefixes. S3 must retain public-access blocking, encryption, versioning, TLS enforcement and exact-origin CORS. For scan line highlights, also set `FOLIO_OCR_ENABLED=1` and grant the server role only `textract:DetectDocumentText` in the selected region. This API requires a wildcard resource; restrict it with `aws:RequestedRegion`. No permanent AWS key is required on Vercel. Locally, installed Tesseract supplies line boxes without Textract; otherwise enable Textract through the authorized SDK profile. A Bedrock API credential alone cannot authorize uploads, speech or Textract.

Run `vercel --prod`, then verify authentication, native document workflows, uploads and speech. Hosted writes fail closed without durable Blob configuration. Direct S3 uploads support ten files at 50 MB each, within 100 files/1 GB per client. Local uploads also support 50 MB; the legacy hosted proxy is limited to 4 MB without S3. Upload grants constrain the key and exact bytes; completion checks an inspected object version. Capacity does not guarantee that every large file fits parser/model bounds. Prepared spreadsheet exports above 4 MB use a private, 60-second download link under the existing `incoming/` prefix, avoiding Vercel’s response-size limit. Configure one-day expiration, including old object versions, on that prefix and the audio prefixes. OCR processes at most 20 scanned pages per document/question, uses confidence-filtered line boxes and sends page images to Claude for cross-checking. Successful small-document parses are cached in memory; failed OCR calls can retry and fall back to visual reading without claiming a line highlight. A cold scan can add latency. AWS services, including Textract, incur usage charges.

## Checks and limits

```sh
npm test
npm run build
```

The checkpoint passed 110 Python tests (including a local run with only `ANTHROPIC_API_KEY` set, against a simulated Anthropic API), six JavaScript tests and 14 deterministic browser scenarios. A real Textract check located the scanned short-delivery line. Export tests read saved values with `openpyxl(data_only=True)`: `Income statement!B8 = 0.3499015576323988`, `Debtors!C9 = 717505.75`, with formulas and untouched source files preserved. Live authenticated checks also covered a new scan upload and actual Claude line citations, a new 5 MB S3 workbook, accepted edits with updated saved results, and exact original/history downloads. Original client states stayed unchanged. Native phone and email preview apps were not directly tested. Tests cover account/client boundaries, same-origin mutations, stale edits, upload replay, session revocation, streaming/deletion races and durable cleanup. Browser tests use a labeled fixture model, fixture OCR coordinates and real document parsers: start `.venv/Scripts/python scripts/run-evidence-ui.py`, then `node scripts/check-evidence-ui.mjs` with Chrome installed. Test outputs stay in ignored `test-results/`.

The opt-in `scripts/acceptance.py` runner makes billable AWS requests and accepts proposals only against a separate localhost server. Use a fresh `DATA_DIR`; never use working state. Supply any needed test credentials through environment variables. Tests generate synthetic login identities instead of distributing reusable examples.

These checks are not a penetration-test certification. MFA, fine-grained team roles, parser sandboxing, load testing and broader semantic/adversarial evaluation remain future work. Unsupported formulas are exposed, not guessed. Edited Word paragraphs normalize mixed inline styles; unchanged paragraphs preserve formatting. PDF attachment assembly is absent. Explicit review is still necessary for factual correctness.

Received Thursday November 26, 2026, 9:31 PM CT. I started on November 27, 2026, 01:18 CST; the finish time is in NOTES.md. I kept polishing after the core was working, so the total time ran past the suggested eight hours. NOTES.md summarizes the hand-in. `python scripts/package.py` produces the submission ZIP and per-file SHA-256 manifest from an explicit source allowlist.
