// Applied before first paint so the page never flashes the wrong theme (CSP forbids inline scripts).
(function () {
  var t = null;
  try { t = localStorage.getItem("screener-theme"); } catch (e) { /* storage disabled */ }
  if (!t) t = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", t);
})();
