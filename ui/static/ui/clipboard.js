/* Clipboard helper for copy buttons.
 *
 * Delegated click listener on document — works for dynamically added modals.
 * Uses navigator.clipboard.writeText with a select-the-text fallback for
 * non-secure contexts (plain HTTP). No inline handlers (CSP-safe).
 */
(function () {
  "use strict";

  function fallbackCopy(text) {
    var textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "absolute";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    try {
      document.execCommand("copy");
    } catch (e) {
      // ignored
    }
    document.body.removeChild(textarea);
  }

  function copyToClipboard(text, button) {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () {
        showCopied(button);
      }).catch(function () {
        fallbackCopy(text);
        showCopied(button);
      });
    } else {
      fallbackCopy(text);
      showCopied(button);
    }
  }

  function showCopied(button) {
    var originalText = button.textContent;
    button.textContent = "Copied";
    button.disabled = true;
    setTimeout(function () {
      button.textContent = originalText;
      button.disabled = false;
    }, 2000);
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-clipboard-target]");
    if (!button) return;

    var targetSelector = button.getAttribute("data-clipboard-target");
    var target = document.querySelector(targetSelector);
    if (!target) return;

    var text = target.textContent || target.value || "";
    if (text) {
      copyToClipboard(text, button);
    }
  });
})();