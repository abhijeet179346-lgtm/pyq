# Admin PYQ batch

`reviewed_pyq_batch.json` records 24 concise restatements from identifiable previous-year paper questions. Each item has a paper, original question number in `source_id`, subject, answer, original PDF URL, source page, and an independently written solution. The JSON is the editable source; `admin_import_workbooks/*.xlsx` are the generated admin uploads. Original examination and publisher rights remain with their owners.

| Exam | Paper/answer source | Imported rows | Admin job |
| --- | --- | ---: | ---: |
| JEE Main | [BYJU'S 6 September 2020 Shift 2 Physics PDF](https://cdn1.byjus.com/wp-content/uploads/2020/10/jee-main-2020-question-paper-physics-september-6-shift-2.pdf) | 1 | 4 |
| JEE Advanced | [Official 2024 Paper 1 PDF](https://jeeadv.ac.in/past_qps/2024_1_English.pdf); answer checked by dimensional analysis | 1 | 3 |
| NEET | [CollegeBatch 2024 Q1 solved PDF](https://www.collegebatch.com/exam-docs/NEET-2024-Question-Paper-with-Answer.pdf) | 1 | 7 |
| COMEDK | [CollegeBatch 2024 solved PDF](https://www.collegebatch.com/exam-docs/COMEDK-2024-Question-Paper-with-Solution.pdf) | 6 | 1 |
| MHT-CET | [GetMyUni 24 April 2024 Shift 1 solutions PDF](https://media.getmyuni.com/assets/downloadables/9c8e766b-cadc-4678-979e-02552b00e38d.pdf) | 4 | 5, 6 |
| CUET UG | [Resonance 16 May 2024 Physics Booklet C solved PDF](https://www.resonance.ac.in/answer-key-solutions/cuet/2024/solutions/physics.pdf) | 11 | 2 |

The MHT-CET PDF has inconsistent working in some items. The four selected items were recalculated before import; the organ-pipe question uses the correct $1.2\,\mathrm{m}$ result despite the PDF's erroneous intermediate line. Diagram-dependent questions were excluded from this batch.

From the project root, run `python manage.py shell < scripts/import_reviewed_pyqs.py` in the project virtual environment to regenerate the Excel files and submit each exam/subject workbook through the admin Content Job add, process, and commit views. The script requires an active superuser and a master tenant. It does not publish questions; an editor must review and publish the drafts. Existing completed job filenames cause the upload to be skipped. The local database has jobs 1–7 completed and all 24 questions published after review.

For further source discovery, see the [official JEE Advanced past papers](https://jeeadv.ac.in/archive.html), [NTA paper downloads](https://nta.ac.in/Downloads), [NEET archive](https://neet.nta.nic.in/archive/), [Maharashtra CET Cell question paper notice](https://cetcell.mahacet.org/cms-download/mht-cet-2024-display-of-question-paper-and-updated-answer-key-for-students/), and [CUET official final answer keys](https://exams.ntaonline.in/CUET-UG/images/offline-final-answer-key-250724.pdf). Some official portals expose individual papers only through candidate logins, so the linked downloadable publisher copies are identified separately from official sources.
