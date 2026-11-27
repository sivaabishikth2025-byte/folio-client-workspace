# Folio: hand-in notes

## What works / what doesn't

**Works**
- Answers come only from the selected client's folder. Every claim cites a source that opens at the highlighted cell, paragraph or scanned-PDF line. Citations are limited by schema to that client's files and checked again on the server.
- Edits and supplier forms are shown as proposals. Nothing is written until **Accept**. The previous version is kept, and an edit made against an outdated file is rejected.
- Client memory is proposed by the assistant and saved only on **Accept**. Memory stays with its own client.
- All 17 supplied files are preserved. PDF, Word and Excel preview and download work. Downloaded workbooks contain both formulas and calculated values.
- Uploads up to 50 MB, OCR for scans, accounts with verification and recovery, chat and file deletion (Trash), dictation and read-aloud.

**Known limits**
- History downloads any stored version but can't restore one as current.
- Word edits can flatten mixed formatting within a paragraph. PDF certificate attachments aren't assembled.
- Unreadable scans fall back to whole-page evidence. Very large documents can exceed the reading limit.
- Speed: first text took 2.4–7.8 s and full answers 4.3–9.9 s across sampled live questions. Edits and memory take longer because they get a second review pass.

## How I checked it

- **110 Python tests** cover client isolation, citation validation, stale-edit rejection, credential handling, provider errors, and a full local run with only `ANTHROPIC_API_KEY` set against a simulated Anthropic API. There are also **6 JS tests**, and the build passes.
- **14 browser scenarios** were run on the live site: cited gross margin (including a `#REF!` cell), VAT marked as general knowledge, cross-client question refused, VAT column proposal → Accept → recalculation, Seabreeze supplier form, exact-line scan highlight, ignored prompt injection in the board minutes, and memory saved, recalled and isolated.
- Downloaded files were read back without recalculation (gross margin 0.3499, debtors 717,505.75). Original seed files match the pack byte for byte. A secret scan of the repo found nothing.

## Decisions (riskiest first)

1. **Claude provider.** If `ANTHROPIC_API_KEY` is set, the server calls the Anthropic Messages API (`ANTHROPIC_MODEL`, default `claude-sonnet-5-5`). Otherwise it uses AWS Bedrock. I host the demo on Bedrock (Claude Sonnet 4.6) for cost reasons because I have AWS credits. Both providers share the same prompt, schema and validation, and the server picks the provider so the client can't.
2. **The model and OCR can be wrong, so the UI assumes they are.** Answers are grounded in cited evidence, unknown form fields stay blank, and every change needs a human Accept. *Trade-off:* more clicks and a slower second pass on writes.
3. **Never lose or overwrite client data.** Files are versioned, writes check the file version, and credentials live only on the server. *Trade-off:* restore is still missing (see below).

## What I'd do next

1. Version restore from History.
2. Cut latency by streaming the review pass and caching parsed files across chats.
3. Preserve run-level Word formatting on edits.
4. MFA and team roles (owner / reviewer / read-only) per client.
5. Adversarial testing: malicious uploads, injection corpora, concurrent editors.
