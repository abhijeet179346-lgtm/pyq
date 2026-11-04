# 500 questions per exam and study materials

## Installed content

| Exam | Original bank | Added | Total |
| --- | ---: | ---: | ---: |
| JEE Main | 6 | 494 | 500 |
| NEET UG | 6 | 494 | 500 |
| JEE Advanced | 88 | 412 | 500 |

There are 51 original revision notes across Physics, Chemistry, Mathematics and Biology. Notes include a concept explanation, key formulas or distinctions, a worked example and a common mistake. They are concise revision aids, not a complete syllabus or an official NCERT reproduction.

## Sources and attribution

The main expansion source is [datavorous/entrance-exam-dataset](https://huggingface.co/datasets/datavorous/entrance-exam-dataset), whose dataset card declares **CC BY 4.0**. Attribution is retained in each question's licence/source fields. This import extracts text from the supplied HTML, excludes visual-dependent questions, normalises options, derives structured labels and selects a chapter-diverse subset. Original examination content remains attributable to the examination authorities; the dataset publisher's declaration is preserved rather than represented as a fresh licence issued by PYQ Vault.

Additional Advanced questions come from [JEEBench, DAIR Group, IIT Delhi (2023)](https://github.com/dair-iitd/jeebench), under its MIT dataset notice preserved in `JEEBENCH_LICENSE.txt`. Existing source notes remain in `README.md` in this directory. The unused soughed JEE Main and Kshitij NEET downloads were inspected but **not imported**: their records did not provide the same reliable exam/paper coverage needed for this import.

Source file checksums and URLs are in `exam_source_manifest.json`. `exam_expansion_report.json` records subject distribution, exclusion reasons and broad taxonomy fallback counts. Source dataset IDs are explicitly labelled as dataset IDs; they are not represented as original exam question numbers.

## Metadata and limits

- Each loaded question has exam, year, subject, chapter, topic, subtopic, type, difficulty and searchable tag labels.
- Difficulty is an **estimate**, not an official or student-calibrated rating. Expansion rules use text length, answer type and exam; the basis is visible on question detail.
- Source chapter tags are retained. Topic/subtopic tags are rule-based estimates; when finer classification cannot be inferred, broad “concepts and applications” labels are explicitly marked for editorial review. They should not be presented as independently validated syllabus mappings.
- Missing-image questions and incomplete options or answer keys are excluded. Normalised duplicate stems are removed against the original bank and the newly selected set.
- Answers and solutions are imported from the attributed source; they have **not all been independently verified**. Administrators can edit tags, taxonomy and difficulty in Content → Questions.
- Study notes are original material authored for PYQ Vault and identified separately from sourced questions.

## Reproduce the installation

```sh
python manage.py migrate
python manage.py import_pyq_sample --tenant master
python manage.py import_exam_banks --tenant master
python manage.py enrich_question_metadata
python manage.py seed_revision_notes
```

The import commands preserve existing records on rerun. `--dry-run` rolls back writes. Importing globally does not force tenant feature flags on. `--tenant demo` can create missing exam assignments for the demo institute while preserving disabled existing assignments.

`exam_expansion.json` is self-contained for import; no Parquet dependency is required at application runtime. To rebuild it, download the source snapshots into `work/pyq_sources`, install the optional build dependency `pyarrow`, and run `python scripts/build_exam_banks.py`. The build also needs the previously downloaded `jeebench.zip`.

## Learner and admin access

- `/practice/`: 500 accessible questions per enabled exam; difficulty and question-type filters apply to both Browse and Start practice.
- `/library/`: exam, subject, chapter and search filters; pagination and individual reading pages.
- Question detail displays tag chips, difficulty and metadata provenance.
- Materials can be assigned to exams in Django admin. Existing tenant features and enabled-exam controls remain effective.

Django system/migration checks and page rendering checks passed during this update. No automated test suite was run for this content expansion.
