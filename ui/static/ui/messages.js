/* Auto-dismiss and dismissible flash messages.
 * Auto-dismisses .msg-success and .msg-info after 5000ms.
 * Errors/warnings remain visible.
 */
(function () {
  "use strict";

  function initMessages() {
    var messages = document.querySelectorAll(".messages .msg");
    if (messages.length === 0) return;

    var AUTO_DISMISS_TYPES = ["success", "info"];
    var DISMISS_DELAY = 5000;
    var HIDE_ANIM_MS = 400;

    messages.forEach(function (msg) {
      // Add dismiss button
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "msg-dismiss";
      btn.setAttribute("aria-label", "Dismiss message");
      btn.innerHTML = "&times;";
      btn.addEventListener("click", function () {
        dismiss(msg);
      });
      msg.appendChild(btn);

      // Auto-dismiss success/info
      var tags = msg.className.split(/\s+/).filter(function (c) {
        return c.indexOf("msg-") === 0;
      });
      var shouldAutoDismiss = false;
      for (var i = 0; i < tags.length; i++) {
        var t = tags[i].replace("msg-", "");
        if (AUTO_DISMISS_TYPES.indexOf(t) !== -1) {
          shouldAutoDismiss = true;
          break;
        }
      }
      if (shouldAutoDismiss) {
        setTimeout(function () {
          dismiss(msg);
        }, DISMISS_DELAY);
      }
    });

    function dismiss(el) {
      if (!el || el.classList.contains("hiding")) return;
      el.classList.add("hiding");
      setTimeout(function () {
        if (el.parentNode) {
          el.parentNode.removeChild(el);
        }
      }, HIDE_ANIM_MS);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initMessages);
  } else {
    initMessages();
  }
})();
