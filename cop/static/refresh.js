// Reload the page every 60 seconds so it shows the newest server snapshot.
// This is the only script. It makes no requests of its own (the Content Security Policy
// sets connect-src 'none'), and it waits while the tab is hidden.
(function () {
  "use strict";
  var seconds = Number(document.body.getAttribute("data-reload-seconds")) || 60;

  function reloadWhenVisible() {
    if (document.visibilityState === "visible") {
      window.location.reload();
      return;
    }
    document.addEventListener("visibilitychange", function onVisible() {
      if (document.visibilityState === "visible") {
        document.removeEventListener("visibilitychange", onVisible);
        window.location.reload();
      }
    });
  }

  window.setTimeout(reloadWhenVisible, seconds * 1000);
})();
