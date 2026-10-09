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
    p: "/automation/playbooks",
    s: "/sources",
  };

  // Cheat sheet data (key -> label)
  var CHEATSHEET = [
    { key: "g", label: "Go to Dashboard" },
    { key: "a", label: "Alerts list" },
    { key: "c", label: "Cases list" },
    { key: "p", label: "Playbooks list" },
    { key: "r", label: "Automation runs" },
    { key: "s", label: "Sources" },
    { key: "/", label: "Focus search / first input" },
    { key: "?", label: "Show this cheat sheet" },
  ];

  function renderCheatsheet() {
    var modal = document.getElementById("cheatsheet-modal");
    if (modal) return;
    modal = document.createElement("dialog");
    modal.id = "cheatsheet-modal";
    modal.className = "cheatsheet-modal";
    var html = '<div class="cheatsheet-header"><h2>Keyboard shortcuts</h2><button class="cheatsheet-close" aria-label="Close">&times;</button></div>';
    html += '<table class="cheatsheet-table"><thead><tr><th>Key</th><th>Action</th></tr></thead><tbody>';
    CHEATSHEET.forEach(function(item) {
      html += '<tr><td class="cheatsheet-key"><kbd>' + item.key + '</kbd></td><td>' + item.label + '</td></tr>';
    });
    html += '</tbody></table>';
    modal.innerHTML = html;
    document.body.appendChild(modal);

    modal.querySelector(".cheatsheet-close").addEventListener("click", function() {
      modal.close();
    });
    modal.addEventListener("click", function(e) {
      if (e.target === modal) modal.close();
    });
    modal.showModal();
  }

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
      console.log("[keys.js] Navigating via shortcut:", event.key, "->", route);
      event.preventDefault();
      window.location.assign(route);
      return;
    }

    // `?` (Shift+/) opens the cheat sheet.
    if (event.key === "?" || (event.key === "/" && event.shiftKey)) {
      console.log("[keys.js] Opening cheatsheet via:", event.key);
      event.preventDefault();
      renderCheatsheet();
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