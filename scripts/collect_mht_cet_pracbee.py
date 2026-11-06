"""Extract candidate 2024 MHT-CET rows from public, source-linked shift pages.

Run from the project root with ``.venv/bin/python scripts/collect_mht_cet_pracbee.py``.
This creates a candidate JSON file; it does not import or publish questions.
"""
import concurrent.futures
import json
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup, NavigableString
from mathml2latex.mathml import process_mathml


PAPERS = tuple(f'{day}-may-{shift}' for day in
               ('2nd', '3rd', '4th', '9th', '10th', '11th', '15th', '16th')
               for shift in ('morning', 'evening'))
SUBJECTS = ('physics', 'chemistry', 'mathematics')
BASE = 'https://hive.pracbee.com/mht-cet/papers/mht-cet-2024-{}-shift/subjects/{}'


def render(node):
    for container in list(node.select('mjx-container')):
        math = container.select_one('mjx-assistive-mml math')
        if not math:
            return None
        converted = process_mathml(BeautifulSoup(str(math), 'xml'))
        if not converted or len(converted) > 100 or any(
                marker in converted for marker in ('Undefined', r'\begin', r'\munder', r'\mspace')):
            return None
        container.replace_with(NavigableString(' $' + converted + '$ '))
    return ' '.join(node.get_text(' ', strip=True).split())


def extract(task):
    shift, subject, page = task
    url = BASE.format(shift, subject) + (f'/page/{page}' if page > 1 else '')
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    articles = soup.select('article.surface')
    flight = ''.join(
        parsed[1]
        for script in soup.find_all('script')
        for match in re.finditer(r'self\.__next_f\.push\((\[.*?\])\)', script.string or '', re.S)
        if isinstance((parsed := json.loads(match.group(1)))[1], str)
    )
    answers = re.findall(r'correct_options_json":"\[\\"([A-D])\\"\]"', flight)
    if len(articles) != len(answers):
        return task, [], f'answer/card mismatch: {len(answers)}/{len(articles)}'
    rows = []
    for index, (article, answer) in enumerate(zip(articles, answers), start=1):
        question = article.select_one('.question-html')
        choices = [button for button in article.find_all('button')
                   if 'min-h-14' in (button.get('class') or [])]
        if not question or len(choices) != 4:
            continue
        if any(part.find(['img', 'table', 'svg']) for part in (question, *choices)):
            continue
        stem = render(question)
        options = [render(choice) for choice in choices]
        if not stem or any(not option for option in options):
            continue
        options = [option[2:].strip() for option in options]
        text = ' '.join((stem, *options))
        if len(stem) < 25 or len(stem) > 300 or any(len(o) < 2 or len(o) > 120 for o in options):
            continue
        if len(set(options)) != 4 or any(word in stem.casefold() for word in
                                         ('following figure', 'shown in the figure', 'shown in figure',
                                          'following graph', 'following diagram', 'shown below')):
            continue
        if any(marker in text for marker in ('Undefined', r'\begin', r'\munder', r'\mspace',
                                            'NaNO/', '::', '[^{', '\ufffd')):
            continue
        if any(value.count('$') % 2 for value in (stem, *options)):
            continue
        rows.append(dict(shift=shift, subject=subject.title(),
                         number=(page - 1) * 25 + index, stem=stem,
                         options=options, answer=answer, source=url))
    return task, rows, len(articles)


def main():
    tasks = [(shift, subject, page) for shift in PAPERS for subject in SUBJECTS for page in (1, 2)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(extract, tasks))
    rows = []
    for task, extracted, count in results:
        print(task, len(extracted), '/', count)
        rows.extend(extracted)
    output = Path('data/mht_cet_pracbee_candidates.json')
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    print('Candidates:', len(rows), 'at', output)


if __name__ == '__main__':
    main()
