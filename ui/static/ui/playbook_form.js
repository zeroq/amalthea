/* Progressive enhancement for the playbook create/edit form.

 * Loaded as an external file so the form works under the shipped CSP
 * (`script-src 'self'`, no inline script, no nonce) — ADR-003. Server-side
 * validation in `ui/views.py::playbook_form` is the source of truth; this file
 * only shows/hides the action-specific field groups and clones header rows.
 *
 * No `innerHTML`: new rows are cloned from the `<template id="header-row-template">`
 * in the page, so no markup string is ever built in JS, and the row's field names
 * are the template's own `{i}` placeholders with the index substituted.
 *
 * Graceful degradation: without JS the action group is hidden (server decides the
 * action from the submitted `action` value) and the four server-rendered header rows
 * are still submitted, so an HTTP playbook can be saved with zero JavaScript.
 */
(function () {
  "use strict";

  function toggleActionFields() {
    var actionField = document.getElementById("id_action");
    var http = document.getElementById("http-fields");
    var python = document.getElementById("python-fields");
    if (!actionField) {
      return;
    }
    var action = actionField.value;
    if (http) {
      http.classList.toggle("hidden", action !== "http");
    }
    if (python) {
      python.classList.toggle("hidden", action !== "python");
    }
  }

  function addHeaderRow() {
    var container = document.getElementById("headers");
    var template = document.getElementById("header-row-template");
    var counter = document.getElementById("header_count");
    if (!container || !template || !counter) {
      return;
    }
    // `header_count` holds the number of server-rendered rows (4); the next row index is
    // therefore its current value, and we leave it as the running count afterwards.
    var index = parseInt(counter.value, 10);
    counter.value = String(index + 1);
    var row = template.content.firstElementChild.cloneNode(true);
    var fields = row.querySelectorAll("[name]");
    for (var i = 0; i < fields.length; i++) {
      fields[i].name = fields[i].name.replace("{i}", String(index));
    }
    container.appendChild(row);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var actionField = document.getElementById("id_action");
    if (actionField) {
      actionField.addEventListener("change", toggleActionFields);
      toggleActionFields();
    }
    var addButton = document.getElementById("add-header");
    if (addButton) {
      addButton.addEventListener("click", addHeaderRow);
    }
  });

  document.addEventListener("click", function (event) {
    var remove = event.target.closest("[data-remove-header], .remove-header");
    if (remove) {
      var row = remove.closest(".field-row");
      if (row) {
        row.remove();
      }
    }
  });
})();
