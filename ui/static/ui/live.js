/* Live case timeline over WebSockets.
 *
 * One page element owns the connection: `#timeline-events`, which also carries `data-ws-url`. No
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
 * Since T2 P2 a frame's `event_type` decides its destination: `page` appends to the case's Pages
 * card, every other value is a ledger `kind` and appends to the timeline. Without that split a page
 * (which carries `content`, not `description`) would land in the timeline as a bare title.
 */
(function () {
  "use strict";

  var list = document.getElementById("timeline-events");
  if (!list) return;
  var url = list.getAttribute("data-ws-url");
  if (!url || typeof WebSocket === "undefined") return;

  // Optional: the case page's Pages card. Pages are not ledger entries, so a `page` event must not
  // be fed to the timeline renderer — it targets this list instead, and is dropped when absent.
  var pages = document.getElementById("case-pages");

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

  /* Mirrors `ui/templates/ui/_page_entry.html`. The wire shape is `page_json`, so the audit fields
   * are the `_createdAt`/`_createdBy` spelling, not the ledger event's `date`/`actor`. */
  function pageEntry(page) {
    var item = node("li", "page");
    item.setAttribute("data-page-id", page.id || page._id);
    item.appendChild(node("p", "page-title", page.title || ""));
    if (page.content) item.appendChild(node("pre", "tl-body", page.content));

    var meta = node("p", "tl-meta");
    if (page.category) {
      meta.appendChild(node("span", "dim", page.category));
      meta.appendChild(document.createTextNode(" · "));
    }
    meta.appendChild(node("span", "mono", stamp(page._createdAt)));
    if (page._createdBy) meta.appendChild(document.createTextNode(" · " + page._createdBy));
    item.appendChild(meta);
    return item;
  }

  function appendPage(page) {
    if (!pages || !page) return;
    var id = page.id || page._id;
    if (!id || pages.querySelector('[data-page-id="' + id + '"]')) return;
    var placeholder = pages.querySelector("li.empty");
    if (placeholder) placeholder.parentNode.removeChild(placeholder);
    pages.appendChild(pageEntry(page));
  }

  function handle(message) {
    if (!message || typeof message !== "object") return;
    if (message.type === "timeline" && Array.isArray(message.events)) {
      message.events.forEach(append);
      return;
    }
    if (message.type === "event" && message.payload) {
      // `event_type` carries the ledger `kind` for timeline entries, or `page` for a page. Routing
      // on it keeps a page out of the timeline, where it would render as a title with no body.
      if (message.event_type === "page") {
        appendPage(message.payload.event);
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
