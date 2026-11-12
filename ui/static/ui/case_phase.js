/* Containment phase dialog: show the action fieldset only for "containment",
 * and show the Other detail row only when the Other checkbox is checked.
 *
 * External file only (CSP/ADR-003). Without JavaScript the fieldset and Other
 * row stay visible; server-side validation remains authoritative.
 */

document.addEventListener("DOMContentLoaded", function () {
  const dialog = document.getElementById("case-field-phase");
  if (!dialog) return;

  const phaseSelect = dialog.querySelector('select[name="value"]');
  const fieldset = dialog.querySelector("[data-containment-fieldset]");
  if (!phaseSelect || !fieldset) return;

  const otherCheckbox = dialog.querySelector(
    'input[name="containment_actions"][value="other"]'
  );
  const otherRow = dialog.querySelector("[data-containment-other-row]");

  function syncContainmentFieldset() {
    fieldset.hidden = phaseSelect.value !== "containment";
  }

  function syncOtherRow() {
    if (!otherCheckbox || !otherRow) return;
    otherRow.hidden = !otherCheckbox.checked;
  }

  syncContainmentFieldset();
  syncOtherRow();

  phaseSelect.addEventListener("change", syncContainmentFieldset);
  if (otherCheckbox) {
    otherCheckbox.addEventListener("change", syncOtherRow);
  }
});
