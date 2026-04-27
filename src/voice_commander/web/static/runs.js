// Subscribe to /api/runs/stream and update list on new runs.
(function () {
  if (!window.EventSource) return;
  const es = new EventSource("/api/runs/stream");
  es.addEventListener("trace.run_completed", function(ev) {
    const form = document.querySelector(".filter-bar");
    if (form) form.dispatchEvent(new Event("change"));
  });
  window.__vcRunsStream = es;
})();
