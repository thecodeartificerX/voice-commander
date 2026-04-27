// Subscribe to /api/runs/stream and update list on new runs.
(function () {
  if (!window.EventSource) return;
  var es = null;

  function startLive() {
    if (es) return;
    es = new EventSource("/api/runs/stream");
    es.addEventListener("trace.run_completed", function(ev) {
      const form = document.querySelector(".filter-bar");
      if (form) form.dispatchEvent(new Event("change"));
    });
    es.onerror = function(ev) {
      console.warn("runs.js EventSource error", ev);
      stopLive();
    };
  }

  function stopLive() {
    if (!es) return;
    es.close();
    es = null;
  }

  startLive();
  window.addEventListener("beforeunload", stopLive);
  window.__vcRunsStream = { start: startLive, stop: stopLive };
})();
