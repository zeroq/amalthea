/* Person picker type-ahead — progressive enhancement for <select name="assignee">.
 *
 * Adds a text <input> before the select that filters the options by case-insensitive
 * substring match on the option label. The select remains fully functional without JS.
 * The current selection is always preserved and submittable.
 *
 * Filtering rebuilds the option list instead of toggling <option hidden>: WebKit does
 * not reliably honour hidden/display:none on <option>, so hiding alone appeared to do
 * nothing in Safari. Rebuilding works in every supported browser.
 *
 * Loaded via <script src="{% static 'ui/picker.js' %}" defer> in base.html.
 * CSP-safe: no inline script, no eval, no dynamic code, no innerHTML.
 */
(function () {
  "use strict";

  var SELECTOR = 'select[name="assignee"]';

  function clearOptions(select) {
    while (select.firstChild) {
      select.removeChild(select.firstChild);
    }
  }

  function initPicker(select) {
    if (select.dataset.pickerInitialized) return;
    select.dataset.pickerInitialized = "true";

    var wrapper = document.createElement("div");
    wrapper.className = "picker-wrapper";
    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(select);

    var input = document.createElement("input");
    input.type = "text";
    input.className = "picker-input";
    input.placeholder = "Filter assignees…";
    input.setAttribute("aria-label", "Filter assignees");
    input.setAttribute("autocomplete", "off");
    wrapper.insertBefore(input, select);

    var status = document.createElement("p");
    status.className = "picker-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.hidden = true;
    wrapper.insertBefore(status, select);

    // Snapshot the server-rendered options. Re-appending the original elements (rather
    // than clones) preserves every attribute and the selected state.
    var original = Array.prototype.slice.call(select.options);

    function labelOf(option) {
      return (option.textContent || "").trim();
    }

    function render(query) {
      var q = query.toLowerCase().trim();
      var selectedValue = select.value;
      var selectedOption = null;
      var unassigned = null;
      var matches = [];

      original.forEach(function (option) {
        if (option.value === "") {
          unassigned = option;
          return;
        }
        if (option.value === selectedValue) {
          selectedOption = option;
        }
        if (q === "" || labelOf(option).toLowerCase().indexOf(q) !== -1) {
          matches.push(option);
        }
      });

      // Never let filtering silently change the submitted assignee: keep the current
      // selection visible even when it does not match the query.
      var pinned = selectedOption && matches.indexOf(selectedOption) === -1;
      if (pinned) {
        matches.unshift(selectedOption);
      }
      var actualMatches = matches.length - (pinned ? 1 : 0);

      clearOptions(select);
      if (unassigned) {
        select.appendChild(unassigned);
      }
      matches.forEach(function (option) {
        select.appendChild(option);
      });
      select.value = selectedValue;

      if (!q) {
        status.hidden = true;
        status.textContent = "";
      } else if (actualMatches === 0) {
        status.textContent = pinned
          ? "No matching assignees (current selection kept)."
          : "No matching assignees.";
        status.hidden = false;
      } else {
        status.textContent =
          actualMatches +
          " matching assignee" +
          (actualMatches === 1 ? "" : "s") +
          ".";
        status.hidden = false;
      }
    }

    input.addEventListener("input", function () {
      render(input.value);
    });

    select.addEventListener("change", function () {
      // A new selection invalidates the filter; clear it so the full list is available.
      if (input.value !== "") {
        input.value = "";
        render("");
      }
    });

    input.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        input.value = "";
        render("");
        input.blur();
      }
    });
  }

  function initWithin(root) {
    if (!root || !root.querySelectorAll) return;
    if (root.matches && root.matches(SELECTOR)) {
      initPicker(root);
    }
    var selects = root.querySelectorAll(SELECTOR);
    for (var i = 0; i < selects.length; i++) {
      initPicker(selects[i]);
    }
  }

  function initAll() {
    initWithin(document);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  // Also handle dynamically added modals (HTMX swaps).
  document.addEventListener("htmx:afterSwap", function (event) {
    initWithin(event.detail.target);
  });
})();
