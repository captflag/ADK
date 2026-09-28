# ADR 0026: Published tables are read from the PDF's own text layer, and shown before use

- **Status:** Accepted
- **Date:** 2026-09-24

## Context

NPPA notifies ceiling prices as an S.O. in the Gazette, and CDSCO publishes the
month's not-of-standard-quality samples as a drug alert list. Both are PDFs of
tens to hundreds of rows, and both have to be acted on: a ceiling price that is
not on record cannot be enforced, and an alert row that is not checked leaves a
batch selling. ADR 0013 and ADR 0014 read both from their tables copied to CSV
and said plainly that reading the PDF was not built. Copying a 200-row table by
hand takes an hour, and it is an hour in which a digit moves.

PDFs differ. Most published notifications are typeset from a word processor and
carry the text they print, each piece placed at a point on the page; some are
scans, which carry a picture and no text at all. Fonts may be standard or a
subset with codes of the document's own. Some files are encrypted to stop
editing, and many have cross-reference tables that no longer match the file
after an edit.

## Decision

- **The text layer is read in plain Python**, with no new dependency:
  `documents/pdf.py` for the file's objects and its filters (Flate, LZW,
  ASCIIHex, ASCII85, RunLength, and the PNG and TIFF predictors — `zlib` is in
  the standard library), `documents/glyphs.py` for what a font's codes mean and
  how wide they are, `documents/text.py` for running the content stream to give
  every piece of text a place on the page, and `documents/tables.py` for putting
  those pieces back into rows and columns.
- **Objects are found by reading the file from front to back**, stepping over
  each stream's bytes, rather than by following the cross-reference table, which
  is often stale. Where a number is defined twice the later definition wins, as
  an incremental update means it to; objects kept inside an object stream are
  unpacked.
- **Nothing is guessed at.** A page with no text is reported as a scan and read
  no further. An encrypted file is refused, saying to save a copy without a
  password. A code no font accounts for is shown as `�` and counted, never
  dropped, so a short row is visible as a short row.
- **The table is found by its own column titles** — the very spellings
  `nppa.py` and `cdsco.py` already hold — so a gazette's preamble, its page
  numbers and its footnotes are left out, and a heading repeated at the top of
  the next page is not a row. A heading printed over two lines is read as one
  heading.
- **Columns come from where the titles sit, moved onto the white lanes the rows
  leave.** A title is often centred over its column, or the figures under it set
  to the right, so the rows are what say where a column really begins; a lane
  counts as white when nearly every line leaves it empty, so one note printed
  across the width does not close the table.
- **A row that wraps is one row.** A line that fills the first column starts a
  row; a line that does not, and that sits close under the line above, is the
  rest of that row.
- **What comes out is the CSV the importers already read.** `batchward pdf
  table <file> --for nppa|cdsco` prints it, and `--out` writes it, so a person
  can check it — and correct a row the layout defeats — before importing with
  `--csv`. `ceilings import --pdf` and `recall import-alerts --pdf` read it
  directly and print what was read first: how many rows, from which pages, and
  how many characters could not be read.
- Nothing else changes: the same readers, the same conversion per pack stocked,
  the same strict matching, the same blocks and the same approvals.
- `batchward pdf text` prints the text layer line by line, with `--places` for
  where each piece sits, which is what to look at when a table is not found.

## Consequences

- A notification or an alert list can be imported from the file as published.
  The CSV path stays for scans, for layouts the reader cannot follow, and for
  corrections.
- The reader is checked against PDFs the tests write — plain and compressed
  streams, a subset font with its own codes, a font named by its glyphs, a
  heading over two or three lines, rows that wrap, a table running over two
  pages, and a page turned a quarter turn — and against
  `tests/documents/typeset-notification.pdf`, a table of the same shape laid out
  and exported by a word processor, which is not a PDF this project wrote: it
  packs its objects into an object stream behind a cross-reference stream, and
  wraps a title over three lines. Like the Marg layout (ADR 0006), the layout of
  a real notification is an assumption until real ones are loaded, which is why
  the CSV is shown before anything is kept.
- A scanned list is not read at all. Nothing recognises text in a picture here.
- A table whose columns are not separated by white space anywhere — one ruled
  tight against its text, or with a first column that wraps — will be read
  wrongly, and the person sees that in the CSV.
- Encrypted files, right-to-left and vertical writing, and tables printed as
  pictures are not handled.
- The reader adds about a thousand lines to keep. It follows the parts of the
  PDF standard that published tables use, not the standard.

## Alternatives rejected

- **A PDF library (pypdf, pdfplumber).** It would read odd files more reliably
  than this does, and that is a real cost of the choice. But it only does the
  first half: the work of turning positioned text into a government table is
  ours either way, the ceiling prices end up in the same CSV, and Batchward is
  meant to install on a distributor's machine with as little behind it as
  possible. The text layer of a typeset document is a small, well-specified
  thing, and `zlib` is already there.
- **Having the clerk agent read the notification, as it reads an invoice
  (ADR 0015).** An invoice's reading is checked by its own arithmetic, and a
  field that does not add up is read again. A notified price has nothing to
  check it against: a misread ceiling of ₹6.42 as ₹64.20 would be recorded and
  enforced. Figures no arithmetic can catch are not a model's work (ADR 0003).
- **Following the cross-reference table.** Correct for a file that was never
  edited, useless for many that were; reading front to back handles both.
- **Finding columns from the white lanes alone.** With a handful of rows, a
  lane appears inside a column by chance. The titles anchor the columns; the
  lanes correct them.
- **Fetching notifications from NPPA and CDSCO.** Still not built: the sites
  give no feed, and a wrong download would record wrong prices.
