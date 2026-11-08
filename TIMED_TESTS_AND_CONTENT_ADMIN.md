# Timed tests, Excel uploads, PDF conversion and mistake review

## Available tests

Learners open **Timed tests** in the navigation (`/practice/tests/`), choose an enabled exam and generate a full or subject test. Full tests select up to 75 questions across available subjects and run for 180 minutes; subject tests select up to 25 questions and run for 60 minutes. The chooser provides a balanced mix where enough questions are available and skips questions that cannot be graded automatically. Generated sets are practice papers and do not claim to reproduce an official exam's complete format. Marking schemes appear on the instructions screen.

Existing attempts and recent results remain accessible from the timed-test page. Previously configured mock papers remain in the database and can still be opened through their direct links. The initial JEE Advanced bank and additional benchmark questions are preserved in `data/timed_test_questions.json`; see `data/JEEBENCH_LICENSE.txt` for the JEEBench notice. Imported answer keys have not all been independently verified.

## Timer, scoring and access

The server stores a fixed deadline and an immutable question/marking snapshot at start. Refreshing does not reset time. Answers and review flags autosave every ten seconds; explicit Save and Save & next actions are also available. Late answer changes are rejected. A closed-browser attempt is finalized on its next visit or by `python manage.py expire_timed_tests`. For unattended expiry, run that command periodically using the deployment's worker/scheduler.

The exam interface shows one question at a time, a question palette, subject navigation, review flags, clear-answer controls and a server-synchronised countdown. The full-paper form retains responses while navigating. Answers and solutions are withheld until submission/expiry. Decimal-equivalent numerical answers are accepted; no implicit tolerance range is assumed. No fake ranking or percentile is calculated.

Tests remain tenant-controlled through the `tests` feature and enabled exam settings. Coaching tenants can upload their own tests; enrolled users access that tenant's test catalogue. The default 15 papers are installed in the master tenant only. `python manage.py seed_timed_tests` installs them idempotently. `--refresh` refreshes seeded content only when no attempt exists, preserving snapshots of already attempted papers.

## Admin: Excel import

Open **Admin → Imports → Content jobs** (`/admin/imports/contentjob/`). Download the relevant template from the Add/Change form:

1. Questions: practice questions or global PYQs.
2. Exams: exam catalogue entries (platform administrators only).
3. Tests: timed tests, sections, question rows and marking schemes.
4. Materials: subject/chapter/topic study sections and page references.

Templates include a small original example row; replace it with your content. The backend supports `.xlsx`; the existing CSV question importer remains available. It does not execute spreadsheet formulas, and exports keep cell contents as literal text. Maximum Excel input: 25 MB, 10,000 rows, 100 MB uncompressed. Legacy `.xls` and macro workbooks are not accepted.

Upload a job of the matching type and tenant. In the job list select **Process uploads**. The service validates the complete workbook and shows errors without writing content. After reviewing, choose **Commit validated Excel uploads**. Invalid batches roll back in full. Repeated commits cannot duplicate a completed job.

For a single PYQ bank, select **Target exam** and **Target subject** on the upload form. Missing `exam` and `subject` workbook cells are filled from those choices; conflicting cells are rejected. `kind` must be `PYQ` or blank and each row must have a year. Leave both choices blank for a mixed workbook. Exam subjects are edited in **Catalog → Exams**. Use `$...$` or `$$...$$` for LaTeX; put question image filenames in the `images` column and upload the workbook with the images in one ZIP. Existing question images can be previewed, removed and reordered in **Content → Questions**.

Questions, tests and material sections are created as drafts/inactive. Publish questions in Content → Questions; activate tests in Assessments → Timed tests; review and activate material sections in Materials → Material sections. New exam entries use the `active` column; existing exam entries are preserved. Enable exams for the intended tenant separately. Only platform admins can create global PYQs or exam entries; institute admins create their own practice content and tests.

`source_id` provides a stable identifier for question imports. Include source and licence information. The question schema supports subject, chapter, topic, subtopic, difficulty, tags, type, A–D options and answer/solution text. Multiple-correct answer format is `A,C` or a JSON list. Tests use `test_name` to group rows into papers and include explicit section marking rules. The same paper must use a consistent exam and duration.

## Admin: large PDF to Excel

Create a **PDF to Excel** content job. Maximum upload: **100 MB, 1,500 pages**. Select **Process uploads / continue next 15 PDF pages** to process the next chunk. For a large job, run:

```sh
python manage.py process_content_jobs --job JOB_ID --all-pages
```

This reads pages incrementally, retaining extracted questions, page numbers, original blocks and source-page text. Download the review workbook with the job's **Download review workbook** link. The Questions sheet follows the standard import columns; Source pages preserves the full extracted page text, split over multiple rows if needed for Excel's cell limit. You can map these columns into another platform's schema.

**Extraction is a review aid, not guaranteed publisher-layout recognition.** Numbering, multi-column layouts, equations, cross-page questions and diagrams must be checked against the source. Answers are copied only when explicitly printed. Missing answers are left blank, preventing direct import until filled/reviewed. OCR-derived answers must also be checked. No content is automatically published.

For scanned pages select **Use OCR**. This requires `OPENAI_API_KEY`, the configured vision provider/model, tenant AI settings and a non-zero budget. Without configuration the job reports the error; it does not silently fabricate content. Text PDFs need no AI key. Embedded question diagrams are not reconstructed by the text parser; review against the original PDF and retain or upload the figure using the existing scan workflow where needed.

## Admin: ingest study material and link it after mistakes

In **Materials → Material documents**:

1. Upload a text PDF, Markdown or TXT file (up to 100 MB).
2. Set the default topic (therefore its subject), source, licence and supported exams.
3. Select **Ingest text PDF/Markdown/TXT into study sections**.
4. Review the extracted sections and refine their topic assignments. PDF page references are preserved. New PDF documents can contain up to 1,500 pages.

Existing material-processing command: `python manage.py process_material_imports`. Selectable PDF text is ingested locally. Scanned study PDFs require OCR before this material-text ingestion path.

For precise teaching links, edit a question in **Content → Questions**, then add entries under **Question material links** selecting an exact section. A document with curated links cannot be re-ingested in place: ingest a new document to preserve those references.

After an incorrect practice response, or in timed-test review, learners receive related study sections with a quoted excerpt, subject/chapter and page reference. Manual links rank first, followed by same-topic, same-chapter and keyword-related subject matches. Each recommendation states its matching reason; this is retrieval, not a claim to diagnose why the student made the mistake. Uploaded source PDFs can be opened at the referenced page via an authenticated, tenant-scoped source endpoint.

The `study_material` feature, active sections/documents, tenant ownership and enabled exam settings filter recommendations. Private institute content is not shown to another tenant. Disabling study material hides recommendations without disabling scoring.

## Checks performed

Django checks and migration consistency passed. Functional checks exercised test listing/start/save/submit, deadline expiry, all four Excel template previews and PDF block-to-workbook conversion. Changes created during checks were rolled back. No full automated regression suite was run for this update.

## Upload review workspace

Content jobs now have a dedicated **Process & review** page linked from the job list and upload form. It shows status, created-record counts, page progress, errors and paginated row previews. Process the next PDF chunk, download the current workbook, or validate an Excel upload directly from that page. A reviewed workbook can be committed there; completed jobs link to the appropriate draft/inactive content lists. Bulk commit actions lead to this review page.

PDF page checkpoints are saved as each page finishes. Concurrent processors cannot claim the same running job, and failed jobs resume at the last saved page. The command-line worker and admin actions share the same processing implementation.
