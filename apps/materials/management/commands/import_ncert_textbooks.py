"""Import the official chapter PDFs into the shared study library."""
import re
from pathlib import Path

import pymupdf
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import Chapter, Subject, Topic
from apps.materials.models import MaterialDocument, MaterialSection

TITLES = {
    'keph1': ['Units and Measurement', 'Motion in a Straight Line', 'Motion in a Plane', 'Laws of Motion', 'Work, Energy and Power', 'Systems of Particles and Rotational Motion', 'Gravitation'],
    'keph2': ['Mechanical Properties of Solids', 'Mechanical Properties of Fluids', 'Thermal Properties of Matter', 'Thermodynamics', 'Kinetic Theory', 'Oscillations', 'Waves'],
    'kech1': ['Some Basic Concepts of Chemistry', 'Structure of Atom', 'Classification of Elements and Periodicity in Properties', 'Chemical Bonding and Molecular Structure', 'Thermodynamics', 'Equilibrium'],
    'kech2': ['Redox Reactions', 'Organic Chemistry: Some Basic Principles and Techniques', 'Hydrocarbons'],
    'leph1': ['Electric Charges and Fields', 'Electrostatic Potential and Capacitance', 'Current Electricity', 'Moving Charges and Magnetism', 'Magnetism and Matter', 'Electromagnetic Induction', 'Alternating Current', 'Electromagnetic Waves'],
    'leph2': ['Ray Optics and Optical Instruments', 'Wave Optics', 'Dual Nature of Radiation and Matter', 'Atoms', 'Nuclei', 'Semiconductor Electronics'],
    'lech1': ['Solutions', 'Electrochemistry', 'Chemical Kinetics', 'The d- and f-Block Elements', 'Coordination Compounds'],
    'lech2': ['Haloalkanes and Haloarenes', 'Alcohols, Phenols and Ethers', 'Aldehydes, Ketones and Carboxylic Acids', 'Amines', 'Biomolecules'],
}


def clean_text(text):
    # Some NCERT PDFs encode ordinary ASCII using a private-use font mapping.
    return ''.join(chr(ord(c) - 0xf000) if 0xf020 <= ord(c) <= 0xf07e else c for c in text)


class Command(BaseCommand):
    help = 'Import NCERT Physics and Chemistry chapter PDFs as globally available study material.'

    def add_arguments(self, parser):
        parser.add_argument('directory')

    def handle(self, *args, **options):
        root = Path(options['directory']).expanduser()
        if not root.is_dir():
            raise CommandError('Textbook directory does not exist.')
        count = 0
        for path in sorted(root.rglob('*.pdf')):
            match = re.fullmatch(r'([kl]e(?:ph|ch)[12])(\d\d)\.pdf', path.name, re.I)
            if not match:
                continue
            code, number = match.group(1).lower(), int(match.group(2))
            titles = TITLES.get(code, [])
            if not 1 <= number <= len(titles):
                self.stderr.write(f'Skipped unknown chapter: {path.name}')
                continue
            grade = 11 if code[0] == 'k' else 12
            subject_name = 'Physics' if code[2:4] == 'ph' else 'Chemistry'
            title = titles[number - 1]
            source = f'https://ncert.nic.in/textbook/pdf/{path.name}'
            existing = MaterialDocument.objects.filter(tenant__isnull=True, source=source).first()
            if existing:
                self.stdout.write(f'Skipped already imported: {path.name}')
                continue
            pdf = pymupdf.open(path)
            pages = [(i + 1, clean_text(page.get_text('text')).strip()) for i, page in enumerate(pdf)]
            pdf.close()
            if not any(text for _, text in pages):
                self.stderr.write(f'No text in {path.name}; skipped')
                continue
            with transaction.atomic():
                subject, _ = Subject.objects.get_or_create(name=subject_name, defaults={'slug': subject_name.lower()})
                chapter, _ = Chapter.objects.get_or_create(subject=subject, name=title)
                topic, _ = Topic.objects.get_or_create(chapter=chapter, name=title)
                doc = MaterialDocument(tenant=None, title=f'NCERT Class {grade} {subject_name}: {title}',
                    source=source, license='NCERT textbook; copyright NCERT', default_topic=topic,
                    processing_status=MaterialDocument.ProcessingStatus.COMPLETED)
                with path.open('rb') as source_file:
                    doc.source_file.save(path.name, File(source_file), save=False)
                doc.save()
                sections = []
                heading = title
                for page_no, text in pages:
                    if not text:
                        continue
                    lines = text.splitlines()
                    for line in lines:
                        found = re.match(r'^\s*\d{1,2}\.\d+(?:\.\d+)?\s+([A-Za-z][A-Za-z ,()\-–/]{3,100})$', line.strip())
                        if found:
                            heading = found.group(1).strip().title()
                            break
                    for offset in range(0, len(text), 2500):
                        sections.append(MaterialSection(document=doc, topic=topic, title=f'{heading} · p{page_no}',
                            body=text[offset:offset+2500], page_reference=str(page_no), order=len(sections)))
                MaterialSection.objects.bulk_create(sections, batch_size=200)
            count += 1
            self.stdout.write(f'{path.name}: {len(sections)} sections', ending='\n')
        self.stdout.write(self.style.SUCCESS(f'Imported {count} chapters.'))
