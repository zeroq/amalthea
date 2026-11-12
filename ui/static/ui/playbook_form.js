/* Progressive enhancement for the playbook create/edit form.

 * Loaded as an external file so the form works under the shipped CSP
 * (`script-src 'self'`, no inline script, no nonce) — ADR-003. Server-side
 * validation is the source of truth; this file shows/hides the action-specific field
 * groups, clones header rows, and refreshes the script picker from the JSON endpoint
 * when Action type is PYTHON (and on load for an existing python playbook).
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

  var scriptDescriptions = {};
  var scriptRequestSeq = 0;

  function setScriptDescription(name) {
    var desc = document.getElementById("script-description");
    if (!desc) {
      return;
    }
    var text = scriptDescriptions[name] || "";
    desc.textContent = text;
    desc.classList.toggle("hidden", text === "");
  }

  function renderScriptOptions(scripts) {
    var select = document.getElementById("id_action_path");
    var empty = document.getElementById("script-empty");
    if (!select || select.tagName !== "SELECT") {
      return;
    }
    var previous = select.value;
    scriptDescriptions = {};
    while (select.options.length > 0) {
      select.remove(0);
    }
    var placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "— select —";
    select.appendChild(placeholder);
    scripts.forEach(function (script) {
      scriptDescriptions[script.name] = script.description || "";
      var option = document.createElement("option");
      option.value = script.name;
      option.textContent = script.name;
      if (script.description) {
        option.title = script.description;
      }
      select.appendChild(option);
    });
    if (previous && Object.prototype.hasOwnProperty.call(scriptDescriptions, previous)) {
      select.value = previous;
    }
    var isEmpty = scripts.length === 0;
    select.classList.toggle("hidden", isEmpty);
    if (empty) {
      empty.classList.toggle("hidden", !isEmpty);
    }
    setScriptDescription(select.value);
  }

  function loadScripts() {
    var container = document.getElementById("python-fields");
    if (!container || !window.fetch) {
      return;
    }
    var url = container.getAttribute("data-scripts-url");
    if (!url) {
      return;
    }
    var seq = ++scriptRequestSeq;
    window
      .fetch(url, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.json();
      })
      .then(function (payload) {
        if (seq !== scriptRequestSeq) {
          return;
        }
        var error = document.getElementById("script-load-error");
        if (error) {
          error.textContent = "";
          error.classList.add("hidden");
        }
        renderScriptOptions((payload && payload.scripts) || []);
      })
      .catch(function () {
        if (seq !== scriptRequestSeq) {
          return;
        }
        var error = document.getElementById("script-load-error");
        if (error) {
          error.textContent = "Could not refresh the script list; showing the last known list.";
          error.classList.remove("hidden");
        }
      });
  }

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
      if (action === "python") {
        // Refresh on select and on load (edit form already set to python), so a
        // newly deployed script appears without a page reload or a process restart.
        loadScripts();
      }
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
    var scriptSelect = document.getElementById("id_action_path");
    if (scriptSelect) {
      scriptSelect.addEventListener("change", function () {
        setScriptDescription(scriptSelect.value);
      });
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
