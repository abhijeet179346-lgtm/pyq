(() => {
  const form = document.getElementById('library-search');
  if (!form) return;
  form.querySelectorAll('[data-library-filter]').forEach((select) => {
    select.addEventListener('change', () => {
      if (select.dataset.libraryFilter === 'exam') form.elements.subject.value = '';
      if (select.dataset.libraryFilter !== 'chapter') form.elements.chapter.value = '';
      form.requestSubmit();
    });
  });
})();
