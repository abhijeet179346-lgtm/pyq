"""Add per-subject source citations alongside downloaded public PDFs."""
from pathlib import Path


ROOT = Path.cwd() / 'data/additional_web_sources'
SOURCES = {
    'COMEDK': {
        'Physics': [
            ('COMEDK 2023 public question paper and answer key downloads — CGP Edu', 'https://cgpedu.com/comedk-papers/'),
            ('COMEDK previous-year papers, 2023 and 2024 shift archives — PracBee', 'https://hive.pracbee.com/comedk/previous-year-question-papers'),
        ],
        'Chemistry': [
            ('COMEDK 2023 public question paper and answer key downloads — CGP Edu', 'https://cgpedu.com/comedk-papers/'),
            ('COMEDK previous-year papers, 2023 and 2024 shift archives — PracBee', 'https://hive.pracbee.com/comedk/previous-year-question-papers'),
        ],
        'Mathematics': [
            ('COMEDK 2023 public question paper and answer key downloads — CGP Edu', 'https://cgpedu.com/comedk-papers/'),
            ('COMEDK previous-year papers, 2023 and 2024 shift archives — PracBee', 'https://hive.pracbee.com/comedk/previous-year-question-papers'),
        ],
    },
    'CUET UG': {
        'Biology': [
            ('CUET UG 2023 papers and final answer key — AglaSem', 'https://admission.aglasem.com/cuet-2023-question-paper/'),
            ('CUET UG 2024 solved Biology paper — Resonance', 'https://www.resonance.ac.in/answer-key-solutions/cuet/2024/solutions/biology.pdf'),
            ('CUET UG 2024 final answer keys — NTA notice', 'https://exams.ntaonline.in/CUET-UG/images/offline-final-answer-key-250724.pdf'),
        ],
        'Chemistry': [
            ('CUET UG 2023 papers and final answer key — AglaSem', 'https://admission.aglasem.com/cuet-2023-question-paper/'),
            ('CUET UG 2024 solved Chemistry paper — Resonance', 'https://www.resonance.ac.in/answer-key-solutions/cuet/2024/solutions/chemistry.pdf'),
            ('CUET UG 2024 final answer keys — NTA notice', 'https://exams.ntaonline.in/CUET-UG/images/offline-final-answer-key-250724.pdf'),
        ],
        'Mathematics': [
            ('CUET UG 2023 papers and final answer key — AglaSem', 'https://admission.aglasem.com/cuet-2023-question-paper/'),
            ('CUET UG 2024 solved Mathematics paper — Resonance', 'https://www.resonance.ac.in/answer-key-solutions/cuet/2024/solutions/mathematics.pdf'),
            ('CUET UG 2024 final answer keys — NTA notice', 'https://exams.ntaonline.in/CUET-UG/images/offline-final-answer-key-250724.pdf'),
        ],
        'Physics': [
            ('CUET UG 2023 papers and final answer key — AglaSem', 'https://admission.aglasem.com/cuet-2023-question-paper/'),
            ('CUET UG 2024 solved Physics paper — Resonance', 'https://www.resonance.ac.in/answer-key-solutions/cuet/2024/solutions/physics.pdf'),
            ('CUET UG 2024 final answer keys — NTA notice', 'https://exams.ntaonline.in/CUET-UG/images/offline-final-answer-key-250724.pdf'),
        ],
        'General Test': [
            ('CUET UG 2023 papers and final answer key — AglaSem', 'https://admission.aglasem.com/cuet-2023-question-paper/'),
            ('CUET UG 2024 solved General Test paper — Resonance', 'https://www.resonance.ac.in/answer-key-solutions/cuet/2024/solutions/General-Test-v1.pdf'),
            ('CUET UG 2024 final answer keys — NTA notice', 'https://exams.ntaonline.in/CUET-UG/images/offline-final-answer-key-250724.pdf'),
        ],
    },
    'JEE Advanced': {},
    'JEE Main': {
        'Physics': [
            ('JEE Main 2024 Session 1 final answer key — NTA', 'https://www.nta.ac.in/Download/Notice/Notice_20240212120843.pdf'),
            ('JEE Main 2024 Session 2 final answer key — NTA', 'https://www.nta.ac.in/Download/Notice/Notice_20240422121837.pdf'),
            ('JEE Main official notice archive', 'https://jeemain.nta.ac.in/archive/'),
        ],
        'Chemistry': [
            ('JEE Main 2024 Session 1 final answer key — NTA', 'https://www.nta.ac.in/Download/Notice/Notice_20240212120843.pdf'),
            ('JEE Main 2024 Session 2 final answer key — NTA', 'https://www.nta.ac.in/Download/Notice/Notice_20240422121837.pdf'),
            ('JEE Main official notice archive', 'https://jeemain.nta.ac.in/archive/'),
        ],
        'Mathematics': [
            ('JEE Main 2024 Session 1 final answer key — NTA', 'https://www.nta.ac.in/Download/Notice/Notice_20240212120843.pdf'),
            ('JEE Main 2024 Session 2 final answer key — NTA', 'https://www.nta.ac.in/Download/Notice/Notice_20240422121837.pdf'),
            ('JEE Main official notice archive', 'https://jeemain.nta.ac.in/archive/'),
        ],
    },
    'NEET': {
        'Biology': [
            ('NEET UG 2024 final answer key — NTA', 'https://nta.ac.in/Download/Notice/Notice_20240726171027.pdf'),
            ('NEET UG 2023 final answer key — NTA', 'https://www.nta.ac.in/Download/Notice/Notice_20230615172159.pdf'),
            ('NEET UG 2024 question paper list; PDFs open from NTA — Disha Stars', 'https://www.dishastars.com/news/nta/papers/neet-ug/2024'),
        ],
        'Chemistry': [
            ('NEET UG 2024 final answer key — NTA', 'https://nta.ac.in/Download/Notice/Notice_20240726171027.pdf'),
            ('NEET UG 2023 final answer key — NTA', 'https://www.nta.ac.in/Download/Notice/Notice_20230615172159.pdf'),
            ('NEET UG 2024 question paper list; PDFs open from NTA — Disha Stars', 'https://www.dishastars.com/news/nta/papers/neet-ug/2024'),
        ],
        'Physics': [
            ('NEET UG 2024 final answer key — NTA', 'https://nta.ac.in/Download/Notice/Notice_20240726171027.pdf'),
            ('NEET UG 2023 final answer key — NTA', 'https://www.nta.ac.in/Download/Notice/Notice_20230615172159.pdf'),
            ('NEET UG 2024 question paper list; PDFs open from NTA — Disha Stars', 'https://www.dishastars.com/news/nta/papers/neet-ug/2024'),
        ],
    },
    'MHT-CET': {
        'Physics': [
            ('MHT-CET 2023 subject question papers — Nuclear Academy', 'https://www.nuclearacademy.org/nuclear-study/mht-cet/pyq-mht-cet'),
            ('MHT-CET 2024 shift papers — PracBee', 'https://hive.pracbee.com/mht-cet/previous-year-question-papers/2024'),
            ('MHT-CET 2024 PCM solved question paper — ITM', 'https://blog-itm.itmlive.in/blog/content/files/2026/01/MHT-CET-2024-PCM-Question-Paper-With-Answer-Key.pdf'),
            ('MHT-CET 2024 updated answer key notice — Maharashtra CET Cell', 'https://cetcell.mahacet.org/cms-download/mht-cet-2024-display-of-question-paper-and-updated-answer-key-for-students/'),
        ],
        'Chemistry': [
            ('MHT-CET 2023 subject question papers — Nuclear Academy', 'https://www.nuclearacademy.org/nuclear-study/mht-cet/pyq-mht-cet'),
            ('MHT-CET 2024 shift papers — PracBee', 'https://hive.pracbee.com/mht-cet/previous-year-question-papers/2024'),
            ('MHT-CET 2024 PCM solved question paper — ITM', 'https://blog-itm.itmlive.in/blog/content/files/2026/01/MHT-CET-2024-PCM-Question-Paper-With-Answer-Key.pdf'),
            ('MHT-CET 2024 updated answer key notice — Maharashtra CET Cell', 'https://cetcell.mahacet.org/cms-download/mht-cet-2024-display-of-question-paper-and-updated-answer-key-for-students/'),
        ],
        'Mathematics': [
            ('MHT-CET 2023 subject question papers — Nuclear Academy', 'https://www.nuclearacademy.org/nuclear-study/mht-cet/pyq-mht-cet'),
            ('MHT-CET 2024 shift papers — PracBee', 'https://hive.pracbee.com/mht-cet/previous-year-question-papers/2024'),
            ('MHT-CET 2024 PCM solved question paper — ITM', 'https://blog-itm.itmlive.in/blog/content/files/2026/01/MHT-CET-2024-PCM-Question-Paper-With-Answer-Key.pdf'),
            ('MHT-CET 2024 updated answer key notice — Maharashtra CET Cell', 'https://cetcell.mahacet.org/cms-download/mht-cet-2024-display-of-question-paper-and-updated-answer-key-for-students/'),
        ],
        'Biology': [
            ('MHT-CET 2023 Biology question paper — Nuclear Academy', 'https://www.nuclearacademy.org/nuclear-study/mht-cet/pyq-mht-cet'),
            ('MHT-CET 2024 updated answer key notice — Maharashtra CET Cell', 'https://cetcell.mahacet.org/cms-download/mht-cet-2024-display-of-question-paper-and-updated-answer-key-for-students/'),
        ],
    },
}

