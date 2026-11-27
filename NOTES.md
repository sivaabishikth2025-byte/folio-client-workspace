# Folio hand-in notes

Received: Thu 26 Nov 2026, 9:31 PM CT.  
Started: Fri 27 Nov 2026, 01:18 CST.  
Finished: Fri 27 Nov 2026, 14:31 CST.

## what works / what doesn't

Chat reads the selected client's folder and opens highlighted evidence, including text lines in scanned PDFs. All 17 supplied files are preserved. PDF, Word and Excel previews/downloads, reviewed edits, supplier forms, uploads, chat deletion and file Trash work. Accounts include verification, recovery and sign-out. Appearance changes, resizable panels, dictation and read-aloud are included.

AI memory suggestions need an **Accept** click; the answer alone does not save them. The manual memory form uses **Save client detail**.

Spreadsheet downloads now include calculated values alongside formulas, including existing files and new uploads. Unsupported functions show errors. History downloads exact stored versions but cannot restore one as current. Word edits may flatten mixed paragraph formatting. PDF certificate attachments are not assembled. Uploads allow 50 MB per file; large documents can exceed reading limits. Unreadable scans fall back to whole-page evidence.

Three earlier live questions showed first text in **2.4–6.9 seconds**, finishing in **4.3–8.6 seconds**. Two scan questions showed text in **7.6–7.8 seconds**, finishing in **9.7–9.9 seconds**. Edits, memory checks and initial scan processing can take longer; these are samples, not guarantees.

## how you checked it

Passed 110 Python tests (including an end-to-end local run with only `ANTHROPIC_API_KEY` set, against a simulated Anthropic API), six JavaScript tests, 14 browser scenarios and the app build. Live checks covered a newly uploaded scan with real AWS line highlighting, existing workbook downloads, a new 5 MB workbook and accepted edits. Downloaded values read without recalculation: gross margin 0.3499015576 and debtor total 717,505.75; formulas remain intact. Original client data stayed unchanged.

Earlier live checks covered eleven AWS question/edit scenarios, account separation, 50 MB transfer, speech, deletion and persistence. Original files match the supplied pack; the submission scan found no credentials. Physical microphones, native phone/email previewers, heavy simultaneous use and a full security review remain untested.

## decisions (riskiest first)

1. Claude provider. The code calls Claude through the Anthropic API when `ANTHROPIC_API_KEY` is set (model `claude-sonnet-5-5`, configurable), and through AWS Bedrock otherwise. The hosted demo uses AWS Bedrock (Claude Sonnet 4.6) for cost reasons, because I have AWS credits; no Anthropic key is set there. To run locally with your own key, see the README. AWS services, including scan text recognition, cost money.
2. AI and scanned text can be wrong. Keep clients separate, show sources, leave unknown fields blank and require acceptance of AI changes.
3. Preserve previous files, reject outdated edits and keep service credentials on the server, outside the hand-in.

I kept polishing after the core was working, so my total time ran past the suggested eight hours.

## next day

Add version restoration. Improve slow answers and mixed Word formatting. Add a second sign-in check and team permissions; test malicious documents, simultaneous users and real microphones.
