(() => {
  document.querySelectorAll('.clear-selection').forEach((button) => {
    button.addEventListener('click', () => {
      const form = button.closest('form');
      form.querySelectorAll('.selection-tree input[type=checkbox]').forEach((input) => { input.checked = false; });
      form.dispatchEvent(new Event('selectionchange'));
    });
  });
  const button = document.querySelector('.theme-toggle');
  if (!button) return;
  const apply = (dark) => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    document.body.classList.toggle('vault-dark', dark);
    button.setAttribute('aria-pressed', String(dark));
    button.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
  };
  let saved = null;
  try { saved = localStorage.getItem('pyq-vault-theme'); } catch (_) {}
  apply(saved === 'dark' || (saved === null && window.matchMedia('(prefers-color-scheme: dark)').matches));
  button.addEventListener('click', () => {
    const dark = !document.body.classList.contains('vault-dark');
    apply(dark);
    try { localStorage.setItem('pyq-vault-theme', dark ? 'dark' : 'light'); } catch (_) {}
  });
})();
