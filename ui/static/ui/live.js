/* Live case timeline over WebSockets.
 *
 * One page element owns the connection: `#audit-timeline`, the case detail Audit tab's ledger, which
 * also carries `data-ws-url`. No
 * inline handlers and no global state — the script defers until the DOM exists, then either
 * connects or does nothing at all, so a page without a timeline is unaffected by its presence.
 *
 * Three behaviours matter and each one is a bug the acceptance criteria name:
 *
 * - On open it sends `{"type": "sync", "after": <newest rendered id>}`, so a tab that was open
 *   before a comment, or one that just reconnected after dropping, catches up from the server's
 *   keyset instead of guessing (AC7.4).
 * - Every entry is rendered at most once. The server's sync already excludes the anchor, but the
 *   live `event` message races with it: an event published between the sync request and the sync
 *   reply can arrive through both paths. `data-event-id` is the dedupe key, which is why the
 *   server partial carries it too.
 * - Reconnect is capped (1s, 2s, 4s … 15s) and re-syncs on every successful open. A refused
 *   handshake (4401: the session is gone) and a protocol rejection (4000) do *not* retry, because
 *   repeating them is a busy-loop against a server that has already answered.
 *
 * Entries are built with DOM nodes and `textContent`, never HTML concatenation: the payload is
 * attacker-adjacent (it carries alert titles and note bodies), and an innerHTML here would be an
 * XSS sink in the one place that renders untrusted text as rich content.
 *
 * Since T2 P2 a frame's `event_type` decides its destination: `page` refreshes the case's Pages card,
 * `task` refreshes the Tasks tab list, every other value is a ledger `kind` and appends to the timeline.
 * Without that split a page (which carries `content`, not `description`) would land in the timeline
 * as a bare title.
 *
 * The Pages card and Tasks list are exceptions to the "build it here" rule: their bodies are
 * analyst-authored Markdown rendered by the server (`|markdown`, sanitized per ADR-005), which this
 * bundle cannot reproduce. A `page`/`task` frame therefore re-fetches the card — see
 * `refreshPages()` and `refreshTasks()`.
 */
