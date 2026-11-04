/* Practice question: check in place, show solution, bookmark, session palette, timer, keys. */
(() => {
  const form = document.getElementById('practice-form');
  if (!form) return;
  const $ = (id) => document.getElementById(id);
  const feedback = $('practice-feedback');
  const error = $('practice-error');
  const quiz = form.dataset.mode === 'quiz';
  let busy = false;
  let done = !feedback.hidden;

  const text = (tag, value, attrs) => { const el = document.createElement(tag); if (value != null) el.textContent = value; Object.assign(el, attrs || {}); return el; };
  const pill = (state, label) => { const p = text('p', label, { className: 'result-pill-state' }); p.dataset.state = state; return p; };

  function render(data) {
    const box = text('div', null, { className: 'practice-result' });
    box.dataset.state = data.state;
    if (data.state === 'correct') box.append(pill('correct', '✓ Correct'));
    else if (data.state === 'wrong') {
      box.append(pill('incorrect', '✗ Not quite'));
      const p = text('p', 'Correct answer: ');
      p.append(text('strong', data.answer));
      if (!form.querySelector('.exam-option') && data.selected) p.append(' · your answer: ' + data.selected.join(', '));
      box.append(p);
    } else if (data.state === 'revealed') {
      box.append(pill('unanswered', 'Solution viewed'));
      const p = text('p', 'Answer: '); p.append(text('strong', data.answer)); box.append(p);
    } else if (data.state === 'saved') {
      box.append(pill('unanswered', 'Answer saved'), text('p', "You'll see whether it was right, with the solution, at the end of the quiz."));
    }
    if (data.state !== 'saved' && data.solution) box.append(text('h3', 'Explanation'), text('div', data.solution, { className: 'practice-solution' }));
    if (data.recommendations && data.recommendations.length) {
      const list = text('ul', null, { className: 'practice-links' });
      data.recommendations.forEach((item) => { const li = text('li'); li.append(text('a', item.title, { href: item.url })); list.append(li); });
      box.append(text('h3', 'Study these related sections'), list);
    }
    feedback.replaceChildren(box);
    feedback.hidden = false;
    if (window.MathJax && MathJax.typesetPromise) MathJax.typesetPromise([feedback]).catch(() => {});
  }

  function mark(data) {
    const fieldset = form.querySelector('fieldset');
    if (fieldset) fieldset.disabled = true;
    const numeric = form.querySelector('.exam-numeric-input');
    if (numeric) numeric.disabled = true;
    form.querySelector('.practice-buttons').hidden = true;
    if (data.state === 'saved') return;
    const answers = new Set(data.answer_ids || []);
    const selected = new Set(data.selected || []);
    form.querySelectorAll('.exam-option').forEach((label) => {
      const id = label.dataset.id, tag = label.querySelector('.practice-tag');
      label.classList.toggle('is-answer', answers.has(id));
      label.classList.toggle('is-wrong', selected.has(id) && !answers.has(id));
      tag.textContent = answers.has(id) && selected.has(id) ? '✓ Your answer · correct' : answers.has(id) ? '✓ Correct answer' : selected.has(id) ? '✗ Your answer' : '';
    });
  }

  function updateSession(data) {
    if (!data.palette) return;
    data.palette.forEach((chip) => {
      const link = document.querySelector(`.practice-palette [data-position="${chip.position}"]`);
      if (link) link.dataset.state = chip.state;
    });
    const set = (name, value) => { const el = document.querySelector(`[data-stat="${name}"]`); if (el) el.textContent = value; };
    set('answered', data.answered);
    set('correct', data.correct);
    set('accuracy', data.answered ? Math.round((100 * data.correct) / data.answered) + '%' : '—');
    const bar = document.querySelector('.practice-bar');
    if (bar) { const max = Number(bar.getAttribute('aria-valuemax')) || 1; bar.setAttribute('aria-valuenow', data.answered); bar.firstElementChild.style.width = (100 * data.answered) / max + '%'; }
    const next = $('next-link');
    if (next) { next.textContent = next.dataset.answeredLabel; next.classList.replace('ghost', 'primary'); }
  }

  async function send(action) {
    if (busy || done) return;
    const data = new FormData(form);
    if (action === 'reveal') data.set('action', 'reveal');
    else if (!data.getAll('answer').some((v) => String(v).trim())) { error.textContent = 'Choose or enter an answer first.'; error.hidden = false; return; }
    error.hidden = true;
    busy = true;
    try {
      const response = await fetch(form.getAttribute('action'), { method: 'POST', body: data, credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || 'This question changed or the page expired. Reload and try again.');
      done = true;
      mark(payload);
      render(payload);
      updateSession(payload);
      const next = $('next-link');
      (next || feedback).focus({ preventScroll: true });
      feedback.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (err) {
      error.textContent = err.message || 'Could not reach the server. Check your connection and try again.';
      error.hidden = false;
    } finally {
      busy = false;
    }
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    send(event.submitter && event.submitter.value === 'reveal' ? 'reveal' : 'check');
  });

  // Bookmark toggle without leaving the page
  const bookmark = document.querySelector('.bookmark-form');
  if (bookmark) bookmark.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = bookmark.querySelector('button');
    try {
      const response = await fetch(bookmark.getAttribute('action'), { method: 'POST', body: new FormData(bookmark), credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } });
      const payload = await response.json();
      button.setAttribute('aria-pressed', String(payload.bookmarked));
      button.textContent = payload.bookmarked ? '★ Bookmarked' : '☆ Bookmark';
    } catch (_) { bookmark.submit(); }
  });

  // Time on this question (informational)
  const timer = $('practice-timer');
  if (timer && !done) {
    const start = Date.now();
    const tick = () => { if (done) return; const s = Math.floor((Date.now() - start) / 1000); timer.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
    setInterval(tick, 1000);
  } else if (timer) timer.textContent = 'Answered';

  // Keyboard shortcuts
  document.addEventListener('keydown', (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.target.matches('input[type=text], textarea, select')) return;
    const key = event.key.toLowerCase();
    if (key === 'arrowright' && $('next-link')) location.href = $('next-link').href;
    else if (key === 'arrowleft' && $('prev-link')) location.href = $('prev-link').href;
    else if (key === 'enter' && !done && !event.target.matches('button, a')) { event.preventDefault(); send('check'); }
    else if (key === 's' && !done && !quiz) send('reveal');
    else if (key === 'b' && bookmark) bookmark.requestSubmit();
    else if (!done) {
      const index = 'abcd'.indexOf(key) >= 0 ? 'abcd'.indexOf(key) : '1234'.indexOf(key);
      const option = index >= 0 ? form.querySelectorAll('.exam-option input')[index] : null;
      if (option) option.checked = option.type === 'checkbox' ? !option.checked : true;
    }
  });

  // Mobile session panel
  const toggle = $('palette-toggle');
  if (toggle) {
    toggle.addEventListener('click', () => { const open = document.body.classList.toggle('palette-open'); toggle.setAttribute('aria-expanded', String(open)); });
    $('palette-close').addEventListener('click', () => { document.body.classList.remove('palette-open'); toggle.setAttribute('aria-expanded', 'false'); });
  }
})();
