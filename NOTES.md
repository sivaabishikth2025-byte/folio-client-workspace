# Folio — Engineering Notes

## Status

**Working**
- **Client-scoped assistant.** One client at a time. Retrieval, citations and memory are all limited to the selected client, and this is enforced on the server as well as in the response schema.
- **Cited answers.** Answers draw on the whole folder (Excel, Word, PDFs, forms, scans). Each source opens beside the chat with the exact cell, paragraph or line highlighted. General-knowledge answers are labelled as such.
- **Reviewed edits.** Excel, Word and PDF-form changes are proposed as a diff and applied on **Accept**. Workbooks keep live formulas, and Word files keep their headings and bullets. Form fields with no evidence in the folder are left blank and reported.
- **Files.** A Finder-style browser with drag-and-drop. PDF, Word and Excel open in the app. Anything uploaded downloads in its own format.
- **Persistence.** Files, edits and client details survive restarts.
- **Untrusted input.** Document content is treated as client data, never as instructions. Unreadable scans are flagged rather than guessed.

**Limits**
- Inline formatting that changes within a single paragraph (mixed bold or italic) can flatten when that paragraph is edited.

## Verification

- **Automated:** 110 backend and 6 frontend tests. They cover client isolation, citation validation, stale-write rejection and both model providers.
- **End-to-end on production:** every scenario from the kickoff call, including cross-client refusal, VAT-labelled general knowledge, the VAT-column edit through Accept, the Seabreeze form, scan highlighting, a prompt injection in a client document, and client memory.
- **Output integrity:** downloaded files were opened outside the app with formulas and values intact. The seed data is unchanged.

## Key decisions

1. **Model provider.** The Anthropic API is used when `ANTHROPIC_API_KEY` is set. Otherwise the app uses AWS Bedrock, and the hosted demo runs on Bedrock for cost reasons. Both share one prompt, schema and validation layer.
2. **Model output is untrusted.** Every claim must cite evidence, and every change needs human approval. A little speed is traded for correctness on financial data.
3. **Client data is never lost.** Writes are versioned and checked for conflicts, and credentials stay server-side.

## Next

1. Reduce response latency.
2. Full-fidelity Word editing.
3. A broader adversarial and regression eval suite.