(function () {
  "use strict";

  var list = document.getElementById("audit-timeline");
  if (!list) return;
  var url = list.getAttribute("data-ws-url");
  if (!url || typeof WebSocket === "undefined") return;

  // Optional: the case page's Pages card. Pages are not ledger entries, so a `page` event must not
  // be fed to the timeline renderer — it refreshes this list instead, and is a no-op when absent.
  var pagesRefresh = null;

  // Optional: the case page's Tasks tab list. A `task` frame refreshes this list.
  var tasksRefresh = null;

  var socket = null;
  var backoff = 1000;
  var BACKOFF_MAX = 15000;
  var RETRY_STOP_CODES = [4000, 4401];

  function pad(value) {
    return (value < 10 ? "0" : "") + value;
  }

  /* `Y-m-d H:i:s`, the format the server template renders, in the viewer's own timezone. */
  function stamp(iso) {
    if (!iso) return "";
    var date = new Date(iso);
    if (isNaN(date.getTime())) return iso;
    return (
      date.getFullYear() +
      "-" + pad(date.getMonth() + 1) +
      "-" + pad(date.getDate()) +
      " " + pad(date.getHours()) +
      ":" + pad(date.getMinutes()) +
      ":" + pad(date.getSeconds())
    );
  }

  /* `kind` → CSS class, the same rule as `{{ event.kind|slugify }}`. The kinds in use are already
   * kebab-case, so this is lowercasing with a guard for whatever a future kind spells badly. */
  function slug(kind) {
    return String(kind || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
  }

  function rendered(id) {
    return !!list.querySelector('[data-event-id="' + id + '"]');
  }

  /* The newest id already on the page: entries only ever append, in server order, so the last
   * node is the keyset anchor. Null on an empty timeline means "send me everything". */
  function anchor() {
    var entries = list.querySelectorAll("[data-event-id]");
    if (!entries.length) return null;
    return entries[entries.length - 1].getAttribute("data-event-id");
  }

  function node(tag, className, text) {
    var element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    return element;
  }

  /* Mirrors `ui/templates/ui/_timeline_entry.html`: the server renders that for a full page load
   * and for the HTMX append, this renders the same tree from the JSON the WebSocket delivers. */
  function entry(event) {
    var item = node("li", "tl tl-" + slug(event.kind));
    item.setAttribute("data-event-id", event.id);
    item.appendChild(node("p", "tl-title", event.title || ""));

    if (event.description) {
      item.appendChild(node("pre", "tl-body", event.description));
    }

    var meta = node("p", "tl-meta");
    meta.appendChild(node("span", "mono", stamp(event.date)));
    if (event.actor) meta.appendChild(document.createTextNode(" · " + event.actor));
    meta.appendChild(document.createTextNode(" · "));
    meta.appendChild(node("span", "dim", event.kind || ""));

    if (event.kind === "automation-run" && event.metadata && event.metadata.status) {
      meta.appendChild(document.createTextNode(" · "));
      meta.appendChild(
        node("span", "pill pill-" + String(event.metadata.status).toLowerCase(), event.metadata.status)
      );
    }

    item.appendChild(meta);
    return item;
  }

  function append(event) {
    if (!event || !event.id || rendered(event.id)) return;
    var placeholder = list.querySelector("li.empty");
    if (placeholder) placeholder.parentNode.removeChild(placeholder);
    list.appendChild(entry(event));
  }

  /* A `page` frame carries the raw `page_json`, whose `content` is Markdown, and this bundle ships no
   * Markdown renderer. Re-fetching the card keeps the live list identical to the reloaded one — the
   * same `_page_entry.html`, the same sanitiser — instead of hand-mirroring the entry markup here,
   * which is exactly the drift the old `pageEntry()` invited. Note the entry and its modals are
   * replaced wholesale, so the buttons always match the markup the server rendered.
   *
   * Requests collapse: while one is in flight a second frame is dropped (the reply is a snapshot of
   * the whole list, so the last one wins by definition). Everything else is dropped when the card is
   * not on screen, and a failed fetch is silent — the analyst's next load renders the same list. */
  function refreshPages() {
    if (pagesRefresh || !document.getElementById("case-pages")) return;
    pagesRefresh = fetch(window.location.pathname + window.location.search, {
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" },
    })
      .then(function (response) {
        return response.ok ? response.text() : "";
      })
      .then(function (html) {
        // Parsed detached, so nothing in it can run or load until the swap below replaces the old
        // list outright. The markup is the server's own output for this same URL, not a payload.
        var fresh = new DOMParser().parseFromString(html, "text/html").getElementById("case-pages");
        var current = document.getElementById("case-pages");
        if (fresh && current) current.replaceWith(fresh);
      })
      .catch(function () {
        /* Offline or a refused session: leave the list as it is. */
      })
      .then(function () {
        pagesRefresh = null;
      });
  }

  /* A `task` frame carries the raw `task_json`, and this bundle ships no task renderer.
   * Re-fetching the Tasks tab list keeps the live list identical to the reloaded one — the
   * same `_tab_tasks.html`, the same markup — instead of hand-mirroring the entry markup here.
   *
   * Requests collapse: while one is in flight a second frame is dropped (the reply is a snapshot of
   * the whole list, so the last one wins by definition). Everything else is dropped when the list is
   * not on screen, and a failed fetch is silent — the analyst's next load renders the same list. */
  function refreshTasks() {
    if (tasksRefresh || !document.getElementById("case-tasks")) return;
    tasksRefresh = fetch(window.location.pathname + window.location.search, {
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" },
    })
      .then(function (response) {
        return response.ok ? response.text() : "";
      })
      .then(function (html) {
        // Parsed detached, so nothing in it can run or load until the swap below replaces the old
        // list outright. The markup is the server's own output for this same URL, not a payload.
        var fresh = new DOMParser().parseFromString(html, "text/html").getElementById("case-tasks");
        var current = document.getElementById("case-tasks");
        if (fresh && current) current.replaceWith(fresh);
      })
      .catch(function () {
        /* Offline or a refused session: leave the list as it is. */
      })
      .then(function () {
        tasksRefresh = null;
      });
  }

  function handle(message) {
    if (!message || typeof message !== "object") return;
    if (message.type === "timeline" && Array.isArray(message.events)) {
      message.events.forEach(append);
      return;
    }
    if (message.type === "event" && message.payload) {
      // `event_type` carries the ledger `kind` for timeline entries, or `page`/`task` for
      // cards that re-fetch their server-rendered markup. Routing on it keeps those frames
      // out of the timeline, where they would render incorrectly.
      if (message.event_type === "page") {
        refreshPages();
      } else if (message.event_type === "task") {
        refreshTasks();
      } else {
        append(message.payload.event);
      }
    }
  }

  function connect() {
    socket = new WebSocket(url);

    socket.addEventListener("open", function () {
      backoff = 1000;
      socket.send(JSON.stringify({ type: "sync", after: anchor() }));
    });

    socket.addEventListener("message", function (event) {
      var message;
      try {
        message = JSON.parse(event.data);
      } catch (error) {
        return; // A frame we cannot parse is a frame we must not render.
      }
      handle(message);
    });

    socket.addEventListener("close", function (event) {
      if (RETRY_STOP_CODES.indexOf(event.code) !== -1) return;
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, BACKOFF_MAX);
    });
  }

  connect();
})();
