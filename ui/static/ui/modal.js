/* Modal behaviour for the shared <dialog> component (ui/templates/ui/_modal.html).
 *
 * Loaded as an external file so the interaction works under the shipped CSP
 * (`script-src 'self'`, no inline/nonce — ADR-003). This file contains **no data**:
 * every label, blocker count and form field it governs is rendered server-side, so
 * there is no innerHTML/HTML-string sink here (see SECURITY-2026-10-11 F3/F10).
 *
 * Progressive enhancement only: opening/closing needs this script, but every form it
 * wraps is a plain POST that works with JS disabled (server-side validation is
 * authoritative). Nothing in the IR workflow depends on it running.
 */
(function () {
  "use strict";

  function focusable(modal) {
    return modal.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    );
  }

  function open(modal) {
    if (!modal || modal.open) {
      return;
    }
    // Server marks an un-actionable modal (e.g. deleting an org that owns tenant data)
    // with [data-modal-blocked]; disable its confirm button so the intent is obvious.
    var blocked = modal.querySelector("[data-modal-blocked]");
    var submit = modal.querySelector('button[type="submit"]');
    if (submit) {
      submit.disabled = !!blocked;
      if (blocked) {
        submit.title = blocked.getAttribute("data-modal-blocked") || "Blocked";
      }
    }
    modal.showModal();
    var autofocus = modal.querySelector("[autofocus]");
    if (autofocus) {
      autofocus.focus();
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var autoOpenModals = document.querySelectorAll("[data-autoopen]");
    for (var i = 0; i < autoOpenModals.length; i++) {
      open(autoOpenModals[i]);
    }
  });

  document.addEventListener("click", function (event) {
    var opener = event.target.closest("[data-modal-open]");
    if (opener) {
      var modalId = opener.getAttribute("data-modal-open");
      var modal = document.getElementById(modalId);
      if (modal) {
        event.preventDefault();
        open(modal);
      }
      // If modal not found, fall through and let the browser follow the href
      return;
    }
    var closer = event.target.closest("[data-modal-close]");
    if (closer) {
      var modal = closer.closest("dialog");
      if (modal) {
        // A close control may be a real <a href> (the JS-less fallback needs somewhere to go:
        // see the show-once token dialog). With JS the dialog should close in place instead of
        // navigating, so the anchor's default action is suppressed — `<button>` closers have no
        // default action, so this is a no-op for them.
        if (closer.tagName === "A") {
          event.preventDefault();
        }
        modal.close();
      }
    }
  });

  // Click-outside-to-close. A native modal <dialog> has **no** separate backdrop element in
  // the DOM: clicks on `::backdrop` are dispatched *to the <dialog>* itself, so the dialog is
  // its own outside-click target. (A `<div>` overlay cannot be used instead — positioned
  // siblings paint above the dialog's static content and swallow every click inside it.)
  // Dialogs that must survive a stray click — destructive confirmations — opt out with
  // `data-modal-static` (Flowbite's `data-modal-backdrop="static"`).
  document.addEventListener("click", function (event) {
    var modal =
      event.target.closest &&
      event.target.closest("dialog.modal, dialog.cheatsheet-modal");
    if (
      modal &&
      event.target === modal &&
      !modal.hasAttribute("data-modal-static")
    ) {
      modal.close();
    }
  });

  document.addEventListener("keydown", function (event) {
    var modal =
      event.target.closest && event.target.closest("dialog[open]");
    if (!modal) {
      return;
    }
    if (event.key === "Escape") {
      // `data-modal-static` means "only an explicit close control dismisses me". It already opts
      // out of click-outside below; Esc has to opt out too, or it is a one-key way to destroy
      // something the dialog exists to protect (the show-once token secret). No dialog used the
      // attribute before this, so no existing behaviour changes.
      if (!modal.hasAttribute("data-modal-static")) {
        modal.close();
      }
      return;
    }
    if (event.key !== "Tab") {
      return;
    }
    var items = focusable(modal);
    if (!items.length) {
      return;
    }
    var first = items[0];
    var last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  // Progressive enhancement for the org-delete member handling: hide/disable the
  // reassign target when "delete members" is chosen. Without JS the select stays
  // enabled and the server ignores it when members=delete.
  document.addEventListener("change", function (event) {
    var input = event.target;
    if (!input || input.name !== "members") {
      return;
    }
    var modal = input.closest("dialog");
    if (!modal) {
      return;
    }
    var wrapper = modal.querySelector("[data-target-org]");
    if (!wrapper) {
      return;
    }
    var select = wrapper.querySelector("select");
    var reassign = input.checked && input.value === "reassign";
    wrapper.hidden = !reassign;
    if (select) {
      select.disabled = !reassign;
    }
  });
})();
