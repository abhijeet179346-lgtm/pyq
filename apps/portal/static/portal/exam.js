/* Full-screen exam screen: palette states, navigation, autosave, timer, proctoring, keypad. */
(() => {
  const form = document.getElementById('timed-form');
  if (!form) return;
  const $ = (id) => document.getElementById(id);
  const esc = (value) => (window.CSS && CSS.escape ? CSS.escape(value) : value.replace(/"/g, '\\"'));
  const state = JSON.parse($('exam-state').textContent || '{}');
  const deadline = Date.parse(form.dataset.deadline);
  let offset = Date.parse(form.dataset.serverTime) - Date.now();
  const questions = [...document.querySelectorAll('.exam-question')];
  const chips = new Map([...document.querySelectorAll('.exam-palette .chip')].map((chip) => [chip.dataset.key, chip]));
  const tabs = [...document.querySelectorAll('.exam-sections [role=tab]')];
  const visited = new Set(state.visited || []);
  const spent = Object.assign({}, state.time_spent || {});
  const limit = state.limit || null;
  let violations = state.violations || 0;
  let pending = [];
  let active = 0, dirty = false, trackDirty = false, saving = false, finishing = false, started = false, retry = null;
  let lastTick = Date.now();
  const warned = new Set();
  const status = $('save-status');
  const fsSupported = !!(document.documentElement.requestFullscreen && document.fullscreenEnabled !== false);

  const LABEL = { 'not-visited': 'not visited', 'not-answered': 'not answered', answered: 'answered', marked: 'marked for review', 'answered-marked': 'answered and marked for review' };
  const fields = (key) => [...form.querySelectorAll(`[name="answer_${esc(key)}"]`)];
  const reviewBox = (key) => form.querySelector(`[name="review_${esc(key)}"]`);
  const isAnswered = (key) => fields(key).some((f) => (f.type === 'text' ? f.value.trim() !== '' : f.checked));
  const stateOf = (key) => {
    if (!visited.has(key)) return 'not-visited';
    const a = isAnswered(key), m = reviewBox(key).checked;
    return a && m ? 'answered-marked' : m ? 'marked' : a ? 'answered' : 'not-answered';
  };
  const current = () => questions[active];

  function toast(message, tone) {
    const el = $('exam-toast');
    el.textContent = message;
    el.dataset.tone = tone || 'info';
    el.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { el.hidden = true; }, 5000);
  }

  function paint() {
    const counts = {};
    const subject = current().dataset.subject;
    questions.forEach((q, i) => {
      const key = q.dataset.key, s = stateOf(key), chip = chips.get(key);
      counts[s] = (counts[s] || 0) + (q.dataset.subject === subject ? 1 : 0);
      chip.dataset.state = s;
      chip.hidden = q.dataset.subject !== subject;
      chip.setAttribute('aria-label', `Question ${i + 1}, ${LABEL[s]}`);
      if (i === active) chip.setAttribute('aria-current', 'true'); else chip.removeAttribute('aria-current');
    });
    document.querySelectorAll('[data-count]').forEach((el) => { el.textContent = counts[el.dataset.count] || 0; });
    $('palette-subject').textContent = subject;
    tabs.forEach((tab) => {
      const on = tab.dataset.subject === subject;
      tab.setAttribute('aria-selected', String(on));
      tab.tabIndex = on ? 0 : -1;
    });
  }

  function account() {
    const now = Date.now();
    if (started && document.visibilityState === 'visible' && current()) {
      const key = current().dataset.key;
      spent[key] = (spent[key] || 0) + (now - lastTick) / 1000;
    }
    lastTick = now;
  }

  function show(index, focus) {
    if (index < 0 || index >= questions.length) return;
    account();
    active = index;
    const q = current();
    if (!visited.has(q.dataset.key)) { visited.add(q.dataset.key); trackDirty = true; }
    questions.forEach((item, i) => { item.hidden = i !== index; });
    $('question-area').scrollTop = 0;
    paint();
    if (focus) q.querySelector('h2').focus({ preventScroll: true });
  }

  const recordEvents = form.dataset.recordEvents !== 'false';
  function fillTracking() {
    account();
    // Timed tests send visited/time_spent/events; institute tests send time_<id> and tab_switch_count.
    if (form.elements.visited) form.elements.visited.value = [...visited].join(',');
    if (form.elements.time_spent) form.elements.time_spent.value = JSON.stringify(Object.fromEntries(Object.entries(spent).map(([k, v]) => [k, Math.round(v)])));
    if (form.elements.events) form.elements.events.value = JSON.stringify(pending);
    questions.forEach((q) => { const field = form.elements['time_' + q.dataset.key]; if (field) field.value = Math.round(spent[q.dataset.key] || 0); });
    if (form.elements.tab_switch_count) form.elements.tab_switch_count.value = violations;
  }

  async function save() {
    if (saving || finishing) return;
    saving = true;
    clearTimeout(retry);
    status.textContent = 'Saving…';
    status.dataset.tone = 'info';
    fillTracking();
    const sent = pending.length;
    const data = new FormData(form);
    data.set('action', 'save');
    try {
      const response = await fetch(location.href, { method: 'POST', body: data, credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } });
      if (!response.ok) throw new Error(String(response.status));
      const payload = await response.json();
      if (payload.result_url) { finishing = true; location.href = payload.result_url; return; }
      if (payload.reload_url) { finishing = true; location.href = payload.reload_url; return; }
      pending = pending.slice(sent);
      if (typeof payload.violations === 'number') violations = payload.violations;
      if (payload.server_time) offset = Date.parse(payload.server_time) - Date.now();
      dirty = false;
      trackDirty = false;
      status.textContent = 'Saved ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      status.dataset.tone = 'ok';
    } catch (_) {
      status.textContent = navigator.onLine === false ? 'Offline — answers kept here, retrying…' : 'Not saved — retrying…';
      status.dataset.tone = 'bad';
      retry = setTimeout(save, 4000);
    } finally {
      saving = false;
    }
  }

  const changed = () => { dirty = true; paint(); };
  form.addEventListener('change', changed);
  form.addEventListener('input', (event) => {
    if (event.target.classList.contains('exam-numeric-input')) {
      const clean = event.target.value.replace(/[^0-9.\-]/g, '').replace(/(?!^)-/g, '').replace(/(\..*)\./g, '$1');
      if (clean !== event.target.value) event.target.value = clean;
    }
    changed();
  });
  setInterval(() => { if (dirty || trackDirty || pending.length) save(); }, 10000);

  function next() {
    if (active < questions.length - 1) show(active + 1, true);
    else toast('This is the last question. Review the palette, then select Submit.', 'info');
  }
  $('save-next').addEventListener('click', () => { if (dirty) save(); next(); });
  $('test-previous').addEventListener('click', () => show(active - 1, true));
  $('mark-next').addEventListener('click', () => { reviewBox(current().dataset.key).checked = true; changed(); save(); next(); });
  $('clear-response').addEventListener('click', () => {
    fields(current().dataset.key).forEach((f) => { if (f.type === 'text') f.value = ''; else f.checked = false; });
    reviewBox(current().dataset.key).checked = false;
    changed();
  });
  chips.forEach((chip) => chip.addEventListener('click', () => {
    show(questions.findIndex((q) => q.dataset.key === chip.dataset.key), true);
    document.body.classList.remove('palette-open');
  }));
  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => show(questions.findIndex((q) => q.dataset.subject === tab.dataset.subject), true));
    tab.addEventListener('keydown', (event) => {
      if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
      event.preventDefault();
      const target = tabs[(i + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
      target.focus();
      target.click();
    });
  });

  // Numeric keypad
  document.querySelectorAll('.exam-keypad').forEach((pad) => pad.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-key]');
    if (!button) return;
    const input = $(pad.dataset.target);
    let value = input.value;
    const key = button.dataset.key;
    if (key === 'back') value = value.slice(0, -1);
    else if (key === 'clear') value = '';
    else if (key === '-') value = value.startsWith('-') ? value.slice(1) : '-' + value;
    else if (key === '.') { if (!value.includes('.')) value += value === '' || value === '-' ? '0.' : '.'; }
    else if (value.length < 20) value += key;
    input.value = value;
    changed();
  }));

  // Keyboard shortcuts (ignored while typing or when a dialog/overlay is open)
  document.addEventListener('keydown', (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.target.matches('input[type=text], textarea') || $('submit-dialog').open || !$('fullscreen-overlay').hidden) return;
    const key = event.key.toLowerCase();
    if (key === 'arrowright' || key === 'n') { event.preventDefault(); $('save-next').click(); }
    else if (key === 'arrowleft' || key === 'p') { event.preventDefault(); $('test-previous').click(); }
    else if (key === 'm') $('mark-next').click();
    else if (key === 'c') $('clear-response').click();
    else {
      const index = 'abcd'.indexOf(key) >= 0 ? 'abcd'.indexOf(key) : '1234'.indexOf(key);
      const option = index >= 0 ? current().querySelectorAll('.exam-option input')[index] : null;
      if (option) { option.checked = option.type === 'checkbox' ? !option.checked : true; changed(); }
    }
  });

  // Submit dialog with a per-section summary
  const dialog = $('submit-dialog');
  $('open-submit').addEventListener('click', () => {
    const rows = {};
    questions.forEach((q) => {
      const subject = q.dataset.subject;
      rows[subject] = rows[subject] || { answered: 0, 'not-answered': 0, marked: 0, 'answered-marked': 0, 'not-visited': 0 };
      rows[subject][stateOf(q.dataset.key)] += 1;
    });
    const body = $('submit-summary');
    body.replaceChildren(...Object.entries(rows).map(([subject, c]) => {
      const tr = document.createElement('tr');
      [subject, c.answered, c['not-answered'], c.marked, c['answered-marked'], c['not-visited']].forEach((value, i) => {
        const cell = document.createElement(i ? 'td' : 'th');
        if (!i) cell.scope = 'row';
        cell.textContent = value;
        tr.append(cell);
      });
      return tr;
    }));
    if (dialog.showModal) dialog.showModal(); else if (confirm('Submit this paper? Answers cannot be changed afterwards.')) $('confirm-submit').click();
  });
  $('cancel-submit').addEventListener('click', () => dialog.close());
  form.addEventListener('submit', () => { fillTracking(); finishing = true; });

  // Timer with warnings and automatic submission
  function tick() {
    account();
    const left = Math.max(0, deadline - (Date.now() + offset));
    const h = Math.floor(left / 3600000), m = Math.floor(left / 60000) % 60, s = Math.floor(left / 1000) % 60;
    $('test-clock').textContent = `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    const timer = $('exam-timer');
    timer.dataset.tone = left <= 60000 ? 'bad' : left <= 300000 ? 'warn' : left <= 900000 ? 'soon' : '';
    let notice = null;
    for (const [ms, text] of [[900000, '15 minutes left.'], [300000, '5 minutes left. Check unanswered questions.'], [60000, '1 minute left. The paper submits automatically.']]) {
      if (left <= ms && left > 0 && !warned.has(ms)) { warned.add(ms); notice = [text, ms <= 300000 ? 'bad' : 'warn']; }
    }
    if (notice) toast(...notice);  // only the most urgent new warning, so a resumed paper doesn't stack them
    if (left <= 0 && !finishing) {
      finishing = true;
      fillTracking();
      const action = document.createElement('input');
      Object.assign(action, { type: 'hidden', name: 'action', value: form.dataset.timeoutAction || 'submit' });
      form.append(action);
      form.submit();
    }
  }

  // Full screen and tab-switch recording
  const overlay = $('fullscreen-overlay');
  function openOverlay(title, message, button) {
    $('overlay-title').textContent = title;
    $('overlay-message').textContent = message;
    $('overlay-action').textContent = button;
    overlay.hidden = false;
    $('overlay-action').focus();
  }
  function recordViolation(type) {
    if (!started || finishing) return;
    if (!recordEvents) return 'Please stay in full screen until you finish.';
    violations += 1;
    pending.push({ type });
    save();
    return limit ? `This has been recorded (${violations} of ${limit}). At ${limit} the paper is submitted automatically.` : `This has been recorded (${violations} so far) and appears on your result.`;
  }
  $('overlay-action').addEventListener('click', () => {
    overlay.hidden = true;
    started = true;
    lastTick = Date.now();
    current().querySelector('h2').focus({ preventScroll: true });
    // Request inside the click (user gesture) but never wait on it: some browsers leave the promise pending.
    if (fsSupported && !document.fullscreenElement) {
      try { const request = document.documentElement.requestFullscreen({ navigationUI: 'hide' }); if (request) request.catch(() => {}); } catch (_) {}
    }
  });
  document.addEventListener('fullscreenchange', () => {
    const fs = !!document.fullscreenElement;
    $('fullscreen-toggle').setAttribute('aria-label', fs ? 'Exit full screen' : 'Enter full screen');
    if (!fs && started && !finishing) {
      const note = recordViolation('fullscreen_exit');
      openOverlay('You left full screen', note || '', 'Return to full screen');
    }
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') { account(); const note = recordViolation('tab_hidden'); if (note) toast('You switched away from the test. ' + note, 'bad'); }
    else lastTick = Date.now();
  });
  if (fsSupported) {
    const toggle = $('fullscreen-toggle');
    toggle.hidden = false;
    toggle.addEventListener('click', () => { if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen().catch(() => {}); });
    openOverlay('This test runs in full screen', 'Full screen keeps the paper free of distractions.' + (recordEvents ? ' Leaving full screen or switching tabs is recorded' + (limit ? `, and after ${limit} times the paper is submitted automatically.` : '.') : ''), 'Continue in full screen');
  } else {
    started = true;
  }

  // Text size, mobile palette
  const root = document.documentElement;
  let scale = 1;
  try { scale = parseFloat(localStorage.getItem('pyq-exam-scale')) || 1; } catch (_) {}
  const applyScale = () => root.style.setProperty('--exam-scale', String(scale));
  applyScale();
  document.querySelectorAll('[data-zoom]').forEach((button) => button.addEventListener('click', () => {
    scale = Math.min(1.5, Math.max(0.85, Math.round((scale + 0.1 * Number(button.dataset.zoom)) * 100) / 100));
    applyScale();
    try { localStorage.setItem('pyq-exam-scale', String(scale)); } catch (_) {}
  }));
  $('palette-toggle').addEventListener('click', () => {
    const open = document.body.classList.toggle('palette-open');
    $('palette-toggle').setAttribute('aria-expanded', String(open));
  });
  $('palette-close').addEventListener('click', () => { document.body.classList.remove('palette-open'); $('palette-toggle').setAttribute('aria-expanded', 'false'); });

  window.addEventListener('beforeunload', (event) => { if (dirty && !finishing) { event.preventDefault(); event.returnValue = ''; } });
  // Flush answers, visits and time when the page is closed or hidden for good.
  window.addEventListener('pagehide', () => {
    if (finishing || !navigator.sendBeacon) return;
    fillTracking();
    const data = new FormData(form);
    data.set('action', 'save');
    if (navigator.sendBeacon(location.href, data)) pending = [];
  });

  const firstUnvisited = questions.findIndex((q) => !visited.has(q.dataset.key));
  show(firstUnvisited > 0 ? firstUnvisited - 1 : 0, false);
  tick();
  setInterval(tick, 500);
})();
