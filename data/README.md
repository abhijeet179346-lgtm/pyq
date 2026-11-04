# Real PYQ sample

`pyq_sample.json` contains 100 actual PYQs: 82 source-attributed JEEBench questions and 18 concise restatements of actual past-paper questions, with the original numerical data and answer choices. They are identified by exam, paper, subject, and original question number. The initial 18 use concise wording; JEEBench entries retain the dataset wording.

| Exam | Paper | Original questions | Count |
| --- | --- | --- | --- |
| JEE Main | 6 September 2020, Shift 2 | Physics 3, 5, 6, 13, 21, 22 | 6 |
| JEE Advanced | 2024, Paper 1 | Mathematics 2; Physics 4, 8, 10; Chemistry 1, 5 | 6 |
| NEET | 13 September 2020, Set G2 | Biology 3, 5, 9; Physics 104, 105, 106 | 6 |

JEE Main source: [paper and worked-answer copy published by BYJU'S](https://cdn1.byjus.com/wp-content/uploads/2020/10/jee-main-2020-question-paper-physics-september-6-shift-2.pdf). The sample answers were checked using the accompanying solutions and independent calculations. This source is a publisher's paper copy, not an NTA-hosted file.

JEE Advanced source: [official 2024 Paper 1](https://jeeadv.ac.in/past_qps/2024_1_English.pdf). Answers were visually checked against the [preserved final-key PDF](https://previousyearquestions.app/papers/jee-advanced/2024/answer-key-paper-1.pdf), which labels the relevant answers C, C, 25000, 8, D, and A/B/C respectively. The original historical key URL now returns 404, so the preserved copy is identified separately.

NEET source: [official archive's English G2 paper](https://cdnbbsr.s3waas.gov.in/s37bc1ec1d9c3426357e69acd5bf320061/uploads/2022/02/2022021667.pdf), linked by the [official NEET archive](https://neet.nta.nic.in/archive/). Answers were checked against page 7 of the [NTA answer-key document mirrored by Vedantu](https://www.vedantu.com/content-files-downloadable/neet/neet-2020-answer-key.pdf): Q3=1, Q5=3, Q9=1, Q104=1, Q105=2, Q106=1.

Each stored question has its source URL, source page, paper/shift, original question number in its section label, topic, concept tag, answer, and a brief independently written explanation. Original paper copyrights remain with their respective owners; this sample does not assert an open redistribution licence.

## Import

```sh
python manage.py import_pyq_sample --dry-run
python manage.py import_pyq_sample --tenant demo
```

The second command creates global published questions and enables the three exams for the existing demo institute. It preserves existing tenant feature choices. Re-running it skips existing source/paper/question references, so it does not duplicate questions or overwrite editorial changes. Use `--path` to select another file following the same schema.

Direct users see the questions at `/practice/` after email verification. Enrolled demo-institute users see them at `/learn/` when global PYQ access is enabled. The fixture includes single-choice, integer, and multiple-correct questions.

## Verification

`python manage.py test apps.content.test_pyq_sample` covers rollback in dry runs, repeatable import, taxonomy/provenance validation, correct and incorrect answer scoring, and submission of all 100 answers through the actual direct-practice views. The broader access/portal suite is `python manage.py test apps.content.test_pyq_sample apps.accounts apps.portal`.

## Expanded bank and exam-first navigation

The bank totals **100 PYQs: JEE Main 6, NEET 6, JEE Advanced 88**. The additional 82 Advanced questions are self-contained MCQs from 2016–2023 distributed across Physics, Chemistry and Mathematics, selected from [JEEBench by DAIR, IIT Delhi](https://github.com/dair-iitd/jeebench). Its MIT notice is preserved in `JEEBENCH_LICENSE.txt`; original examination rights remain with their owners. Diagram/table-dependent entries were excluded. Dataset answers are imported as supplied, with no claim that they have been independently checked against official keys. Worked solutions for those 82 questions are not yet provided, and chapter/topic/subtopic labels are an editorial classification of the question content. These entries link to the source dataset and have no invented PDF page number.

The learner home page and `/practice/` show enabled exam cards first, with live accessible-question counts. Selecting an exam reveals its questions with subject/year/search filters and pagination. Disabled exams cannot be opened via a copied URL. Question detail returns to its exam. Mathematical notation renders locally using the bundled MathJax 3.2.2 SVG renderer (Apache-2.0; `MATHJAX_LICENSE.txt`), without a runtime CDN dependency.

Import to the core platform: `python manage.py import_pyq_sample --tenant master`. Repeat imports preserve editorial changes and do not override tenant exam choices.

## Exam-wise topic and subtopic browsing

The 100 bundled questions now have chapter → topic → subtopic assignments. Both core and institute learners can browse that hierarchy, with counts taken only from their accessible questions in the chosen exam. These labels categorise the current bank; they do not represent a complete official syllabus. Unpopulated catalogue entries are omitted from question navigation. Questions without a subtopic remain accessible at the topic level.

Platform administrators manage `Catalog → Topics` and `Catalog → Subtopics`. Each subtopic belongs to exactly one topic. Question editing rejects a subtopic from a different topic. `Content → Questions` has exam, subject, chapter, topic and subtopic filters. Institute editors continue to edit their tenant-owned content. The CSV importer accepts an optional `subtopic` column, which must match an existing child of the selected topic.

To update taxonomy for an already imported copy: `python manage.py import_pyq_sample --tenant master --refresh-taxonomy`. This refresh changes taxonomy and concept links, creates a revision and increments the question version; question wording, answers, sources and attempt records are retained. Ordinary repeated imports keep existing editorial changes.

## Latest expansion

The original 100-question sample described above is retained for reproducibility. The installed database now contains **1,500 questions (500 per exam)** and **51 original revision notes**. See [EXPANDED_BANKS.md](EXPANDED_BANKS.md) for the expansion sources, tags, difficulty estimates, metadata limits and installation commands.
