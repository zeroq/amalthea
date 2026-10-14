/* Tab switching for the case detail page.
 *
 * Progressive enhancement: tabs are real <a href="?tab=..."> links, so with JS disabled
 * the browser navigates and the server renders the correct panel active.
 * With JS enabled: click toggles `hidden`, updates `aria-selected` + roving `tabindex`,
 * and pushes `history.pushState` for deep-linking.
 *
 * Keyboard: ArrowLeft/ArrowRight move between tabs (wrapping), Home/End jump to first/last.
 * Number keys 1–9 switch tabs when focus is NOT in a form control.
 */
(function () {
  "use strict";

  var tablist = document.querySelector('[role="tablist"]');
  if (!tablist) return;

  var tabs = Array.prototype.slice.call(tablist.querySelectorAll('[role="tab"]'));
  var panels = {};

  // Map tab slug -> panel element
  tabs.forEach(function (tab) {
    var controls = tab.getAttribute("aria-controls");
    if (controls) {
      panels[controls] = document.getElementById(controls);
    }
  });

  function isTyping(target) {
    if (!target) return false;
    if (target.isContentEditable) return true;
    var tag = target.tagName;
    if (tag === "TEXTAREA" || tag === "SELECT") return true;
    if (tag === "INPUT") {
      var type = (target.getAttribute("type") || "text").toLowerCase();
      return ["text", "password", "email", "search", "url", "number", "date", "hidden"].indexOf(type) !== -1;
    }
    return false;
  }

  // `push` is false for the initial sync and for popstate: those calls must not add a
  // history entry, otherwise Back would need two presses to leave the page.
  function activateTab(tab, push) {
    if (!tab) return;
    var slug = tab.getAttribute("href")?.replace("?tab=", "") || "";
    var panelId = tab.getAttribute("aria-controls");

    // Update all tabs
    tabs.forEach(function (t) {
      var isActive = t === tab;
      t.setAttribute("aria-selected", isActive ? "true" : "false");
      t.setAttribute("tabindex", isActive ? "0" : "-1");
      t.classList.toggle("tab-on", isActive);
    });

    // Show/hide panels
    Object.keys(panels).forEach(function (id) {
      var p = panels[id];
      if (p) {
        p.hidden = (id !== panelId);
      }
    });

    // Push state. Focus deliberately stays on the tab (WAI-ARIA APG); we never move it
    // into the panel, and never steal focus on the initial page load.
    if (push !== false && slug && window.history && window.history.pushState) {
      var url = new URL(window.location.href);
      url.searchParams.set("tab", slug);
      window.history.pushState({ tab: slug }, "", url.toString());
    }
  }

  function getActiveIndex() {
    var active = tablist.querySelector('[aria-selected="true"]');
    return tabs.indexOf(active);
  }

  function focusTab(index) {
    if (index < 0) index = tabs.length - 1;
    if (index >= tabs.length) index = 0;
    tabs[index]?.focus();
  }

  // Click handler
  tablist.addEventListener("click", function (event) {
    var tab = event.target.closest('[role="tab"]');
    if (!tab || tab.parentElement !== tablist) return;
    event.preventDefault();
    activateTab(tab);
  });

  // Keyboard handler on tablist
  tablist.addEventListener("keydown", function (event) {
    var activeIndex = getActiveIndex();
    if (activeIndex === -1) return;

    switch (event.key) {
      case "ArrowLeft":
        event.preventDefault();
        focusTab(activeIndex - 1);
        break;
      case "ArrowRight":
        event.preventDefault();
        focusTab(activeIndex + 1);
        break;
      case "Home":
        event.preventDefault();
        focusTab(0);
        break;
      case "End":
        event.preventDefault();
        focusTab(tabs.length - 1);
        break;
      default:
        // Number keys 1-9 (only when not typing)
        if (event.key >= "1" && event.key <= "9" && !isTyping(event.target)) {
          var num = parseInt(event.key, 10) - 1;
          if (num >= 0 && num < tabs.length) {
            event.preventDefault();
            var tab = tabs[num];
            tab.focus();
            activateTab(tab);
          }
        }
        return;
    }

    // Activation follows focus for arrow/Home/End
    var newTab = tabs[getActiveIndex()];
    if (newTab) {
      activateTab(newTab);
    }
  });

  // Sync on popstate (back/forward navigation)
  window.addEventListener("popstate", function () {
    var url = new URL(window.location.href);
    var slug = url.searchParams.get("tab");
    if (!slug) return;
    var tab = tablist.querySelector('[href="?tab=' + slug + '"]');
    if (tab) {
      activateTab(tab, false);
    }
  });

  // Initial sync: ensure the active tab from server render matches URL
  // (In case of a reload with ?tab=, the server already set aria-selected, but
  // we also need to make sure panels' `hidden` attributes match.)
  var initialActive = tablist.querySelector('[aria-selected="true"]');
  if (initialActive) {
    activateTab(initialActive, false);
  }
})();