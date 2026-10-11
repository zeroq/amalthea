/* Keyboard shortcuts for the analyst UI.
 *
 * Scope is deliberately narrow: single unmodified letters are only bound when focus is in the body,
 * never while a text field, textarea, select or contenteditable has focus. An IR analyst types alert
 * text and search terms constantly, and a `c` that navigates away mid-typing loses their work — so
 * "typeable contexts win" is a hard rule here, not a nicety.
 *
 * Every shortcut is also reachable by mouse and by tab order, so this file is a speed layer over a
 * complete interface rather than the only way to do anything (WCAG 2.1.1).
 *
 * The cheat-sheet modal is now server-rendered in base.html (D6). This file only handles opening
 * it via `?` and the navigation shortcuts. The CHEATSHEET array is the single source of truth for
 * both the JS navigation and the server-rendered table — a conformance test enforces this.
 */
(function () {
  "use strict";

  var CHEATSHEET = [
    { key: "g", label: "Go to Dashboard" },
    { key: "a", label: "Alerts list" },
    { key: "c", label: "Cases list" },
    { key: "o", label: "Observables list" },
    { key: "r", label: "Automation runs" },
    { key: "p", label: "Playbooks list" },
    { key: "s", label: "Sources" },
    { key: "t", label: "Tags list" },
    { key: "m", label: "Case templates" },
    { key: "x", label: "Taxonomy" },
    { key: ",", label: "Settings (Organisations)" },
    { key: "/", label: "Focus search / first input" },
    { key: "?", label: "Show this cheat sheet" },
  ];

  var ROUTES = {
    g: "/",
    a: "/alerts",
    c: "/cases",
    o: "/observables",
    r: "/automation",
    p: "/automation/playbooks",
    s: "/sources",
    t: "/tags",
    m: "/case-templates",
    x: "/taxonomy",
    ",": "/settings/organisations",
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

  function openCheatsheet() {
    var modal = document.getElementById("cheatsheet-modal");
    if (modal) {
      modal.showModal();
    }
  }

  document.addEventListener("keydown", function (event) {
    if (isModified(event) || isTyping(event.target)) return;

    var route = ROUTES[event.key];
    if (route) {
      event.preventDefault();
      window.location.assign(route);
      return;
    }

    // `?` (Shift+/) opens the cheat sheet.
    if (event.key === "?" || (event.key === "/" && event.shiftKey)) {
      event.preventDefault();
      openCheatsheet();
      return;
    }

    // `/` focuses the first text field on the page, the way most search-first tools behave.
    if (event.key === "/" && !event.shiftKey) {
      var field = document.querySelector("main input:not([type=hidden]), main textarea");
      if (field) {
        event.preventDefault();
        field.focus();
      }
    }
  });
})();