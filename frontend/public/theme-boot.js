// Resolve the theme before first paint (external file: the CSP forbids inline scripts).
(function () {
  var choice = 'system';
  try { choice = window.localStorage.getItem('lumina.theme') || 'system'; } catch (e) { /* storage denied */ }
  if (choice !== 'light' && choice !== 'dark') {
    choice = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  document.documentElement.dataset.theme = choice;
})();
