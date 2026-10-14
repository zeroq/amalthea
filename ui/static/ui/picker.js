/* Person picker type-ahead — progressive enhancement for <select name="assignee">.
 *
 * Adds a text <input> before the select that filters <option>s by case-insensitive
 * substring match on the option label. The select remains fully functional without JS.
 * The current selection is always preserved and submittable.
 *
 * Loaded via <script src="{% static 'ui/picker.js' %}" defer> in base.html.
 * CSP-safe: no inline script, no eval, no dynamic code.
 */
(function () {
  "use strict";

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

    // Keep track of the original options for filtering
    var options = Array.from(select.querySelectorAll("option"));

    function filter(query) {
      var q = query.toLowerCase().trim();
      var hasVisible = false;
      options.forEach(function (opt) {
        // Always keep the empty "Unassigned" option visible
        if (opt.value === "") {
          opt.hidden = false;
          hasVisible = true;
          return;
        }
        var label = opt.textContent.toLowerCase();
        var match = q === "" || label.indexOf(q) !== -1;
        opt.hidden = !match;
        if (match) hasVisible = true;
      });
      // If nothing matches (except empty), show all to avoid confusing empty state
      if (!hasVisible) {
        options.forEach(function (opt) { opt.hidden = false; });
      }
    }

    input.addEventListener("input", function () {
      filter(input.value);
    });

    // Sync filter when selection changes programmatically (e.g., prefilled on edit)
    select.addEventListener("change", function () {
      var selected = select.options[select.selectedIndex];
      if (selected && selected.value !== "") {
        input.value = "";
        filter("");
      }
    });

    // Keyboard: Escape clears filter
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        input.value = "";
        filter("");
        input.blur();
      }
    });
  }

  function initAll() {
    var selects = document.querySelectorAll('select[name="assignee"]');
    for (var i = 0; i < selects.length; i++) {
      initPicker(selects[i]);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  // Also handle dynamically added modals (HTMX swaps)
  document.addEventListener("htmx:afterSwap", function (event) {
    var selects = event.detail.target.querySelectorAll('select[name="assignee"]');
    for (var i = 0; i < selects.length; i++) {
      initPicker(selects[i]);
    }
  });
})();