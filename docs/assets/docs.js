(() => {
  'use strict';
  const root = document.documentElement;
  const theme = document.querySelector('#theme-toggle');
  try { root.dataset.theme = localStorage.getItem('pocket-docs-theme') || 'dark'; } catch (_) { root.dataset.theme = 'dark'; }
  theme.addEventListener('click', () => {
    root.dataset.theme = root.dataset.theme === 'light' ? 'dark' : 'light';
    try { localStorage.setItem('pocket-docs-theme', root.dataset.theme); } catch (_) {}
  });
  const nav = document.querySelector('#nav-toggle');
  nav.addEventListener('click', () => {
    const open = document.querySelector('#sidebar').classList.toggle('open');
    nav.setAttribute('aria-expanded', String(open));
  });
  const input = document.querySelector('#doc-search');
  const results = document.querySelector('#search-results');
  const status = document.querySelector('#search-status');
  const close = () => { results.hidden = true; input.setAttribute('aria-expanded', 'false'); };
  input.addEventListener('input', () => {
    const words = input.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    results.replaceChildren();
    if (!words.length) { close(); status.textContent = ''; return; }
    const matches = (window.POCKET_DOCS || []).filter(entry => {
      const haystack = `${entry.title} ${entry.page} ${entry.text}`.toLocaleLowerCase();
      return words.every(word => haystack.includes(word));
    }).sort((a, b) => {
      const score = entry => words.filter(word => entry.title.toLocaleLowerCase().includes(word)).length;
      return score(b) - score(a);
    }).slice(0, 12);
    for (const entry of matches) {
      const link = document.createElement('a');
      link.href = entry.url;
      link.textContent = entry.title;
      const detail = document.createElement('small'); detail.textContent = entry.page;
      link.append(detail); results.append(link);
    }
    if (!matches.length) { const p = document.createElement('p'); p.textContent = 'No matching sections. Try a command, plugin name, or setting.'; results.append(p); }
    results.hidden = false; input.setAttribute('aria-expanded', 'true');
    status.textContent = `${matches.length} matching sections shown`;
  });
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') close();
    if (event.key === 'ArrowDown') { results.querySelector('a')?.focus(); event.preventDefault(); }
    if (event.key === 'Enter') results.querySelector('a')?.click();
  });
  results.addEventListener('keydown', event => {
    const links = [...results.querySelectorAll('a')];
    const index = links.indexOf(document.activeElement);
    if (event.key === 'Escape') { close(); input.focus(); }
    if (event.key === 'ArrowDown') { links[Math.min(index + 1, links.length - 1)]?.focus(); event.preventDefault(); }
    if (event.key === 'ArrowUp') { if (index <= 0) input.focus(); else links[index - 1].focus(); event.preventDefault(); }
  });
  document.addEventListener('click', event => { if (!event.target.closest('.search-wrap')) close(); });
  document.addEventListener('keydown', event => {
    const editing = /INPUT|TEXTAREA/.test(document.activeElement?.tagName || '');
    if ((event.key === '/' && !editing) || (event.key.toLowerCase() === 'k' && (event.ctrlKey || event.metaKey))) { event.preventDefault(); input.focus(); }
  });
  async function copy(value) {
    if (navigator.clipboard?.writeText) { try { await navigator.clipboard.writeText(value); return; } catch (_) {} }
    const area = document.createElement('textarea'); area.value = value;
    area.style.position = 'fixed'; area.style.opacity = '0'; document.body.append(area);
    area.select(); const success = document.execCommand('copy'); area.remove();
    if (!success) throw new Error('Clipboard unavailable');
  }
  document.querySelectorAll('.copy-code').forEach(button => button.addEventListener('click', async () => {
    try { await copy(button.closest('.code-block').querySelector('code').textContent); button.textContent = 'Copied'; }
    catch (_) { button.textContent = 'Select code to copy'; }
    setTimeout(() => { button.textContent = 'Copy'; }, 1800);
  }));
  document.querySelectorAll('.print-doc').forEach(button => button.addEventListener('click', () => window.print()));
})();
