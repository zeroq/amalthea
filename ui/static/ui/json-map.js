// json-map.js - "raw-data click-to-map" enhancer.
// CSP-safe: external script only; no inline handlers, no innerHTML, no eval.
// Clicking a JSON scalar opens the map dialog; the GET form only prefills a
// mapping textarea (saving remains the existing POST).
document.addEventListener("DOMContentLoaded", function () {
  var modal = document.getElementById("json-map-modal");
  if (!modal) return;

  var leaves = document.querySelectorAll(".json-val[data-json-path]");
  var targetRadios = modal.querySelectorAll('input[name="map_target"]');
  var pathRadios = modal.querySelectorAll('input[name="map_path"]');
  var observableSelect = modal.querySelector('select[name="map_observable_type"]');
  var observableRadio = modal.querySelector('input[name="map_target"][value="observable"]');
  var preview = document.getElementById("path-preview");
  var concreteRadio = pathRadios[0];
  var allItemsRadio = pathRadios[1];
  var allItemsLabel = allItemsRadio ? allItemsRadio.closest("label") : null;
  var observableLabel = observableRadio ? observableRadio.closest("label") : null;

  // No configured observable types -> "extract" could only produce an ignored request.
  if (observableSelect && observableSelect.options.length === 0) {
    if (observableRadio) {
      observableRadio.disabled = true;
    }
    if (observableLabel) {
      observableLabel.hidden = true;
    }
  }

  function selectedPath() {
    if (allItemsRadio && allItemsRadio.checked) {
      return allItemsRadio.value;
    }
    return concreteRadio ? concreteRadio.value : "";
  }

  function updatePreview() {
    if (preview) {
      preview.textContent = selectedPath();
    }
  }

  function setAllItems(available, value) {
    if (!allItemsRadio) return;
    allItemsRadio.value = available ? value : "";
    allItemsRadio.disabled = !available;
    if (allItemsLabel) {
      allItemsLabel.hidden = !available;
    }
    if (!available) {
      concreteRadio.checked = true;
    }
  }

  function resetTargets() {
    if (targetRadios.length > 0) {
      targetRadios[0].checked = true;
    }
    if (observableSelect) {
      observableSelect.selectedIndex = 0;
      observableSelect.disabled = true;
    }
  }

  function openFor(path, allPath) {
    if (!concreteRadio) return;
    concreteRadio.value = path || "";
    setAllItems(Boolean(allPath), allPath || "");
    concreteRadio.checked = true;
    resetTargets();
    updatePreview();
    modal.showModal();
    if (targetRadios.length > 0) {
      targetRadios[0].focus();
    }
  }

  function activate(leaf) {
    openFor(leaf.getAttribute("data-json-path"), leaf.getAttribute("data-json-path-all"));
  }

  for (var i = 0; i < leaves.length; i += 1) {
    var leaf = leaves[i];
    var label = "Map value at " + leaf.getAttribute("data-json-path");
    leaf.setAttribute("role", "button");
    leaf.setAttribute("tabindex", "0");
    leaf.setAttribute("aria-label", label);
    leaf.setAttribute("title", label);
    leaf.classList.add("json-val-map");
  }

  document.body.addEventListener("click", function (event) {
    var target = event.target.closest(".json-val[data-json-path]");
    if (target) {
      activate(target);
    }
  });

  document.body.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    var target = event.target.closest(".json-val[data-json-path]");
    if (!target) {
      return;
    }
    event.preventDefault();
    activate(target);
  });

  if (observableRadio && observableSelect) {
    observableRadio.addEventListener("change", function () {
      var on = observableRadio.checked;
      observableSelect.disabled = !on;
      if (on) {
        observableSelect.focus();
      }
    });
  }

  for (var j = 0; j < targetRadios.length; j += 1) {
    targetRadios[j].addEventListener("change", function () {
      if (observableSelect && (!observableRadio || !observableRadio.checked)) {
        observableSelect.disabled = true;
      }
    });
  }

  for (var k = 0; k < pathRadios.length; k += 1) {
    pathRadios[k].addEventListener("change", updatePreview);
  }

  // Coming back from the raw tab: reveal and focus the textarea the view prefilled.
  var prefilled = document.querySelectorAll("textarea[data-prefilled='1']");
  for (var m = 0; m < prefilled.length; m += 1) {
    prefilled[m].scrollIntoView({ block: "center" });
    prefilled[m].focus();
    var end = prefilled[m].value.length;
    prefilled[m].setSelectionRange(end, end);
  }
});
