(() => {
  const levels = ['exam', 'subject', 'chapter', 'topic', 'subtopic'];
  document.querySelectorAll('form.filters').forEach((form) => {
    form.querySelectorAll('select').forEach((select) => {
      const level = levels.indexOf(select.name);
      if (level < 0) return;
      select.addEventListener('change', () => {
        levels.slice(level + 1).forEach((name) => {
          const field = form.elements.namedItem(name);
          if (!field) return;
          if (field.type === 'hidden') field.remove();
          else field.value = '';
        });
      });
    });
  });
})();
