"""Build a reproducible source-attributed import file from downloaded Parquet snapshots.
Requires optional build dependency pyarrow; the app/importer only requires JSON.
"""
import ast
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import zipfile
from collections import defaultdict, Counter
import pyarrow.parquet as pq

BASE = Path(__file__).resolve().parents[1]
SOURCES = BASE.parent.parent / 'work/pyq_sources'
DATASET = 'https://huggingface.co/datasets/datavorous/entrance-exam-dataset'

class PlainText(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','svg'): self.skip+=1
        if tag in ('br','p','div','li','tr'): self.parts.append('\n')
        if tag=='sup' and not self.skip:self.parts.append('^(')
        if tag=='sub' and not self.skip:self.parts.append('_(')
    def handle_endtag(self, tag):
        if tag in ('script','style','svg'): self.skip=max(0,self.skip-1)
        if tag in ('sup','sub') and not self.skip:self.parts.append(')')
        if tag in ('p','div','li','tr'):self.parts.append('\n')
    def handle_data(self, data):
        if not self.skip:self.parts.append(data)
def text(value):
    parser=PlainText();parser.feed(str(value or ''));return re.sub(r'\n\s*\n+', '\n\n',''.join(parser.parts)).strip()
def signature(stem):return re.sub(r'[^a-z0-9]+','',stem.casefold())

# Topic and subtopic rules refine the dataset's chapter tags. The source chapter is always preserved.
RULES = [
 ('Physics',r'capacit|electrostat', [('capacitor|capacitance','Capacitance','Capacitor networks and stored energy'),('dipole','Electric dipoles','Dipole field and potential'),('potential','Electric potential','Potential and potential energy'),('flux|gauss','Gauss law','Electric flux and charge distributions'),('charge|electric field','Electric fields','Coulomb force and field superposition')]),
 ('Physics',r'current electricity', [('meter|potentiometer|bridge','Electrical measurements','Circuit instruments and bridge balance'),('resist|wire','DC circuits','Resistance and circuit combinations'),('cell|emf|battery','Sources of current','EMF and internal resistance')]),
 ('Physics',r'magnetic|electromagnetic induction', [('flux|induc|lenz','Electromagnetic induction','Flux change and induced EMF'),('loop|coil|solenoid','Magnetic fields','Fields of current loops and solenoids'),('charge|electron|proton','Motion in magnetic fields','Lorentz force and charged particles'),('wire|current','Magnetic fields','Fields and forces due to currents')]),
 ('Physics',r'optics', [('lens','Image formation','Thin lenses and lens combinations'),('mirror','Image formation','Spherical mirrors'),('prism','Refraction','Prism deviation and dispersion'),('polar','Polarisation','Malus law and polarising filters'),('interferen|slit','Interference','Double-slit interference'),('diffract','Diffraction','Aperture diffraction'),('refractive|refract','Refraction','Refractive index and Snell law')]),
 ('Physics',r'thermodynamic|kinetic theory|thermal', [('radiat|black.body','Thermal radiation','Stefan-Boltzmann and Wien laws'),('adiabatic|isotherm','Gas processes','Isothermal and adiabatic transformations'),('entropy','Entropy','Entropy changes'),('heat|temperature','Heat transfer and calorimetry','Thermal energy and heat capacity'),('molecul|rms|gas','Kinetic theory','Molecular speeds and gas pressure')]),
 ('Physics',r'atomic|atom|dual nature|nuclear', [('decay|half.life|radioact','Radioactivity','Decay law and half-life'),('binding|mass defect','Nuclear energy','Binding energy and mass defect'),('photo|stopping','Photoelectric effect','Threshold frequency and stopping potential'),('wavelength|de broglie','Matter waves','de Broglie wavelength'),('hydrogen|bohr|spectrum','Atomic structure','Energy levels and atomic spectra')]),
 ('Physics',r'wave|oscillation', [('doppler','Sound waves','Doppler effect'),('pipe|resonan|standing','Standing waves','Resonance and harmonics'),('spring|pendulum|harmonic','Simple harmonic motion','Oscillator energy and time period'),('wave|sound','Wave propagation','Wave speed frequency and phase')]),
 ('Physics',r'gravitation', [('satellite|orbit','Orbital motion','Satellites and orbital energy'),('escape','Gravitational energy','Escape speed'),('field|acceleration','Gravitational fields','Field strength and gravitational acceleration')]),
 ('Physics',r'fluid|solid', [('capillar|surface tension|bubble','Surface tension','Capillarity and pressure difference'),('viscos|terminal','Viscosity','Stokes law and terminal speed'),('pressure|bernoulli|flow','Fluid mechanics','Pressure and fluid flow'),('young|elastic|stress|strain','Elasticity','Stress strain and elastic moduli')]),
 ('Physics',r'rotation|rotational|center of mass|centre of mass', [('collision|collid','Collisions','Momentum and energy conservation'),('torque|angular momentum','Rotational dynamics','Torque and angular momentum'),('inertia|rolling','Rigid body motion','Moment of inertia and rolling'),('mass','Systems of particles','Centre of mass')]),
 ('Physics',r'motion|laws|work|power', [('projectile|horizontal|vertical','Kinematics','Projectile and component motion'),('friction','Force and motion','Friction and contact forces'),('energy|work|power','Work and energy','Work-energy theorem and power'),('velocity|acceleration|speed','Kinematics','Equations of motion')]),
 ('Physics',r'unit|dimension|experimental|mathematics in physics', [('error|uncertain','Measurement','Errors and uncertainty'),('dimension|unit','Dimensions and units','Dimensional analysis'),('vector','Vectors','Vector operations')]),
 ('Physics',r'semiconductor', [('diode|junction','Semiconductor devices','PN junction and rectification'),('gate|logic','Digital electronics','Logic gates'),('transistor','Semiconductor devices','Transistor circuits')]),
 ('Physics',r'alternating current|electromagnetic wave', [('resonan|induct|capac','AC circuits','Impedance and resonance'),('transform','AC devices','Transformer ratios'),('wave|frequency','Electromagnetic radiation','Wave properties and spectrum')]),
 ('Chemistry',r'coordination', [('isomer','Coordination isomerism','Geometrical and optical isomers'),('magnet|spin|unpaired','Bonding in complexes','Spin states and magnetic moment'),('ligand|hybrid|geometry','Bonding in complexes','Ligands geometry and hybridisation')]),
 ('Chemistry',r'equilibrium', [('solubil|precipitat','Ionic equilibrium','Solubility product and precipitation'),('buffer','Acid-base equilibrium','Buffer solutions'),('ph|acid|base|hydrolys','Acid-base equilibrium','pH and salt hydrolysis'),('equilibrium|constant','Chemical equilibrium','Equilibrium constant and reaction quotient')]),
 ('Chemistry',r'kinetics', [('half.life|first.order','Rate laws','First-order kinetics and half-life'),('arrhenius|activation','Temperature dependence','Arrhenius equation'),('rate|order','Rate laws','Reaction order and rate equations')]),
 ('Chemistry',r'thermodynamic', [('entropy','Thermodynamic functions','Entropy and spontaneity'),('enthalpy|heat','Thermochemistry','Enthalpy changes and Hess law'),('work|gas','Thermodynamic processes','Pressure-volume work'),('gibbs|free energy','Thermodynamic functions','Gibbs energy and equilibrium')]),
 ('Chemistry',r'electrochem', [('conduct','Conductance','Molar conductivity and ionisation'),('electrolys|faraday','Electrolysis','Faraday laws'),('cell|potential|nernst','Electrochemical cells','Electrode potential and Nernst equation')]),
 ('Chemistry',r'chemical bonding|structure of atom', [('orbital|electron|quantum','Electronic structure','Orbitals and electron configuration'),('bond|hybrid|shape','Molecular structure','Bonding geometry and hybridisation')]),
 ('Chemistry',r'surface', [('adsorp','Adsorption','Physisorption and chemisorption'),('colloid|micell|emulsion','Colloids','Colloidal properties and micelles'),('catal','Catalysis','Catalyst action')]),
 ('Chemistry',r'solution|solid state|states of matter', [('osmotic|freezing|boiling','Colligative properties','Concentration-dependent properties'),('crystal|lattice|cell|packing','Crystal structures','Unit cells and packing'),('gas|pressure|volume','Gas laws','Gas mixtures and equations of state'),('solut|concentration','Solutions','Concentration and ideal behaviour')]),
 ('Chemistry',r'general organic|hydrocarbon|alcohol|aldehyde|ketone|amine|haloalkane|carboxylic', [('isomer|chiral','Organic structure','Stereochemistry and isomerism'),('oxid|reduc','Organic reactions','Oxidation and reduction'),('substitut|elimin','Organic mechanisms','Substitution and elimination'),('reaction|reagent|product','Organic reactions','Reagents and product prediction'),('acid|base','Organic properties','Acidity and basicity')]),
 ('Chemistry',r'metal|block|periodic|hydrogen|redox', [('oxidation|oxidis|reduc','Chemical reactivity','Oxidation states and redox changes'),('radius|radii|ionization|electronegat','Periodic properties','Atomic size and periodic trends'),('ore|extract|roast|bauxite','Metallurgy','Ores and extraction processes'),('compound|salt|oxide','Inorganic compounds','Composition and chemical reactions')]),
 ('Chemistry',r'basic concepts', [('mole|mass|stoichi','Stoichiometry','Mole calculations and limiting reagents')]),
 ('Mathematics',r'integr|area under', [('area|region','Applications of integration','Area of bounded regions'),('definite|integral|int','Integration methods','Definite integrals and substitutions')]),
 ('Mathematics',r'deriv|different|limit|continuity', [('maxim|minim|increas|decreas','Applications of derivatives','Extrema and monotonicity'),('tangent|normal','Applications of derivatives','Tangents and normals'),('continuous|continuity','Continuity','Continuity at a point'),('limit|lim','Limits','Evaluation of limits'),('differenti|derivative','Differentiation','Derivative rules and differentiability')]),
 ('Mathematics',r'probability|permutation', [('conditional|given|bag|defective','Conditional probability','Conditional events and Bayes theorem'),('select|arrang|ways','Combinatorics','Counting arrangements and selections'),('probability|random','Probability models','Events and sample spaces')]),
 ('Mathematics',r'complex|quadratic', [('argument|arg|modulus','Complex numbers','Modulus and principal argument'),('root|quadratic','Polynomial equations','Roots and coefficients'),('complex|locus','Complex geometry','Complex-plane loci')]),
 ('Mathematics',r'circle|ellipse|parabola|hyperbola|straight|pair of lines', [('tangent|normal','Coordinate geometry','Tangents and normals to curves'),('circle','Circles','Circle equations and chord properties'),('ellipse|parabola|hyperbola','Conic sections','Conic equations and focal properties'),('line|slope|distance','Straight lines','Line equations and distances')]),
 ('Mathematics',r'three dimensional|vector', [('plane','Three dimensional geometry','Planes and their intersections'),('line|distance','Three dimensional geometry','Lines angles and distances'),('vector|product','Vector algebra','Scalar and vector products')]),
 ('Mathematics',r'sequence|binomial|matri|determinant', [('arithmetic|progression|series|sum','Sequences and series','Progressions and finite sums'),('binomial|coefficient','Binomial theorem','Expansion and coefficients'),('matrix|matrices|determinant','Linear algebra','Matrices and determinants')]),
 ('Mathematics',r'function|sets|relation', [('one.one|onto|inject|surject','Mappings','Injectivity and surjectivity'),('range|domain','Real functions','Domain and range'),('set|relation','Sets and relations','Set operations and relations')]),
 ('Mathematics',r'trigon|triangle|height', [('triangle|angle','Trigonometry','Triangle identities and angle relations'),('equation|solution','Trigonometric equations','Solutions in restricted intervals'),('sin|cos|tan','Trigonometric functions','Identities and transformations')]),
 ('Biology',r'inheritance|evolution', [('mendel|cross|allele|genotype','Genetics','Inheritance patterns and crosses'),('dna|rna|transcript|replicat|codon','Molecular genetics','Genetic information and expression'),('evolution|selection|hardy','Evolution','Population genetics and selection')]),
 ('Biology',r'cell|biomolecule', [('mitos|meios|division','Cell division','Mitosis meiosis and cell cycle'),('enzyme|protein','Biomolecules','Proteins enzymes and function'),('membrane|organel|mitochond|ribosom','Cell structure','Organelles and cellular function'),('dna|rna|nucle','Biomolecules','Nucleic acids')]),
 ('Biology',r'classification|kingdom|living world', [('bacter|virus|viroid|fung','Biological classification','Microbes and subviral agents'),('taxon|species|genus|nomenclature','Taxonomy','Classification and nomenclature'),('animal|phylum|mammal','Animal diversity','Characteristics of animal groups'),('plant|algae|bryophy|pterido','Plant diversity','Characteristics of plant groups')]),
 ('Biology',r'reproduction|reproductive', [('fertili|embryo|gamete','Reproduction','Gametogenesis fertilisation and development'),('pollen|ovule|flower','Plant reproduction','Pollination and seed formation'),('contracept|std|sexually','Reproductive health','Contraception and infections')]),
 ('Biology',r'health|microbes|biotech', [('disease|pathogen|malaria|immun|antibod','Health and immunity','Pathogens and immune responses'),('pcr|vector|enzyme|dna','Biotechnology tools','Recombinant DNA and genetic engineering'),('microb|ferment|antibiotic','Applied microbiology','Microbes and useful products')]),
 ('Biology',r'ecosystem|population|environment|biodiversity', [('energy|trophic|food','Ecosystems','Energy flow and food webs'),('population|growth|competition','Population ecology','Growth and species interactions'),('biodiversity|conserv|extinct','Conservation','Biodiversity and conservation measures'),('pollut|ozone|greenhouse','Environment','Pollution and environmental change')]),
 ('Biology',r'photosynth|respiration|transport|nutrition|growth', [('photosynth|chlorophyll|light','Plant physiology','Photosynthesis and light reactions'),('respirat|glycol|krebs','Plant physiology','Respiration and energy metabolism'),('hormone|auxin|gibberell','Plant regulation','Hormonal control of growth'),('transport|xylem|phloem|transpir','Plant transport','Water minerals and translocation')]),
 ('Biology',r'circulation|breathing|digestion|excret|neural|chemical coordination|locomotion', [('hormone|gland','Human physiology','Endocrine regulation'),('neuron|nerve|brain|synap','Human physiology','Neural control and signalling'),('blood|heart|circul','Human physiology','Blood and circulation'),('lung|breath|oxygen','Human physiology','Respiration and gas exchange'),('kidney|nephron|urine','Human physiology','Excretion and osmoregulation'),('digest|enzyme|intestin','Human physiology','Digestion and absorption'),('muscle|bone|joint','Human physiology','Musculoskeletal function')]),
]
def classify(subject,chapter,stem):
    for subj,chap_pattern,rules in RULES:
        if subj==subject and re.search(chap_pattern,chapter,re.I):
            for pattern,topic,subtopic in rules:
                if re.search(pattern,stem,re.I):return topic,subtopic,'Rule-based content classification; editorial review pending'
    return chapter+' concepts','Concepts and applications','Chapter-based fallback; subtopic review pending'

def build():
    existing=json.loads((BASE/'data/pyq_sample.json').read_text())['questions']
    seen={signature(r['stem']) for r in existing}; output=[];report={}
    for name,slug,exam,needed in [('main','jee-main','JEE Main',494),('neet','neet','NEET',494),('advanced','jee-advanced','JEE Advanced',412)]:
        buckets=defaultdict(list);reject=Counter()
        for row in pq.read_table(SOURCES/f'{name}.parquet').to_pylist():
            try: tags=ast.literal_eval(row['tags'])
            except (ValueError,SyntaxError):reject['invalid_tags']+=1;continue
            if len(tags)<4 or tags[2]!=exam:reject['wrong_exam_or_no_paper']+=1;continue
            paper=next((x for x in tags[3:] if re.search(r'\b(?:19|20)\d{2}\b',x)),None)
            if not paper:reject['missing_year']+=1;continue
            if tags[0] not in (['Physics','Chemistry','Biology'] if slug=='neet' else ['Physics','Chemistry','Mathematics']):continue
            raw=' '.join(str(row.get(k) or '') for k in ('question','options','answer'))
            if re.search(r'<img|<svg[^>]*>.*?image|figure|diagram|as shown|following graph|following table',raw,re.I|re.S):reject['visual_dependency']+=1;continue
            stem=text(row['question']);solution=text(row['answer'])
            if len(stem)<25 or len(solution)<15:reject['incomplete_text']+=1;continue
            # Some records contain an erroneous table nesting which cannot render as a question.
            if 'http' in stem or '[IMAGE]' in raw or '\\includegraphics' in raw:reject['missing_media']+=1;continue
            options=[];correct=[]
            for attrs,body in re.findall(r'<li\b([^>]*)>(.*?)</li>',row.get('options') or '',re.S|re.I):
                label=re.search(r'class=["\']option-label["\'][^>]*>(.*?)</span>',body,re.S)
                data=re.search(r'class=["\']option-data["\'][^>]*>(.*?)</span>',body,re.S)
                if not label or not data:continue
                key=text(label[1]);value=text(data[1])
                if not value:continue
                options.append({'id':key,'text':value})
                if re.search(r'class=["\'][^"\']*\bcorrect\b',attrs):correct.append(key)
            if len(options)!=4 or len({r['text'] for r in options})!=4 or not correct:reject['incomplete_options_or_key']+=1;continue
            if signature(stem) in seen:reject['duplicate']+=1;continue
            year=int(re.search(r'\b(?:19|20)\d{2}\b',paper)[0])
            topic,subtopic,basis=classify(tags[0],tags[1],stem+' '+ ' '.join(o['text'] for o in options))
            length=len(stem.split()); multi=len(correct)>1
            score=1 if length<28 and not multi else 2 if length<65 and not multi else 3 if length<120 and not multi else 4
            if slug=='jee-advanced':score=min(5,score+1)
            item=dict(exam=slug,year=year,sitting=paper[:100],source_url=DATASET+'/blob/main/search_'+name.upper()+'.parquet#record='+str(row['source_db'])+':'+str(row['id']),source_page=None,
                section=f"{tags[0]} · Dataset ID {row['id']}",subject=tags[0],chapter=tags[1],topic=topic[:160],subtopic=subtopic[:160],question_number=row['id'],stem=stem,options=options,
                answer=correct if multi else correct[0],question_type='multi_correct' if multi else 'single_correct',solution=solution,
                answer_source_url=DATASET,source_record=f"{row['source_db']} / {row['id']} / {row['slug']}",difficulty=score,
                difficulty_basis='Heuristic estimate from text length, answer type and exam; not an official or student-calibrated rating.',taxonomy_basis=basis,
                tags=list(dict.fromkeys([exam,tags[0],tags[1],topic,subtopic,'Multiple correct' if multi else 'Single correct',str(year)])),
                license='datavorous/entrance-exam-dataset, CC BY 4.0; original examination rights retained.')
            buckets[(tags[0],tags[1])].append(item)
        if slug == 'jee-advanced':
            # JEEBench supplies additional self-contained items from newer papers.
            bench=json.loads(zipfile.ZipFile(SOURCES/'jeebench.zip').read('data/dataset.json'))
            for row in bench:
                parts=re.split(r'\n\s*\(([ABCD])\)\s*',row['question'])
                if row['type'] not in ('MCQ','MCQ(multiple)') or len(parts)!=9:continue
                if re.search(r'figure|diagram|shown|\\begin|\\end|passage|following table|list[ -]?i',row['question'],re.I):continue
                if not re.fullmatch('[ABCD]+',row['gold']):continue
                if signature(parts[0].strip()) in seen:continue
                subject={'phy':'Physics','chem':'Chemistry','math':'Mathematics'}[row['subject']]
                # Infer the chapter from closest source chapter vocabulary; record uncertainty.
                chapter='Additional concepts'
                haystack=row['question'].casefold()
                patterns={'Physics': [('hydrogen|bohr','Atomic Physics'),('nucle|radioact|decay','Nuclear Physics'),('lens|mirror','Ray Optics'),('therm|gas|entropy','Thermodynamics'),('magnet|current|wire','Magnetic Effects of Current'),('charge|potential','Electrostatics'),('angular|torque|rotat','Rotational Motion'),('wave|sound','Waves and Sound'),('dimensional','Units and Dimensions')], 'Chemistry':[('orbital|electron|atom','Structure of Atom'),('complex|ligand|coordination','Coordination Compounds'),('acid|base|equilibrium','Ionic Equilibrium'),('reaction|compound|product','General Organic Chemistry')], 'Mathematics':[('integral|\\\\int|area','Definite Integration'),('probability|random','Probability'),('complex|argument','Complex Number'),('matrix|matrices|determinant','Matrices'),('ellipse|parabola|circle','Circle'),('function|differenti','Functions'),('triangle|sin|cos|tan','Trigonometric Ratios & Identities')]}
                for pattern,label in patterns[subject]:
                    if re.search(pattern,haystack):chapter=label;break
                topic,subtopic,basis=classify(subject,chapter,row['question'])
                year=int(re.search(r'20\d\d',row['description'])[0]);paper=int(re.search(r'Paper (\d)',row['description'])[1]);multi=row['type']=='MCQ(multiple)'
                item=dict(exam=slug,year=year,sitting=f'Paper {paper}',source_url='https://github.com/dair-iitd/jeebench/blob/main/data.zip',source_page=None,subject=subject,chapter=chapter,topic=topic,subtopic=subtopic,question_number=row['index'],stem=parts[0].strip(),options=[{'id':parts[i],'text':parts[i+1].strip()} for i in range(1,9,2)],answer=list(row['gold']) if multi else row['gold'],question_type='multi_correct' if multi else 'single_correct',solution='Dataset answer key: '+', '.join(row['gold'])+'. Worked solution not yet provided.',answer_source_url='https://github.com/dair-iitd/jeebench',source_record=f"{row['description']} / {row['subject']} / {row['index']}",difficulty=4 if multi else 3,difficulty_basis='Heuristic estimate; not student-calibrated.',taxonomy_basis='Rule-based chapter and topic estimate; editorial review pending',tags=['JEE Advanced',subject,chapter,topic,subtopic,str(year)],license='JEEBench dataset (MIT), DAIR IIT Delhi, 2023; original exam rights retained.')
                buckets[(subject,chapter)].append(item)
        # Round-robin subjects and chapters, keeping paper variety and removing repeated stems.
        picked=[]
        for bucket in buckets.values():bucket.sort(key=lambda r:(-r['year'],r['question_number']))
        while len(picked)<needed and any(buckets.values()):
            for key in sorted(buckets):
                while buckets[key]:
                    row=buckets[key].pop(0)
                    sig=signature(row['stem'])
                    if sig in seen:continue
                    seen.add(sig);picked.append(row);break
                if len(picked)==needed:break
        if len(picked)!=needed:raise RuntimeError(f'{exam}: only {len(picked)} usable questions; needed {needed}')
        output+=picked
        report[slug]={'added':len(picked),'subject_counts':dict(Counter(r['subject'] for r in picked)), 'rejected':dict(reject),'fallback_taxonomy':sum('fallback' in r['taxonomy_basis'] for r in picked)}
    (BASE/'data/exam_expansion.json').write_text(json.dumps({'description':'1400 source-attributed questions; combine with the original 100 for 500 per exam.','dataset':DATASET,'questions':output},ensure_ascii=False,indent=2)+'\n')
    (BASE/'data/exam_expansion_report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__':build()
