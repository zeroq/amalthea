/* Keyboard shortcuts for the analyst UI.
 *
 * Scope is deliberately narrow: single unmodified letters are only bound when focus is in the body,
 * never while a text field, textarea, select or contenteditable has focus. An IR analyst types alert
 * text and search terms constantly, and a `c` that navigates away mid-typing loses their work — so
 * "typeable contexts win" is a hard rule here, not a nicety.
 *
 * Every shortcut is also reachable by mouse and by tab order, so this file is a speed layer over a
 * complete interface rather than the only way to do anything (WCAG 2.1.1).
 */
(function () {
  "use strict";

  var ROUTES = {
    g: "/",
    a: "/alerts",
    c: "/cases",
    r: "/automation",
    s: "/sources",
  };

  function isTyping(target) {
    if (!target) return false;
    if (target.isContentEditable) return true;
    var tag = target.tagName;
    if (tag === "TEXTAREA" || tag === "SELECT") return true;
    if (tag === "INPUT") {
      var type = (target.getAttribute("type") || "text").toLowerCase();
      // Checkboxes and buttons are clickable targets, not text entry; leave them alone.
      return ["text", "password", "email", "search", "url", "number", "date", "hidden"].indexOf(type) !== -1;
    }
    return false;
  }

  function isModified(event) {
    return event.metaKey || event.ctrlKey || event.altKey;
  }

  document.addEventListener("keydown", function (event) {
    if (isModified(event) || isTyping(event.target)) return;

    var route = ROUTES[event.key];
    if (route) {
      event.preventDefault();
      window.location.assign(route);
      return;
    }

    // `/` focuses the first text field on the page, the way most search-first tools behave.
    if (event.key === "/") {
      var field = document.querySelector("main input:not([type=hidden]), main textarea");
      if (field) {
        event.preventDefault();
        field.focus();
      }
    }
  });
})();