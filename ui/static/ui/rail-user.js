/* Rail user menu — progressive enhancement for the <details> disclosure.
 *
 * Loaded as an external file so the interaction works under the shipped CSP
 * (`script-src 'self'`, no inline/nonce — ADR-003). This file contains no data
 * and no innerHTML sinks.
 *
 * Behaviour:
 * 1. Outside click closes the open menu.
 * 2. Escape closes the menu and returns focus to the <summary>.
 * 3. Activating a menu item closes the menu after a setTimeout(..., 0) so the
 *    default action (navigation, form submit, or data-modal-open) still runs.
 *
 * Native <details>/<summary> handles the primary open/close toggle and expanded
 * state; this script is progressive enhancement only.
 */
(function () {
  "use strict";

  function init() {
    var triggers = document.querySelectorAll("details.rail-user");
    for (var i = 0; i < triggers.length; i++) {
      setupDisclosure(triggers[i]);
    }
  }

  function setupDisclosure(details) {
    var summary = details.querySelector(".rail-user-trigger");
    if (!summary) {
      return;
    }

    // Outside click closes the open menu
    document.addEventListener("click", function (event) {
      if (!details.open) {
        return;
      }
      if (details.contains(event.target)) {
        return;
      }
      details.open = false;
    });

    // Escape closes the menu and returns focus to the summary
    document.addEventListener("keydown", function (event) {
      if (!details.open) {
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        details.open = false;
        summary.focus();
      }
    });

    // Activating a menu item closes the menu after a microtask so the
    // default action (navigation, form submit, or data-modal-open) still runs.
    var menu = details.querySelector(".rail-user-menu");
    if (menu) {
      menu.addEventListener("click", function (event) {
        var item = event.target.closest(".rail-user-menu-item");
        if (item && details.open) {
          setTimeout(function () {
            details.open = false;
          }, 0);
        }
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();