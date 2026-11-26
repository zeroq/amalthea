/* Live case timeline, org refresh, and counts-only presence.
 *
 * Static JS only (ADR-003). Server-rendered partials own all markup; this file opens sockets,
 * renders timeline entries with textContent, and asks HTMX to swap server-rendered partials.
 */
(function () {
  "use strict";

  var PAGE_TARGETS = {
    "alert-list": [{ partial: "rows", selector: "#alert-list-rows" }],
    "case-list": [{ partial: "rows", selector: "#case-list-rows" }],
    dashboard: [
      { partial: "stats", selector: "#dashboard-stats" },
      { partial: "recent-alerts", selector: "#dashboard-recent-alerts" },
      { partial: "recent-cases", selector: "#dashboard-recent-cases" }
    ]
  };

  var LABELS = { live: "Live", reconnecting: "Reconnecting…", offline: "Offline" };

  function setChipState(chip, state) {
    chip.textContent = LABELS[state] || LABELS.offline;
    chip.setAttribute("role", "status");
    chip.setAttribute("aria-live", "polite");
    chip.setAttribute("data-state", state);
  }

  function setPresence(count) {
    var el = document.getElementById("case-presence");
    if (!el) return;
    var text = el.querySelector("[data-presence-text]");
    if (!text) return;
    var value = Number(count) || 0;
    text.textContent = value + (value === 1 ? " analyst viewing" : " analysts viewing");
  }

  function partialUrl(partial) {
    var url = new URL(window.location.href);
    url.searchParams.delete("partial");
    url.searchParams.set("partial", partial);
    return url.toString();
  }

  function targetsFor(page, surfaces) {
    var targets = PAGE_TARGETS[page] || [];
    if (!surfaces.size || surfaces.has("dashboard")) return targets;
    if (page === "alert-list" && surfaces.has("alerts")) return targets;
    if (page === "case-list" && surfaces.has("cases")) return targets;
    if (page === "dashboard") {
      var selected = [];
      targets.forEach(function (target) {
        if (target.partial === "stats") selected.push(target);
        if (target.partial === "recent-alerts" && surfaces.has("alerts")) selected.push(target);
        if (target.partial === "recent-cases" && surfaces.has("cases")) selected.push(target);
      });
      return selected;
    }
    return targets;
  }

  function setupOrgSocket(page) {
    var chip = document.getElementById("live-chip-" + page);
    if (!chip) return;
    var url = chip.getAttribute("data-ws-url");
    if (!url || typeof WebSocket === "undefined") {
      setChipState(chip, "offline");
      return;
    }

    var backoff = 500;
    var pending = new Set();
    var timer = null;
    var needsRefreshOnVisible = false;

    function flush() {
      timer = null;
      if (document.hidden) {
        needsRefreshOnVisible = true;
        return;
      }
      var surfaces = pending;
      pending = new Set();
      targetsFor(page, surfaces).forEach(function (target) {
        if (!document.querySelector(target.selector)) return;
        htmx.ajax("GET", partialUrl(target.partial), {
          target: target.selector,
          swap: "innerHTML"
        });
      });
    }

    function schedule(surface) {
      if (surface) pending.add(surface);
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(flush, 250);
    }

    function connect() {
      var socket = new WebSocket(url);
      socket.addEventListener("open", function () {
        backoff = 500;
        setChipState(chip, "live");
      });
      socket.addEventListener("message", function (event) {
        var frame;
        try {
          frame = JSON.parse(event.data);
        } catch (e) {
          return;
        }
        if (frame.type === "event" && frame.event_type === "refresh") {
          schedule(frame.payload && frame.payload.surface);
        }
      });
      socket.addEventListener("error", function () {
        setChipState(chip, "reconnecting");
      });
      socket.addEventListener("close", function (event) {
        if ([4401, 4404, 4000].indexOf(event.code) !== -1) {
          setChipState(chip, "offline");
          return;
        }
        setChipState(chip, "reconnecting");
        setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 8000);
      });
    }

    document.addEventListener("visibilitychange", function () {
      if (document.hidden || !needsRefreshOnVisible) return;
      needsRefreshOnVisible = false;
      schedule(null);
    });

    connect();
  }

  function setupCaseTimeline() {
    var list = document.getElementById("audit-timeline");
    if (!list) return;
    var url = list.getAttribute("data-ws-url");
    if (!url || typeof WebSocket === "undefined") return;

    var pagesRefresh = null;
    var tasksRefresh = null;
    var socket = null;
    var backoff = 1000;
    var BACKOFF_MAX = 15000;
    var RETRY_STOP_CODES = [4000, 4401];

    function pad(value) {
      return (value < 10 ? "0" : "") + value;
    }

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

    function slug(kind) {
      return String(kind || "")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
    }

    function rendered(id) {
      return !!list.querySelector('[data-event-id="' + id + '"]');
    }

    function anchor() {
      var entries = list.querySelectorAll("[data-event-id]");
      if (!entries.length) return null;
      return entries[0].getAttribute("data-event-id");
    }

    function node(tag, className, text) {
      var element = document.createElement(tag);
      if (className) element.className = className;
      if (text) element.textContent = text;
      return element;
    }

    function entry(event) {
      var item = node("li", "tl tl-" + slug(event.kind));
      item.setAttribute("data-event-id", event.id);
      item.appendChild(node("p", "tl-title", event.title || ""));
      if (event.description) item.appendChild(node("pre", "tl-body", event.description));

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

    function prepend(event) {
      if (!event || !event.id || rendered(event.id)) return;
      var placeholder = list.querySelector("li.empty");
      if (placeholder) placeholder.parentNode.removeChild(placeholder);
      list.insertBefore(entry(event), list.firstChild);
    }

    function refreshCard(id, get, set) {
      if (get() || !document.getElementById(id)) return;
      set(
        fetch(window.location.pathname + window.location.search, {
          credentials: "same-origin",
          headers: { "X-Requested-With": "fetch" }
        })
          .then(function (response) {
            return response.ok ? response.text() : "";
          })
          .then(function (html) {
            var fresh = new DOMParser().parseFromString(html, "text/html").getElementById(id);
            var current = document.getElementById(id);
            if (fresh && current) current.replaceWith(fresh);
          })
          .catch(function () {
            /* Offline or a refused session: leave the list as it is. */
          })
          .then(function () {
            set(null);
          })
      );
    }

    function handle(message) {
      if (!message || typeof message !== "object") return;
      if (message.type === "presence") {
        setPresence(message.count);
        return;
      }
      if (message.type === "timeline" && Array.isArray(message.events)) {
        message.events.forEach(prepend);
        return;
      }
      if (message.type === "event" && message.payload) {
        if (message.event_type === "page") {
          refreshCard(
            "case-pages",
            function () { return pagesRefresh; },
            function (value) { pagesRefresh = value; }
          );
        } else if (message.event_type === "task") {
          refreshCard(
            "case-tasks",
            function () { return tasksRefresh; },
            function (value) { tasksRefresh = value; }
          );
        } else {
          prepend(message.payload.event);
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
          return;
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
  }

  function setupStandalonePresence() {
    var el = document.getElementById("case-presence");
    if (!el || document.getElementById("audit-timeline")) return;
    var url = el.getAttribute("data-ws-url");
    if (!url || typeof WebSocket === "undefined") {
      var text = el.querySelector("[data-presence-text]");
      if (text) text.textContent = "Live updates unavailable";
      return;
    }
    var pingTimer = null;
    var socket = new WebSocket(url);
    socket.addEventListener("message", function (event) {
      var frame;
      try {
        frame = JSON.parse(event.data);
      } catch (e) {
        return;
      }
      if (frame.type === "presence") setPresence(frame.count);
    });
    socket.addEventListener("open", function () {
      pingTimer = setInterval(function () {
        if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "presence_ping" }));
      }, 30000);
    });
    socket.addEventListener("close", function () {
      if (pingTimer !== null) clearInterval(pingTimer);
    });
  }

  ["alert-list", "case-list", "dashboard"].forEach(function (page) {
    if (document.getElementById("live-chip-" + page)) setupOrgSocket(page);
  });
  setupCaseTimeline();
  setupStandalonePresence();
})();