jee_adv_sources = [
    ('Official JEE Advanced question paper archive', 'https://jeeadv.ac.in/archive.html'),
    ('JEE Advanced 2024 Paper 1 English — official PDF', 'https://jeeadv.ac.in/past_qps/2024_1_English.pdf'),
    ('JEE Advanced 2024 Paper 2 English — official PDF', 'https://jeeadv.ac.in/past_qps/2024_2_English.pdf'),
]
SOURCES['JEE Advanced'] = {subject: jee_adv_sources for subject in ('Physics', 'Chemistry', 'Mathematics')}

for exam, subjects in SOURCES.items():
    for subject, links in subjects.items():
        folder = ROOT / exam / subject
        folder.mkdir(parents=True, exist_ok=True)
        lines = [f'# More source links — {exam} / {subject}', '',
                 'Downloaded source PDFs in this folder are listed first. Links below point to the source pages or official keys.', '']
        downloaded = sorted(path.name for path in folder.glob('*.pdf'))
        if downloaded:
            lines.append('## Files in this folder')
            lines.extend(f'- `{name}`' for name in downloaded)
            lines.append('')
        lines.append('## More sources')
        lines.extend(f'- [{title}]({url})' for title, url in links)
        lines.extend(['', 'Question papers and answer keys belong to their source publishers. Check the source and key version before using them to score or publish questions.', ''])
        (folder / 'More sources.md').write_text('\n'.join(lines))

print('Wrote source notes for', sum(len(subjects) for subjects in SOURCES.values()), 'subject folders.')
