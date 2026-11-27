# Folio — Engineering Notes

## Status

**Shipped**
- **Grounded answers.** Retrieval is scoped to the selected client. Every claim links to its source and opens at the exact cell, paragraph or scanned-PDF line. The response schema allows only that client's source IDs, and the server validates them again.
- **Reviewed writes.** Edits, supplier forms and client memory are returned as proposals and applied only after the user accepts them. Writes are versioned and checked against the current file version (optimistic concurrency).
- **Documents.** All 17 seed files are preserved. PDF, Word and Excel preview and download work, and exported workbooks carry cached formula values. Uploads go up to 50 MB through S3. Scans are processed with Textract OCR.
- **Platform.** Cognito accounts with verification and recovery, private Blob storage, Trash, and voice input and output (Transcribe and Polly).

**Open items**
- Run-level Word formatting (e.g. mixed bold within one paragraph) can flatten on edit; headings and bullets are kept.
- Latency: first token at 2.4–7.8 s and full answers at 4.3–9.9 s on Bedrock. Writes add a second review pass.

## Verification

- **Automated:** 110 Python tests and 6 JS tests. They cover tenant isolation, citation validation, stale-write rejection, credential handling, provider error mapping, and an end-to-end run on the Anthropic provider against a mocked API.
- **Live, on production:** 14 scenarios:
  - a cited gross margin, including a `#REF!` cell;
  - a general-knowledge answer labelled as such;
  - a cross-client request refused;
  - a VAT column added, accepted and recalculated;
  - a supplier form filled;
  - line-level highlights on scans;
  - a prompt injection in the board minutes ignored;
  - memory saved, recalled and isolated per client.
- **Data integrity:** exported values were read back without recalculation (gross margin 0.3499, debtors 717,505.75). Seed files match the pack byte for byte. A secret scan of the repo came back clean.

## Key decisions

1. **Model provider.** The Anthropic Messages API is used when `ANTHROPIC_API_KEY` is set (`ANTHROPIC_MODEL`, default `claude-sonnet-5-5`). Otherwise the app uses AWS Bedrock. The hosted demo runs on Bedrock for cost reasons. Both paths share one prompt, one schema and one validation layer. The provider is chosen on the server, never by the client.
2. **Treat model and OCR output as untrusted.** Answers must cite evidence, unknown form fields stay blank, and every change needs explicit approval. This trades a little speed and an extra click for correctness on financial data.
3. **Client data is never lost or overwritten.** Every write keeps the previous version, stale writes are rejected, and credentials stay server-side.

## Roadmap

1. Latency: stream the review pass and cache parsed documents across chats.
2. Keep run-level formatting when editing Word files.
3. MFA and per-client roles (owner, reviewer, read-only).
4. Adversarial test suite: malicious uploads, injection corpora, concurrent editors.
