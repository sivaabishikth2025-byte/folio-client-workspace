# Folio hand-in notes

Received: Thu 26 Nov 2026, 9:31 PM CT.  
Started: Fri 27 Nov 2026, 01:18 CST.  
Finished: Fri 27 Nov 2026, 13:54 CST.

## what works / what doesn't

Chat reads the selected client's folder and opens highlighted sources. All 17 supplied files are preserved. PDF, Word and Excel previews/downloads, reviewed document edits, supplier-form filling, file uploads, chat deletion and file Trash work. Accounts support email verification, recovery and sign-out. Appearance changes, resizable panels, dictation and read-aloud are included.

Ask the AI to remember a detail, then click **Accept** on its suggestion: the answer alone does not save it. The manual Client memory form uses **Save client detail** instead.

History lets you download earlier versions but cannot restore one as the current version. Edited spreadsheet downloads keep formulas and typed values, but no saved formula results; open them in Excel or LibreOffice to recalculate. Some spreadsheet functions are unsupported. Word edits may flatten mixed formatting within a paragraph. PDF certificate attachments are not assembled. Uploads allow 50 MB per file, but large documents can exceed reading limits.

In three live questions, first text appeared after **2.4–6.9 seconds**, with complete answers after **4.3–8.6 seconds**. Edits and memory suggestions undergo another check and can take longer. These are samples, not promised response times.

## how you checked it

The extracted hand-in passed 89 Python tests and six JavaScript tests; the app build passed. Thirteen browser scenarios checked sources, panel behavior and interrupted questions. Earlier live checks covered eleven AWS question/edit scenarios, account separation, 50 MB upload/download, speech, deletion and persistence. The cleaned deployment passed real sign-in and client-file access checks. Original documents matched the supplied files; the submission scan found no credentials. Physical microphone capture, heavy simultaneous use and a full security review remain untested.

## decisions (riskiest first)

1. AWS began as an optional extra and costs money. At the owner's request, this build now uses AWS Claude only, so it does **not** meet the brief's direct Anthropic API requirement.
2. AI answers can still be wrong. Keep clients separate, show sources, leave unknown form fields blank and require acceptance of AI changes.
3. Keep previous files and reject edits based on an outdated version. Keep service credentials on the server and outside the hand-in.

Additional requested work continued beyond the brief's eight-hour limit.

## next day

Add version restoration and saved spreadsheet formula results. Improve slow answers and mixed Word formatting. Add a second sign-in check and team permissions, test malicious documents and many simultaneous users, and verify a real microphone. Revisit the required direct Anthropic connection before assessment submission.
