(() => {
  const dialog = document.querySelector('.feedback-dialog');
  const launcher = document.querySelector('.feedback-launch');
  if (!dialog || !launcher) return;
  const form = dialog.querySelector('.feedback-form');
  const status = dialog.querySelector('.feedback-status');
  const submit = dialog.querySelector('.feedback-submit');
  const close = () => dialog.close();
  launcher.addEventListener('click', () => {
    status.textContent = '';
    status.dataset.state = '';
    dialog.showModal();
  });
  dialog.querySelector('.feedback-close').addEventListener('click', close);
  dialog.querySelector('.feedback-cancel').addEventListener('click', close);
  dialog.addEventListener('click', (event) => { if (event.target === dialog) close(); });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    status.textContent = '';
    status.dataset.state = '';
    submit.disabled = true;
    try {
      const response = await fetch(form.action, {
        method: 'POST', body: new FormData(form), credentials: 'same-origin',
        headers: {'Accept': 'application/json'},
      });
      const data = response.headers.get('content-type')?.includes('application/json') ? await response.json() : {};
      if (!response.ok) throw new Error(data.error || 'Could not send feedback. Please try again.');
      form.reset();
      status.textContent = data.message;
      status.dataset.state = data.notification_sent ? 'success' : 'error';
    } catch (error) {
      status.textContent = error.message || 'Could not send feedback. Please try again.';
      status.dataset.state = 'error';
    } finally {
      submit.disabled = false;
    }
  });
})();
