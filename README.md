# Folio

Folio is a workspace for an accounting firm. Pick a client, browse their folder, and ask Claude questions about it. Every answer shows where it came from, and nothing in a file changes until you accept it.

**Live:** https://folio-client-workspace.vercel.app (create an account with your email to sign in)

## What it does

- **One client at a time.** Claude only sees the client you've picked. Asking Northwind about Ostara's payroll gets a clear "I can't see that".
- **Answers with sources.** Each answer links to the exact paragraph, spreadsheet cell or PDF line it used, and opens that document beside the chat with the passage highlighted. Scanned PDFs highlight the matching line.
- **General questions are labelled.** "What's the VAT rate?" is answered from general knowledge and tagged that way, with no client files cited.
- **Edits you approve.** Change Excel cells or Word paragraphs yourself, or ask Claude ("add a column with VAT at 15% next to each amount"). You see a before/after preview and nothing is saved until you click **Accept**.
- **Real files out.** Downloads are real `.xlsx`, `.docx` and `.pdf` files. Spreadsheets keep their formulas and their calculated totals; Word files keep their headings and bullets.
- **Forms filled from the folder.** "Fill in Seabreeze's supplier form for Northwind" fills only what the folder supports. Boxes are ticked only when true (an expired B-BBEE certificate stays unticked), and anything unknown is left blank and flagged.
- **It's careful with client files.** Broken formulas like `#REF!` are reported, not repaired. Unreadable scans are called unreadable, not guessed. Instructions hidden inside a client's document are treated as the client's text, not as commands.
- **It remembers.** Tell it "Northwind's new finance contact is…" and, once you accept, every later chat for that client knows. Uploads, edits, chats and notes all survive a restart.
- **Files like Finder.** Upload by dragging from the desktop, move files to Trash and restore them, and download any earlier version from History.
- **Voice.** Dictate a question, or have an answer read aloud (hosted version, via AWS).

## Run it locally

You need Node.js 20.19+ and Python 3.11+. Then, from this folder:

```sh
npm start
```

This sets up everything (a Python virtual environment, dependencies and the frontend build) and opens on http://127.0.0.1:8000. It also creates a `.env` file from `.env.example`.

Put your Anthropic API key in `.env` and restart:

```sh
ANTHROPIC_API_KEY=your-anthropic-api-key
```

That's all you need. Folio uses `claude-sonnet-5-5` by default; set `ANTHROPIC_MODEL` to change it. Without a key, the files, editing and memory still work, and the chat tells you Claude isn't connected. The key stays on the server and never reaches the browser.

**Using AWS instead:** leave `ANTHROPIC_API_KEY` empty and set `AWS_BEDROCK_ENABLED=1` with a normal AWS CLI profile (or `AWS_BEARER_TOKEN_BEDROCK`). The live demo runs this way, on Claude Sonnet 4.6 through AWS Bedrock, because I have AWS credits. If both are set, the Anthropic key wins.

## Try it

1. **Northwind:** ask *"What's the gross margin, and is anything broken in the workbook?"* You should get 35.0%, with the cell highlighted and the `#REF!` error called out.
2. **Northwind:** ask *"Add a column with VAT at 15% next to each amount"*, review the preview and click **Accept**, then download the workbook and open it in Excel.
3. **Northwind:** ask *"Fill in Seabreeze's supplier form."*
4. **Northwind:** ask *"What was short-delivered on the 14 October delivery note?"* This one reads a scan.
5. **Ostara:** ask *"What did the board decide in September?"* Notice how it handles the note in the minutes addressed to AI assistants.

## How it's built

- **Frontend:** React and Vite.
- **Backend:** FastAPI (Python).
- **Claude:** every answer is schema-constrained JSON. The server checks each citation against the selected client's sources before anything is shown, so a made-up or foreign reference is rejected. Answers stream as they're written; proposed edits get a second checking pass before you can accept them.
- **Documents:** openpyxl, python-docx, pypdf and PyMuPDF. Scans are read with Tesseract locally, or AWS Textract when hosted.
- **Storage:** SQLite and files on disk locally. On Vercel it uses private Vercel Blob, plus private S3 for large uploads.
- **Accounts:** Amazon Cognito when hosted (email verification, password reset, sign out). Running on localhost needs no login.

## Tests

```sh
npm test        # 112 Python tests + 6 JavaScript tests
npm run build
```

The tests cover client isolation, citation checking, edit acceptance, stale-edit protection, spreadsheet exports with saved values, accounts and sessions, and a full chat run with only `ANTHROPIC_API_KEY` set (against a simulated Anthropic API).

## Limits

See [NOTES.md](NOTES.md) for what works, what doesn't, and what I'd do next. In short:
- History can download any version but can't restore one yet.
- Edited Word paragraphs lose mixed inline formatting.
- Attaching a certificate to a PDF form isn't implemented.
- This is a working demo, not a security-audited product: there's no MFA or team roles, and no load testing.

<details>
<summary>Deploying your own copy to Vercel</summary>

Deploy this folder to Vercel and set these environment variables:
- **Required:** `BLOB_READ_WRITE_TOKEN` (connect a **private** Blob store) and `COGNITO_CLIENT_ID` (a Cognito app client with email verification).
- **Claude:** either `ANTHROPIC_API_KEY`, or `FOLIO_AWS_ROLE_ARN` with `BEDROCK_REGION` and `BEDROCK_MODEL_ID` (Vercel OIDC → an IAM role scoped to that model).
- **Large uploads and voice:** `FOLIO_S3_BUCKET` (private, versioned, encrypted, public access blocked, CORS limited to your domain).
- **Scan line highlights:** `FOLIO_OCR_ENABLED=1` and `textract:DetectDocumentText` on the role.

No permanent AWS keys are needed on Vercel. AWS services cost money.

`python scripts/package.py` builds the submission zip with a SHA-256 manifest.
</details>
