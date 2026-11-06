"""Download additional public PYQ source PDFs into Drive-ready subject folders.

Run from project root: .venv/bin/python scripts/collect_additional_drive_sources.py
Only public PDF responses with a valid PDF signature are saved.
"""
from pathlib import Path
import shutil
import requests


ROOT = Path.cwd() / 'data/additional_web_sources'
if ROOT.exists():
    shutil.rmtree(ROOT)

SOURCES = {
    'COMEDK': {
        'Physics': [
            ('COMEDK_2023_Question_Paper.pdf', 'https://cgpedu.com/wp-content/uploads/2026/09/COMEDK-2023.pdf'),
            ('COMEDK_2023_Answer_Key.pdf', 'https://cgpedu.com/wp-content/uploads/2026/09/COMEDK-2023-A.pdf'),
        ],
        'Chemistry': [
            ('COMEDK_2023_Question_Paper.pdf', 'https://cgpedu.com/wp-content/uploads/2026/09/COMEDK-2023.pdf'),
            ('COMEDK_2023_Answer_Key.pdf', 'https://cgpedu.com/wp-content/uploads/2026/09/COMEDK-2023-A.pdf'),
        ],
        'Mathematics': [
            ('COMEDK_2023_Question_Paper.pdf', 'https://cgpedu.com/wp-content/uploads/2026/09/COMEDK-2023.pdf'),
            ('COMEDK_2023_Answer_Key.pdf', 'https://cgpedu.com/wp-content/uploads/2026/09/COMEDK-2023-A.pdf'),
        ],
    },
    'CUET UG': {
        'Biology': [
            ('CUET_UG_2023_Biology_Question_Paper.pdf', 'https://cdn.aglasem.com/aglasem-doc/c404f048-390c-11ee-8101-0a5e36bc6706/c404f048-390c-11ee-8101-0a5e36bc6706.pdf'),
            ('CUET_UG_2024_Biology_Solved_Paper.pdf', 'https://www.resonance.ac.in/answer-key-solutions/cuet/2024/solutions/biology.pdf'),
        ],
        'Chemistry': [
            ('CUET_UG_2023_Chemistry_Question_Paper.pdf', 'https://cdn.aglasem.com/aglasem-doc/cb19e24e-390c-11ee-977b-0a5e36bc6706/cb19e24e-390c-11ee-977b-0a5e36bc6706.pdf'),
            ('CUET_UG_2024_Chemistry_Solved_Paper.pdf', 'https://www.resonance.ac.in/answer-key-solutions/cuet/2024/solutions/chemistry.pdf'),
        ],
        'Mathematics': [
            ('CUET_UG_2023_Mathematics_Question_Paper.pdf', 'https://cdn.aglasem.com/aglasem-doc/e4e1840c-390c-11ee-bf06-0a5e36bc6706/e4e1840c-390c-11ee-bf06-0a5e36bc6706.pdf'),
            ('CUET_UG_2024_Mathematics_Solved_Paper.pdf', 'https://www.resonance.ac.in/answer-key-solutions/cuet/2024/solutions/mathematics.pdf'),
        ],
        'Physics': [
            ('CUET_UG_2023_Physics_Question_Paper.pdf', 'https://cdn.aglasem.com/aglasem-doc/e542ce10-390c-11ee-8fd0-0a5e36bc6706/e542ce10-390c-11ee-8fd0-0a5e36bc6706.pdf'),
            ('CUET_UG_2024_Physics_Solved_Paper.pdf', 'https://www.resonance.ac.in/answer-key-solutions/cuet/2024/solutions/physics.pdf'),
        ],
        'General Test': [
            ('CUET_UG_2023_General_Test_Question_Paper.pdf', 'https://cdn.aglasem.com/aglasem-doc/d5df047a-390c-11ee-b951-0a5e36bc6706/d5df047a-390c-11ee-b951-0a5e36bc6706.pdf'),
            ('CUET_UG_2024_General_Test_Solved_Paper.pdf', 'https://www.resonance.ac.in/answer-key-solutions/cuet/2024/solutions/General-Test-v1.pdf'),
        ],
    },
    'JEE Advanced': {
        'Physics': [
            ('JEE_Advanced_2024_Paper_1_English.pdf', 'https://jeeadv.ac.in/past_qps/2024_1_English.pdf'),
            ('JEE_Advanced_2024_Paper_2_English.pdf', 'https://jeeadv.ac.in/past_qps/2024_2_English.pdf'),
        ],
        'Chemistry': [
            ('JEE_Advanced_2024_Paper_1_English.pdf', 'https://jeeadv.ac.in/past_qps/2024_1_English.pdf'),
            ('JEE_Advanced_2024_Paper_2_English.pdf', 'https://jeeadv.ac.in/past_qps/2024_2_English.pdf'),
        ],
        'Mathematics': [
            ('JEE_Advanced_2024_Paper_1_English.pdf', 'https://jeeadv.ac.in/past_qps/2024_1_English.pdf'),
            ('JEE_Advanced_2024_Paper_2_English.pdf', 'https://jeeadv.ac.in/past_qps/2024_2_English.pdf'),
        ],
    },
    'NEET': {
        'Biology': [('NEET_UG_2024_Final_Answer_Key.pdf', 'https://nta.ac.in/Download/Notice/Notice_20240726171027.pdf')],
        'Chemistry': [('NEET_UG_2024_Final_Answer_Key.pdf', 'https://nta.ac.in/Download/Notice/Notice_20240726171027.pdf')],
        'Physics': [('NEET_UG_2024_Final_Answer_Key.pdf', 'https://nta.ac.in/Download/Notice/Notice_20240726171027.pdf')],
    },
    'MHT-CET': {
        'Physics': [
            ('MHT_CET_2023_Physics_Question_Paper.pdf', 'https://drive.google.com/uc?export=download&id=12I_KcnsEaCBH5ikSix4nRkHNgIP_XrG0'),
            ('MHT_CET_2024_PCM_Question_Paper_Answer_Key.pdf', 'https://blog-itm.itmlive.in/blog/content/files/2026/01/MHT-CET-2024-PCM-Question-Paper-With-Answer-Key.pdf'),
        ],
        'Chemistry': [
            ('MHT_CET_2023_Chemistry_Question_Paper.pdf', 'https://drive.google.com/uc?export=download&id=1XrhTr6HfvhTHC4DDaESPbk4xl8ybhXqz'),
            ('MHT_CET_2024_PCM_Question_Paper_Answer_Key.pdf', 'https://blog-itm.itmlive.in/blog/content/files/2026/01/MHT-CET-2024-PCM-Question-Paper-With-Answer-Key.pdf'),
        ],
        'Mathematics': [
            ('MHT_CET_2023_Mathematics_Question_Paper.pdf', 'https://drive.google.com/uc?export=download&id=1X8lvGiiIHPoMw1fXJjtoSDHZlgdUfy3p'),
            ('MHT_CET_2024_PCM_Question_Paper_Answer_Key.pdf', 'https://blog-itm.itmlive.in/blog/content/files/2026/01/MHT-CET-2024-PCM-Question-Paper-With-Answer-Key.pdf'),
        ],
        'Biology': [
            ('MHT_CET_2023_Biology_Question_Paper.pdf', 'https://drive.google.com/uc?export=download&id=15kIjW0u5OQLJmtW9UgHJ5qiSQLyQIC2M'),
        ],
    },
}


def download(url):
    response = requests.get(url, timeout=90)
    response.raise_for_status()
    if not response.content.startswith(b'%PDF-'):
        raise ValueError(f'Not a PDF response from {url}: {response.headers.get("Content-Type")}')
    if len(response.content) > 60 * 1024 * 1024:
        raise ValueError(f'PDF is too large to include: {url}')
    return response.content


cache = {}
for exam, subjects in SOURCES.items():
    for subject, docs in subjects.items():
        folder = ROOT / exam / subject
        folder.mkdir(parents=True, exist_ok=True)
        for filename, url in docs:
            if url not in cache:
                cache[url] = download(url)
            (folder / filename).write_bytes(cache[url])
        print(f'{exam}/{subject}: {len(docs)} PDFs')

print(f'Downloaded {len(cache)} unique PDFs into {ROOT}')
