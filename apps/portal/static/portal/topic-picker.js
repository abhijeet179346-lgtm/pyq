(() => {
  document.querySelectorAll('[data-topic-picker]').forEach((picker) => {
    const form = picker.closest('form');
    const controls = picker.querySelector('.topic-search-controls');
    const search = picker.querySelector('.topic-search');
    const toggle = picker.querySelector('.topic-picker-toggle');
    const options = picker.querySelector('.topic-options');
    const hint = picker.querySelector('.topic-options-hint');
    const empty = picker.querySelector('.topic-no-results');
    const chips = picker.querySelector('.topic-chips');
    const noSelection = picker.querySelector('.topic-selection-empty');
    const chapters = [...picker.querySelectorAll('.selection-chapter')];
    let previousOpen = null;

    const setOpen = (open) => {
      options.hidden = !open;
      toggle.setAttribute('aria-expanded', String(open));
      toggle.firstChild.textContent = open ? 'Close ' : 'Browse all ';
    };

    const resetFilter = () => {
      chapters.forEach((chapter) => {
        chapter.hidden = false;
        chapter.querySelectorAll('.selection-topic, .selection-subtopics > label').forEach((item) => { item.hidden = false; });
      });
      if (previousOpen) {
        previousOpen.forEach(([item, wasOpen]) => { item.open = wasOpen; });
        previousOpen = null;
      }
      empty.hidden = true;
    };

    const filterOptions = () => {
      const query = search.value.trim().toLocaleLowerCase();
      if (!query) { resetFilter(); return; }
      if (!previousOpen) {
        previousOpen = [...picker.querySelectorAll('.selection-chapter, .selection-topic')].map((item) => [item, item.open]);
      }
      let matches = 0;
      chapters.forEach((chapter) => {
        const chapterMatches = chapter.dataset.name.toLocaleLowerCase().includes(query);
        let matchingTopics = 0;
        chapter.querySelectorAll('.selection-topic').forEach((topic) => {
          const topicMatches = topic.dataset.name.toLocaleLowerCase().includes(query);
          let matchingSubtopics = 0;
          topic.querySelectorAll('.selection-subtopics > label').forEach((subtopic) => {
            const match = subtopic.dataset.name.toLocaleLowerCase().includes(query);
            subtopic.hidden = !(chapterMatches || topicMatches || match);
            if (match) matchingSubtopics++;
          });
          const show = chapterMatches || topicMatches || matchingSubtopics > 0;
          topic.hidden = !show;
          if (show) matchingTopics++;
          topic.open = !chapterMatches && (topicMatches || matchingSubtopics > 0);
        });
        chapter.hidden = !(chapterMatches || matchingTopics > 0);
        chapter.open = !chapter.hidden;
        if (!chapter.hidden) matches++;
      });
      empty.hidden = matches > 0;
    };

    const renderChips = () => {
      const selected = [...picker.querySelectorAll('.selection-tree input[type=checkbox]:checked')];
      chips.replaceChildren();
      noSelection.hidden = selected.length > 0;
      selected.forEach((input) => {
        const chip = document.createElement('span');
        chip.className = 'topic-chip';
        const label = document.createElement('span');
        label.textContent = input.dataset.chipLabel;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'topic-chip-remove';
        remove.setAttribute('aria-label', `Remove ${input.dataset.chipLabel}`);
        remove.textContent = '×';
        remove.addEventListener('click', () => { input.checked = false; renderChips(); toggle.focus({preventScroll: true}); });
        chip.append(label, remove);
        chips.append(chip);
      });
    };

    controls.hidden = false;
    hint.hidden = false;
    setOpen(false);
    renderChips();
    search.addEventListener('focus', () => setOpen(true));
    search.addEventListener('input', () => { setOpen(true); filterOptions(); });
    search.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { setOpen(false); return; }
      if (event.key !== 'Enter') return;
      event.preventDefault();
      const query = search.value.trim().toLocaleLowerCase();
      const inputs = [...picker.querySelectorAll('.selection-tree input[type=checkbox]')];
      const first = inputs.find((input) => {
        const item = input.closest('[data-name]');
        return !input.closest('[hidden]') && (!query || item.dataset.name.toLocaleLowerCase().includes(query));
      });
      if (first) {
        first.checked = true;
        renderChips();
        search.value = '';
        resetFilter();
        setOpen(false);
      }
    });
    toggle.addEventListener('click', () => setOpen(options.hidden));
    picker.addEventListener('change', (event) => {
      if (!event.target.matches('.selection-tree input[type=checkbox]')) return;
      renderChips();
      search.value = '';
      resetFilter();
      setOpen(false);
      toggle.focus({preventScroll: true});
    });
    picker.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !options.hidden) { setOpen(false); toggle.focus({preventScroll: true}); }
    });
    form.addEventListener('selectionchange', renderChips);
    document.addEventListener('click', (event) => { if (!picker.contains(event.target)) setOpen(false); });
  });
})();
