// Resolve the theme before first paint (external file: the CSP forbids inline scripts).
(function () {
  var choice = 'system';
  try { choice = window.localStorage.getItem('lumina.theme') || 'system'; } catch (e) { /* storage denied */ }
  if (choice !== 'light' && choice !== 'dark' && choice !== 'oled') {
    choice = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  document.documentElement.dataset.theme = choice;
  var paper = choice === 'oled' ? '#000' : choice === 'light' ? '#f4f1ea' : '#0f0e0c';
  document.querySelectorAll('meta[name="theme-color"]').forEach(function (meta) { meta.content = paper; });
})();
