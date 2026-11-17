/* Shared by every app page: lets the browser install Pergamon to the home screen.
 * Service workers only run on secure pages (HTTPS, or localhost); on plain HTTP
 * from a phone this does nothing and the site works as an ordinary web page. */
(function () {
  "use strict";
  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/sw.js").catch(function () { /* optional */ });
  }
})();
