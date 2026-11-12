// Campaign-side conventions used by the test funnel: content toggles
// (?timer=n etc.) and carrying the query string onto funnel links.
(function () {
  var q = new URLSearchParams(location.search);
  ['exit', 'timer', 'reviews', 'loading', 'banner', 'seen'].forEach(function (k) {
    if (q.get(k) === 'n') document.querySelectorAll('[data-toggle-param="' + k + '"]').forEach(function (el) { el.style.display = 'none'; });
  });
  if (location.search) document.querySelectorAll('a[data-carry]').forEach(function (a) { a.href = a.getAttribute('href') + location.search; });
})();
