"use strict";

(function () {
  // ---- Sprache --------------------------------------------------------------
  // Übersetzungen liefert der Server im <script id="i18n">; deutsche Texte sind der Schlüssel.
  var I18N = (function () {
    try { return JSON.parse(document.getElementById("i18n").textContent); } catch (e) { return {}; }
  })();
  var LOCALE = I18N.locale || "de-DE";
  var TEXTS = I18N.texts || {};
  var PANEL = I18N.panel || "Pterodactyl";
  function tr(text, values) {
    var out = (TEXTS[text] || text).split("{panel}").join(PANEL);
    if (values) Object.keys(values).forEach(function (k) { out = out.split("{" + k + "}").join(values[k]); });
    return out;
  }
  function dec(value) { return LOCALE.indexOf("de") === 0 ? String(value).replace(".", ",") : String(value); }

  // ---- Balken ---------------------------------------------------------------
  function paintBar(el, pct) {
    pct = Math.max(0, Math.min(100, Number(pct) || 0));
    el.style.width = pct + "%";
    el.classList.toggle("warn", pct >= 75 && pct < 90);
    el.classList.toggle("crit", pct >= 90);
  }
  function paintBars(root) {
    root.querySelectorAll("[data-pct]").forEach(function (el) { paintBar(el, el.dataset.pct); });
  }

  // ---- Kopieren -------------------------------------------------------------
  document.addEventListener("click", function (ev) {
    var el = ev.target.closest(".copyable");
    if (!el) return;
    var text = el.dataset.copy || el.textContent.trim();
    if (!text || text === "–") return;
    var done = function () {
      el.classList.add("copied");
      setTimeout(function () { el.classList.remove("copied"); }, 900);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done);
    } else {
      var range = document.createRange();
      range.selectNodeContents(el);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      try { document.execCommand("copy"); done(); } catch (e) { /* Auswahl bleibt markiert */ }
    }
  });

  // ---- Bestätigungen --------------------------------------------------------
  document.addEventListener("submit", function (ev) {
    var msg = ev.target.dataset.confirm;
    if (msg && !window.confirm(msg)) ev.preventDefault();
  });

  // ---- Live-Werte -----------------------------------------------------------
  var FIELDS = ["state_label", "cpu_label", "cpu_text", "mem_text", "disk_text", "ingame_name", "address",
                "time_label", "time_value", "updated_at"];

  function updateScope(scope, s) {
    FIELDS.forEach(function (f) {
      scope.querySelectorAll('[data-f="' + f + '"]').forEach(function (el) {
        if (el.textContent !== String(s[f])) el.textContent = s[f];
        if (el.dataset.copy !== undefined) el.dataset.copy = s[f];
      });
    });
    scope.querySelectorAll("[data-f-title]").forEach(function (el) { el.title = s[el.dataset.fTitle] || ""; });
    ["cpu_pct", "mem_pct", "disk_pct"].forEach(function (f) {
      scope.querySelectorAll('[data-f="' + f + '"]').forEach(function (el) { paintBar(el, s[f]); });
    });
    scope.querySelectorAll('[data-f="state"]').forEach(function (el) {
      el.className = "pill state-" + s.state;
    });
    scope.querySelectorAll(".power").forEach(function (el) { el.dataset.state = s.state; });
    if (scope.classList.contains("server-card")) {
      scope.className = scope.className.replace(/\bstate-\S+/g, "").trim() + " state-" + s.state;
    }
  }

  function startLive() {
    var live = document.querySelectorAll("[data-live] [data-ident], [data-live][data-ident]");
    if (!live.length) return;
    var status = document.getElementById("monitor-status");
    function tick() {
      fetch("/api/overview", { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) {
          if (r.status === 401) { window.location = "/login"; throw new Error("login"); }
          return r.json();
        })
        .then(function (data) {
          var byId = {};
          data.servers.forEach(function (s) { byId[s.identifier] = s; });
          live.forEach(function (scope) {
            var s = byId[scope.dataset.ident];
            if (s) updateScope(scope, s);
          });
          if (window.hatchtowerFilter) window.hatchtowerFilter();
          if (window.hatchtowerSort) window.hatchtowerSort();
          var down = document.getElementById("panel-down");
          if (down) {
            down.hidden = !data.monitor.down_since;
            if (data.monitor.down_since) document.getElementById("panel-down-since").textContent = data.monitor.down_since;
          }
          if (status) {
            status.classList.toggle("is-bad", !data.monitor.healthy);
            status.textContent = data.monitor.healthy
              ? tr("Monitoring aktiv") + " · " + data.monitor.last_ok
              : (data.monitor.error || tr("Monitoring startet …"));
          }
        })
        .catch(function () {});
    }
    window.hatchtowerRefresh = tick;
    // Liest nur die lokale Datenbank von Hatchtower, nicht das Panel: alle 5 s ist günstig
    setInterval(function () { if (!document.hidden) tick(); }, 5000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) tick(); });
  }

  // ---- Konsole --------------------------------------------------------------
  var ANSI_RE = /\x1b\[([0-9;]*)m/g;

  function ansiToNodes(text) {
    var frag = document.createDocumentFragment();
    var classes = [];
    var last = 0;
    var m;
    function push(chunk) {
      if (!chunk) return;
      var span = document.createElement("span");
      if (classes.length) span.className = classes.join(" ");
      span.textContent = chunk;
      frag.appendChild(span);
    }
    ANSI_RE.lastIndex = 0;
    while ((m = ANSI_RE.exec(text)) !== null) {
      push(text.slice(last, m.index));
      last = ANSI_RE.lastIndex;
      (m[1] || "0").split(";").forEach(function (code) {
        var n = Number(code);
        if (n === 0) classes = [];
        else if (n === 1) classes.push("ansi-b");
        else if ((n >= 30 && n <= 37) || (n >= 90 && n <= 97)) {
          classes = classes.filter(function (c) { return c === "ansi-b"; });
          classes.push("ansi-" + n);
        } else if (n === 39) classes = classes.filter(function (c) { return c === "ansi-b"; });
      });
    }
    push(text.slice(last).replace(/\x1b\[[0-9;?]*[A-Za-z]/g, ""));
    return frag;
  }

  function startConsole() {
    var box = document.getElementById("console");
    if (!box) return;
    var stateEl = document.getElementById("console-state");
    var filterEl = document.getElementById("console-filter");
    var pauseBtn = document.getElementById("console-pause");
    var dlBtn = document.getElementById("console-download");
    var paused = false;
    var lines = [];
    var MAX = 3000;

    function matches(text) {
      var q = filterEl.value.trim().toLowerCase();
      return !q || text.toLowerCase().indexOf(q) !== -1;
    }
    function addLine(text, cls) {
      var plain = text.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "");
      lines.push(plain);
      var div = document.createElement("div");
      if (cls) div.className = cls;
      div.dataset.plain = plain.toLowerCase();
      div.appendChild(ansiToNodes(text));
      if (!matches(plain)) div.classList.add("hidden");
      box.appendChild(div);
      while (box.childNodes.length > MAX) { box.removeChild(box.firstChild); lines.shift(); }
      if (!paused) box.scrollTop = box.scrollHeight;
    }

    filterEl.addEventListener("input", function () {
      var q = filterEl.value.trim().toLowerCase();
      box.childNodes.forEach(function (div) {
        div.classList.toggle("hidden", !!q && div.dataset.plain.indexOf(q) === -1);
      });
    });
    pauseBtn.addEventListener("click", function () {
      paused = !paused;
      pauseBtn.textContent = paused ? tr("Weiter") : tr("Pause");
      if (!paused) box.scrollTop = box.scrollHeight;
    });
    dlBtn.addEventListener("click", function () {
      var blob = new Blob([lines.join("\n") + "\n"], { type: "text/plain" });
      var a = document.createElement("a");
      var stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      a.href = URL.createObjectURL(blob);
      a.download = (box.dataset.name || "server").replace(/[^\w.-]+/g, "_") + "-" + stamp + ".log";
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    });

    var retry = 2000;
    function connect() {
      stateEl.textContent = tr("verbinde …");
      var es = new EventSource(box.dataset.stream);
      es.onopen = function () { stateEl.textContent = tr("live"); retry = 2000; };
      es.onmessage = function (ev) {
        var msg;
        try { msg = JSON.parse(ev.data); } catch (e) { return; }
        if (msg.type === "line") addLine(msg.text, msg.source === "console output" ? "" : "sys");
        else if (msg.type === "stats") document.dispatchEvent(new CustomEvent("hatchtower:stats", { detail: msg }));
        else if (msg.type === "status") addLine("[Hatchtower] " + tr("Status laut {panel}: {state}", { state: msg.state }), "sys");
        else if (msg.type === "error") {
          addLine("[Hatchtower] " + msg.text, "err");
          stateEl.textContent = tr("getrennt");
          // Wings refuses the connection: retrying does not help until the configuration changes
          if (msg.fatal) es.close();
        }
      };
      es.onerror = function () {
        es.close();
        stateEl.textContent = tr("getrennt, neuer Versuch …");
        setTimeout(connect, retry);
        retry = Math.min(retry * 2, 30000);
      };
    }
    connect();
  }

  // ---- Steuerung -------------------------------------------------------------
  function csrfToken() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : "";
  }

  function toast(text, ok) {
    var box = document.getElementById("toasts");
    if (!box) return;
    var el = document.createElement("div");
    el.className = "toast " + (ok ? "ok" : "err");
    el.textContent = text;
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, ok ? 4000 : 7000);
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken(), Accept: "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (r.status === 401) { window.location = "/login"; throw new Error("login"); }
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(data.error || tr("Fehler {code}", { code: r.status }));
        return data;
      });
    });
  }

  var POWER_CONFIRM = {
    stop: "{name} wirklich stoppen?", restart: "{name} wirklich neu starten?", kill: "{name} wirklich hart beenden (Kill)?",
  };

  function startControls() {
    document.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-power]");
      if (!btn) return;
      var box = btn.closest(".power");
      var signal = btn.dataset.power;
      var name = box.dataset.name;
      if (signal !== "start") {
        var msg = tr(POWER_CONFIRM[signal], { name: name });
        if (!window.confirm(msg)) return;
        if (signal === "kill" && !window.confirm(tr("Kill beendet den Prozess sofort, ohne zu speichern. Fortfahren?"))) return;
      }
      box.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
      postJson("/api/servers/" + box.dataset.powerFor + "/power", { signal: signal })
        .then(function (data) { toast(data.message || tr("Gesendet"), true); })
        .catch(function (e) { toast(e.message, false); })
        .then(function () {
          setTimeout(function () { box.querySelectorAll("button").forEach(function (b) { b.disabled = false; }); }, 3000);
          // Kurz eng nachverfolgen, damit starting/stopping sofort erscheint
          if (window.hatchtowerRefresh) {
            var n = 0;
            var fast = setInterval(function () { window.hatchtowerRefresh(); if (++n >= 45) clearInterval(fast); }, 1000);
          }
        });
    });

    // Konsolenbefehle mit Verlauf (Pfeil hoch/runter)
    var form = document.getElementById("console-cmd");
    if (form) {
      var input = form.querySelector("input");
      var history = [];
      var pos = 0;
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var cmd = input.value.trim();
        if (!cmd) return;
        input.disabled = true;
        postJson(form.dataset.url, { command: cmd })
          .then(function () {
            history.push(cmd);
            pos = history.length;
            input.value = "";
            var box = document.getElementById("console");
            if (box) {
              var div = document.createElement("div");
              div.className = "sys";
              div.dataset.plain = ("> " + cmd).toLowerCase();
              div.textContent = "> " + cmd;
              box.appendChild(div);
              box.scrollTop = box.scrollHeight;
            }
          })
          .catch(function (e) { toast(e.message, false); })
          .then(function () { input.disabled = false; input.focus(); });
      });
      input.addEventListener("keydown", function (ev) {
        if (ev.key === "ArrowUp" && pos > 0) { pos--; input.value = history[pos]; ev.preventDefault(); }
        else if (ev.key === "ArrowDown") { pos = Math.min(history.length, pos + 1); input.value = history[pos] || ""; ev.preventDefault(); }
      });
    }

    // Umbenennen per Eingabedialog
    document.querySelectorAll("form[data-rename]").forEach(function (f) {
      f.addEventListener("submit", function (ev) {
        var old = f.dataset.rename;
        var name = window.prompt(tr("Neuer Name für „{name}“:", { name: old }), old);
        if (!name || name.trim() === "" || name.trim() === old) { ev.preventDefault(); return; }
        f.querySelector('input[name="new"]').value = name.trim();
      });
    });

    // Längere Vorgänge: Button sperren und Status zeigen
    document.querySelectorAll("form[data-busy]").forEach(function (f) {
      f.addEventListener("submit", function () {
        var b = f.querySelector('button[type="submit"]');
        if (b) { b.disabled = true; b.textContent = f.dataset.busy; }
      });
    });

    // Editor: Strg+S speichert
    var editor = document.querySelector("textarea.editor");
    if (editor) {
      editor.addEventListener("keydown", function (ev) {
        if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
          ev.preventDefault();
          document.getElementById("editor-form").requestSubmit();
        }
      });
    }

    // Zeitplan: Befehlsfeld nur bei Aktion "Befehl"
    var repeat = document.getElementById("sched-repeat");
    if (repeat) {
      var syncRepeat = function () {
        var once = repeat.value === "once";
        document.getElementById("sched-date").hidden = !once;
        document.getElementById("sched-date").querySelector("input").required = once;
        document.getElementById("sched-weekdays").hidden = once;
      };
      repeat.addEventListener("change", syncRepeat);
      syncRepeat();
    }

    var action = document.getElementById("sched-action");
    var cmdField = document.getElementById("sched-command");
    if (action && cmdField) {
      var sync = function () {
        var update = action.value === "update";
        cmdField.hidden = action.value !== "command";
        ["sched-update"].forEach(function (id) { var el = document.getElementById(id); if (el) el.hidden = !update; });
        ["sched-only-running", "sched-warn"].forEach(function (id) { var el = document.getElementById(id); if (el) el.hidden = update; });
      };
      action.addEventListener("change", sync);
      sync();
      // Nur Aktionen anbieten, für die der Benutzer auf dem gewählten Server das Recht hat
      var schedServer = document.getElementById("sched-server");
      if (schedServer) {
        var syncActions = function () {
          var opt = schedServer.options[schedServer.selectedIndex];
          var allowed = opt && opt.dataset.actions ? opt.dataset.actions.split(" ") : null;
          Array.prototype.forEach.call(action.options, function (o) {
            o.disabled = allowed !== null && allowed.indexOf(o.value) < 0;
          });
          if (action.selectedOptions[0] && action.selectedOptions[0].disabled) {
            var first = Array.prototype.find.call(action.options, function (o) { return !o.disabled; });
            if (first) action.value = first.value;
          }
          sync();
        };
        schedServer.addEventListener("change", syncActions);
        syncActions();
      }
    }
  }

  // ---- Verlauf: CPU, RAM, Netzwerk (eigene Mini-Diagramme, ohne Bibliothek) --------
  function fmtBytes(v, perSecond) {
    var units = perSecond ? ["B/s", "KB/s", "MB/s", "GB/s"] : ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return dec(v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)) + " " + units[i];
  }
  // Uhrzeiten in der Zeitzone, die in Hatchtower eingestellt ist (nicht die des Browsers)
  var TZ = (document.querySelector('meta[name="timezone"]') || {}).content || undefined;
  function fmtClock(ts, withSeconds) {
    var opts = { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: TZ };
    if (withSeconds) opts.second = "2-digit";
    try { return new Intl.DateTimeFormat(LOCALE, opts).format(new Date(ts * 1000)); }
    catch (e) { delete opts.timeZone; return new Intl.DateTimeFormat(LOCALE, opts).format(new Date(ts * 1000)); }
  }
  function fmtDay(ts) {
    try { return new Intl.DateTimeFormat(LOCALE, { day: "2-digit", month: "2-digit", timeZone: TZ }).format(new Date(ts * 1000)); }
    catch (e) { return new Date(ts * 1000).toLocaleDateString(LOCALE, { day: "2-digit", month: "2-digit" }); }
  }
  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function drawChart(canvas, opts) {
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    var L = 58, R = 8, T = 8, B = 20;
    var pw = w - L - R, ph = h - T - B;
    var x = function (t) { return L + (t - opts.t0) / (opts.t1 - opts.t0) * pw; };
    var y = function (v) { return T + ph - Math.min(v / opts.max, 1) * ph; };

    ctx.font = "11px " + cssVar("--font", "sans-serif");
    ctx.fillStyle = cssVar("--faint", "#6f6887");
    ctx.strokeStyle = cssVar("--line", "rgba(255,255,255,0.06)");
    ctx.lineWidth = 1;
    [0, 0.5, 1].forEach(function (k) {
      var yy = Math.round(T + ph - k * ph) + 0.5;
      ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(w - R, yy); ctx.stroke();
      ctx.textAlign = "right"; ctx.textBaseline = "middle";
      ctx.fillText(opts.fmt(opts.max * k), L - 6, yy);
    });
    ctx.textBaseline = "alphabetic";
    ctx.textAlign = "left"; ctx.fillText(opts.startLabel, L, h - 5);
    ctx.textAlign = "right"; ctx.fillText(opts.endLabel, w - R, h - 5);

    opts.series.forEach(function (s, si) {
      // In zusammenhängende Abschnitte teilen, damit Lücken auch Lücken bleiben
      var segments = [], seg = null, lastT = null;
      opts.points.forEach(function (p) {
        var v = p[s.index];
        if (v === null || v === undefined || p[0] < opts.t0) { seg = null; return; }
        if (!seg || (lastT !== null && p[0] - lastT > opts.gap)) { seg = []; segments.push(seg); }
        seg.push([x(p[0]), y(v)]);
        lastT = p[0];
      });
      segments.forEach(function (pts) {
        ctx.beginPath();
        pts.forEach(function (pt, i) { if (i === 0) ctx.moveTo(pt[0], pt[1]); else ctx.lineTo(pt[0], pt[1]); });
        ctx.strokeStyle = s.color; ctx.lineWidth = 1.8; ctx.lineJoin = "round";
        ctx.stroke();
        if (si === 0 && pts.length > 1) {
          ctx.lineTo(pts[pts.length - 1][0], T + ph); ctx.lineTo(pts[0][0], T + ph); ctx.closePath();
          ctx.globalAlpha = 0.12; ctx.fillStyle = s.color; ctx.fill(); ctx.globalAlpha = 1;
        }
      });
    });

    if (opts.hoverT !== undefined) {
      var hx = Math.round(x(opts.hoverT)) + 0.5;
      ctx.strokeStyle = cssVar("--faint", "rgba(255,255,255,0.35)"); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(hx, T); ctx.lineTo(hx, T + ph); ctx.stroke();
    }
    canvas._chart = { opts: opts, x0: L, pw: pw };
  }

  function startCharts() {
    var sec = document.getElementById("verlauf");
    if (!sec) return;
    var url = sec.dataset.metrics;
    var range = 120;
    var data = { points: [], cpu_limit: 0, mem_limit: 0, bucket: 15 };
    var live = [];
    var lastStats = null, statsSeen = 0;
    var canvases = {};
    sec.querySelectorAll("canvas[data-chart]").forEach(function (c) { canvases[c.dataset.chart] = c; });
    var nowEls = {};
    sec.querySelectorAll("[data-now]").forEach(function (e) { nowEls[e.dataset.now] = e; });
    // read on every draw, so switching the theme recolours the charts
    function chartColors() {
      return { cpu: cssVar("--accent-2", "#d98fa4"), mem: cssVar("--blue", "#38bdf8"), rx: cssVar("--green", "#34d399"), tx: cssVar("--amber", "#fbbf24") };
    }
    var colors = chartColors();

    function cpuFmt(v) { return Math.round(v) + " %"; }
    function maxOf(pts, idx) {
      var m = 0;
      pts.forEach(function (p) { idx.forEach(function (i) { if (p[i] > m) m = p[i]; }); });
      return m;
    }

    function draw(hoverT) {
      colors = chartColors();
      var now = Date.now() / 1000;
      var pts = range === 120 ? live : data.points;
      var t0 = now - range, t1 = now;
      var gap = range === 120 ? 20 : Math.max(data.bucket * 3, 45);
      var labelStart = range === 120 ? tr("vor 2 Min.") : fmtClock(t0) + (range >= 86400 ? " (" + fmtDay(t0) + ")" : "");
      var common = { points: pts, t0: t0, t1: t1, gap: gap, startLabel: labelStart, endLabel: tr("jetzt"), hoverT: hoverT };
      var cpuMax = data.cpu_limit ? 100 : Math.max(100, Math.ceil(maxOf(pts, [1]) / 100) * 100);
      drawChart(canvases.cpu, Object.assign({}, common, {
        max: cpuMax, fmt: data.cpu_limit ? cpuFmt : function (v) { return dec((v / 100).toFixed(1)) + " " + tr("K"); },
        series: [{ index: 1, color: colors.cpu }],
      }));
      var memMax = data.mem_limit || Math.max(maxOf(pts, [2]) * 1.2, 1024 * 1024 * 1024);
      drawChart(canvases.mem, Object.assign({}, common, {
        max: memMax, fmt: function (v) { return fmtBytes(v); }, series: [{ index: 2, color: colors.mem }],
      }));
      var netMax = Math.max(maxOf(pts, [3, 4]) * 1.2, 50 * 1024);
      drawChart(canvases.net, Object.assign({}, common, {
        max: netMax, fmt: function (v) { return fmtBytes(v, true); },
        series: [{ index: 3, color: colors.rx }, { index: 4, color: colors.tx }],
      }));
      var last = null;
      for (var i = pts.length - 1; i >= 0; i--) { if (pts[i][1] !== null) { last = pts[i]; break; } }
      if (hoverT === undefined) showValues(last, range === 120 && last && now - last[0] < 30 ? "" : null);
    }

    function showValues(p, prefix) {
      if (!p) { nowEls.cpu.textContent = nowEls.mem.textContent = nowEls.net.textContent = tr("keine Daten"); return; }
      var pre = prefix === null ? "" : (prefix || "");
      nowEls.cpu.textContent = pre + (p[1] === null ? "–" : (data.cpu_limit ? cpuFmt(p[1]) : tr("{n} Kerne", { n: dec((p[1] / 100).toFixed(1)) })));
      nowEls.mem.textContent = pre + (p[2] === null ? "–" : fmtBytes(p[2]) + (data.mem_limit ? " / " + fmtBytes(data.mem_limit) : ""));
      nowEls.net.textContent = pre + (p[3] === null ? "–" : "↓ " + fmtBytes(p[3], true) + "  ↑ " + fmtBytes(p[4] || 0, true));
    }

    function load() {
      fetch(url + "?range=" + range, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          data = d;
          if (range === 120 && Date.now() / 1000 - statsSeen > 5) live = d.points.map(function (p) { return p.slice(); });
          draw();
        })
        .catch(function () {});
    }

    // Live-Werte aus der Konsolen-Verbindung (Wings schickt etwa jede Sekunde Statistiken)
    document.addEventListener("hatchtower:stats", function (ev) {
      var s = ev.detail;
      var t = Date.now() / 1000;
      var rx = null, tx = null;
      if (lastStats && t - lastStats.t > 0) {
        rx = Math.max(s.rx - lastStats.rx, 0) / (t - lastStats.t);
        tx = Math.max(s.tx - lastStats.tx, 0) / (t - lastStats.t);
      }
      lastStats = { t: t, rx: s.rx, tx: s.tx };
      var cpu = data.cpu_limit ? Math.min(s.cpu / data.cpu_limit * 100, 100) : s.cpu;
      if (statsSeen === 0 || t - statsSeen > 5) live = live.filter(function (p) { return p[0] < t - 1; });
      statsSeen = t;
      live.push([t, cpu, s.mem, rx, tx]);
      live = live.filter(function (p) { return p[0] >= t - 130; });
      if (range === 120) draw();
    });

    sec.querySelectorAll("[data-range]").forEach(function (b) {
      b.addEventListener("click", function () {
        range = Number(b.dataset.range);
        sec.querySelectorAll("[data-range]").forEach(function (o) { o.setAttribute("aria-pressed", String(o === b)); });
        load();
      });
    });

    // Mauszeiger: Werte an dieser Stelle anzeigen
    Object.keys(canvases).forEach(function (k) {
      var c = canvases[k];
      c.addEventListener("mousemove", function (ev) {
        if (!c._chart) return;
        var rect = c.getBoundingClientRect();
        var o = c._chart.opts;
        var t = o.t0 + (ev.clientX - rect.left - c._chart.x0) / c._chart.pw * (o.t1 - o.t0);
        var best = null;
        o.points.forEach(function (p) { if (!best || Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p; });
        if (!best || Math.abs(best[0] - t) > Math.max(o.gap, (o.t1 - o.t0) / 60)) return;
        draw(best[0]);
        showValues(best, fmtClock(best[0], range <= 3600) + " · ");
      });
      c.addEventListener("mouseleave", function () { draw(); });
    });

    var resizeTimer;
    window.addEventListener("resize", function () { clearTimeout(resizeTimer); resizeTimer = setTimeout(function () { draw(); }, 150); });
    // Ohne Live-Verbindung (Server aus oder kein Konsolenrecht): alle 15 s aus der Datenbank nachladen
    setInterval(function () {
      if (range !== 120 || Date.now() / 1000 - statsSeen > 5) load();
      else draw();
    }, 15000);
    setInterval(function () { if (range === 120) draw(); }, 2000);
    load();
  }

  // ---- Statusfilter in der Übersicht (Mehrfachauswahl, Standard: alle) ---------------
  function startStatusFilter() {
    var bar = document.getElementById("status-filter");
    var grid = document.getElementById("server-grid");
    if (!bar || !grid) return;
    var buttons = bar.querySelectorAll("[data-filter]");
    var allBtn = bar.querySelector('[data-filter="all"]');
    var empty = document.getElementById("filter-empty");
    var selected = [];   // leer = alle
    var tagBar = document.getElementById("tag-filter");
    var tag = "";

    function stateOf(card) {
      var m = card.className.match(/\bstate-(\S+)/);
      return m ? m[1] : "unknown";
    }

    function apply() {
      var cards = grid.querySelectorAll(".server-card");
      var counts = { all: cards.length };
      var visible = 0;
      cards.forEach(function (card) {
        var st = stateOf(card);
        counts[st] = (counts[st] || 0) + 1;
        var show = selected.length === 0 || selected.some(function (group) { return group.split(" ").indexOf(st) !== -1; });
        if (tag) show = show && (card.dataset.tags || "").split("|").indexOf(tag) !== -1;
        card.classList.toggle("filtered-out", !show);
        if (show) visible++;
      });
      bar.querySelectorAll("[data-count]").forEach(function (el) {
        var n = el.dataset.count.split(" ").reduce(function (sum, k) { return sum + (counts[k] || 0); }, 0);
        el.textContent = n;
        el.closest(".sf").dataset.empty = n === 0 ? "1" : "0";
      });
      buttons.forEach(function (b) {
        var key = b.dataset.filter;
        b.setAttribute("aria-pressed", String(key === "all" ? selected.length === 0 : selected.indexOf(key) !== -1));
      });
      if (empty) empty.hidden = visible > 0;
      document.dispatchEvent(new Event("hatchtower:filtered"));
    }

    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        var key = b.dataset.filter;
        if (key === "all") selected = [];
        else if (selected.indexOf(key) !== -1) selected = selected.filter(function (k) { return k !== key; });
        else selected.push(key);
        apply();
      });
    });
    if (tagBar) {
      tagBar.querySelectorAll("[data-tag]").forEach(function (b) {
        b.addEventListener("click", function () {
          tag = b.dataset.tag;
          tagBar.querySelectorAll("[data-tag]").forEach(function (o) { o.setAttribute("aria-pressed", String(o === b)); });
          apply();
        });
      });
    }
    window.hatchtowerFilter = apply;
    apply();
  }

  // ---- Sortierung der Übersicht (pro Browser gemerkt) -------------------------------
  var STATUS_ORDER = { running: 0, starting: 1, stopping: 2, offline: 3, suspended: 4, unknown: 5 };

  function startSort() {
    var select = document.getElementById("sort-select");
    var grid = document.getElementById("server-grid");
    if (!select || !grid) return;
    try { select.value = localStorage.getItem("hatchtower.sort") || "status-online"; } catch (e) { /* Standard */ }
    if (!select.value) select.value = "status-online";

    function nameOf(card) {
      var h = card.querySelector(".card-head h2");
      return h ? h.textContent.trim() : "";
    }
    function rankOf(card) {
      var m = card.className.match(/\bstate-(\S+)/);
      var r = STATUS_ORDER[m ? m[1] : "unknown"];
      return r === undefined ? 5 : r;
    }

    function apply() {
      var mode = select.value;
      var cards = Array.prototype.slice.call(grid.querySelectorAll(".server-card"));
      cards.sort(function (a, b) {
        var fav = (b.classList.contains("is-favorite") ? 1 : 0) - (a.classList.contains("is-favorite") ? 1 : 0);
        if (fav) return fav;
        var byName = nameOf(a).localeCompare(nameOf(b), LOCALE, { sensitivity: "base", numeric: true });
        if (mode === "name-desc") return -byName;
        if (mode === "status-online") return rankOf(a) - rankOf(b) || byName;
        if (mode === "status-offline") return rankOf(b) - rankOf(a) || byName;
        return byName;
      });
      var changed = cards.some(function (c, i) { return grid.children[i] !== c; });
      if (changed) cards.forEach(function (c) { grid.appendChild(c); });
    }

    select.addEventListener("change", function () {
      try { localStorage.setItem("hatchtower.sort", select.value); } catch (e) { /* Speicher nicht verfügbar (z. B. privater Modus) */ }
      apply();
    });
    window.hatchtowerSort = apply;
    apply();
  }

  // ---- Handy im Modus "Desktop-Website" erkennen ----------------------------------------
  // Dann ignoriert der Browser die Handy-Ansicht und verkleinert die ganze Seite.
  // Abstellen lässt sich das nur im Browser-Menü, deshalb ein deutlicher Hinweis.
  function checkDesktopMode() {
    var touch = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
    var phone = Math.min(screen.width, screen.height) <= 540;
    if (!touch || !phone || window.innerWidth <= 900) return;
    var main = document.querySelector("main.container");
    if (!main) return;
    var box = document.createElement("div");
    box.className = "desktop-mode-hint";
    box.setAttribute("role", "alert");
    // Texte stammen aus dem eigenen Übersetzungskatalog, nicht aus Eingaben
    box.innerHTML = tr("<strong>Dein Browser zeigt die Desktop-Version.</strong> Deshalb ist alles so klein.") + " " +
      tr("Öffne das Browser-Menü (⋮ oben rechts) und entferne den Haken bei <strong>„Desktop-Website“</strong>.") + " " +
      tr("Danach passt sich die Seite deinem Handy an.");
    main.insertBefore(box, main.firstChild);
  }

  // ---- Menü auf dem Handy ------------------------------------------------------------
  function startMenu() {
    var btn = document.getElementById("menu-toggle");
    var menu = document.getElementById("topbar-menu");
    if (!btn || !menu) return;
    var bar = btn.closest(".sidebar-top");
    function set(open) {
      // the menu starts right below the top bar, whose height depends on the community name
      if (open && bar) document.documentElement.style.setProperty("--menu-top", Math.round(bar.getBoundingClientRect().bottom) + "px");
      document.documentElement.classList.toggle("menu-open", open);
      menu.classList.toggle("open", open);
      btn.setAttribute("aria-expanded", String(open));
      btn.setAttribute("aria-label", open ? tr("Menü schließen") : tr("Menü öffnen"));
    }
    btn.addEventListener("click", function () { set(!menu.classList.contains("open")); });
    document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") set(false); });
    document.addEventListener("click", function (ev) {
      if (menu.classList.contains("open") && !menu.contains(ev.target) && !btn.contains(ev.target)) set(false);
    });
  }

  // ---- Serverseite: Reiter statt einer langen Seite ----------------------------------
  function startServerTabs() {
    var nav = document.getElementById("server-tabs");
    if (!nav) return;
    var links = nav.querySelectorAll("[data-tab-link]");
    var panels = document.querySelectorAll("[data-tab]");
    var keys = [];
    links.forEach(function (a) { keys.push(a.dataset.tabLink); });
    function keyFor(hash) {
      var id = (hash || "").replace(/^#/, "");
      if (keys.indexOf(id) >= 0) return id;
      var el = id && document.getElementById(id);
      var panel = el && el.closest("[data-tab]");
      return panel ? panel.dataset.tab : keys[0];
    }
    function show(key) {
      links.forEach(function (a) {
        var on = a.dataset.tabLink === key;
        a.classList.toggle("active", on);
        if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
      });
      panels.forEach(function (p) { p.hidden = p.dataset.tab !== key; });
      // Diagramme und Konsole brauchen nach dem Einblenden ihre echte Größe
      window.dispatchEvent(new Event("resize"));
      var con = document.getElementById("console");
      if (key === "konsole" && con) con.scrollTop = con.scrollHeight;
    }
    links.forEach(function (a) {
      a.addEventListener("click", function (ev) {
        ev.preventDefault();
        history.replaceState(null, "", "#" + a.dataset.tabLink);
        show(a.dataset.tabLink);
      });
    });
    window.addEventListener("hashchange", function () { show(keyFor(location.hash)); });
    nav.hidden = false;
    show(keyFor(location.hash));
  }

  // ---- Server page: the "Spieler" tab reloads itself while it is visible ------------------
  function startPresenceTab() {
    var box = document.querySelector("[data-presence]");
    if (!box) return;
    function reload() {
      if (box.hidden || document.hidden) return;
      fetch(box.dataset.presence, { credentials: "same-origin", headers: { Accept: "text/html" } })
        .then(function (r) { return r.ok ? r.text() : null; })
        .then(function (html) { if (html !== null) box.innerHTML = html; })
        .catch(function () { /* next try in a few seconds */ });
    }
    setInterval(reload, 10000);
  }

  // ---- Theme switch (dark/light): applied at once, saved in the background ----------------
  function startThemeSwitch() {
    document.querySelectorAll(".theme-switch a[data-theme-key]").forEach(function (a) {
      a.addEventListener("click", function (ev) {
        if (ev.button !== 0 || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey) return;
        ev.preventDefault();
        var key = a.dataset.themeKey;
        document.documentElement.setAttribute("data-theme", key);
        var bar = document.querySelector('meta[name="theme-color"]');
        if (bar) bar.setAttribute("content", key === "hell" ? "#efebe9" : "#141215");
        var scheme = document.querySelector('meta[name="color-scheme"]');
        if (scheme) scheme.setAttribute("content", key === "hell" ? "light" : "dark");
        document.querySelectorAll(".theme-switch a[data-theme-key]").forEach(function (o) {
          var on = o.dataset.themeKey === key;
          o.classList.toggle("is-current", on);
          if (on) o.setAttribute("aria-current", "true"); else o.removeAttribute("aria-current");
        });
        window.dispatchEvent(new Event("resize"));  // redraw charts in the new colours
        // the server stores the choice and answers with a redirect we do not need to follow
        fetch(a.href, { credentials: "same-origin", redirect: "manual" }).catch(function () { location.href = a.href; });
      });
    });
  }

  // ---- Kontomenü in der Kopfzeile (Desktop) ------------------------------------------
  function startAccountMenu() {
    var btn = document.getElementById("account-toggle");
    if (!btn) return;
    var box = btn.parentNode;
    function set(open) {
      box.classList.toggle("open", open);
      btn.setAttribute("aria-expanded", String(open));
    }
    btn.addEventListener("click", function () { set(!box.classList.contains("open")); });
    document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") set(false); });
    document.addEventListener("click", function (ev) { if (!box.contains(ev.target)) set(false); });
  }

  // ---- Dateien: Mehrfachauswahl für ZIP-Download -----------------------------------
  function startFileSelection() {
    var form = document.getElementById("zip-form");
    if (!form) return;
    var boxes = document.querySelectorAll('input[name="names"][form="zip-form"]');
    var all = document.getElementById("select-all");
    var count = document.getElementById("selection-count");
    function update() {
      var n = 0;
      boxes.forEach(function (b) {
        b.closest("tr").classList.toggle("is-selected", b.checked);
        if (b.checked) n++;
      });
      form.hidden = n === 0;
      count.textContent = n === 1 ? tr("1 Eintrag ausgewählt") : tr("{n} Einträge ausgewählt", { n: n });
      if (all) {
        all.checked = n > 0 && n === boxes.length;
        all.indeterminate = n > 0 && n < boxes.length;
      }
    }
    boxes.forEach(function (b) { b.addEventListener("change", update); });
    if (all) {
      all.addEventListener("change", function () {
        boxes.forEach(function (b) { b.checked = all.checked; });
        update();
      });
    }
    document.getElementById("selection-clear").addEventListener("click", function () {
      boxes.forEach(function (b) { b.checked = false; });
      update();
    });
    var moveTarget = form.querySelector("[data-move-target]");
    startMoveDialog(form, boxes, moveTarget);
    form.addEventListener("submit", function (ev) {
      var by = ev.submitter;
      if (by && by.hasAttribute("data-move")) {
        if (!moveTarget.value) ev.preventDefault();
        return;
      }
      if (by && by.hasAttribute("data-delete-many")) {
        if (!window.confirm(form.dataset.confirmDelete)) ev.preventDefault();
        return;
      }
      if (by && by.hasAttribute("data-zip")) {
        toast(tr("ZIP wird erstellt, der Download startet gleich. Große Ordner brauchen etwas."), true);
      }
    });
    update();
  }

  // ---- Files: search across all folders (index on the server, see fileindex.py) ----------
  function startFileSearch() {
    var box = document.getElementById("file-search");
    if (!box) return;
    var input = box.querySelector("[data-search-input]");
    var status = box.querySelector("[data-search-status]");
    var list = box.querySelector("[data-search-results]");
    var reindex = box.querySelector("[data-search-reindex]");
    var base = box.dataset.base;
    var canDownload = box.hasAttribute("data-can-download");
    var ticket = 0, timer = null, poll = null, lastStatus = "";

    function folderUrl(dir) { return base + "?dir=" + encodeURIComponent(dir); }
    function showStatus(ix) {
      var text;
      if (ix.status === "running") text = tr("Index wird aufgebaut: {dirs} Ordner, {n} Einträge bisher. Gefundenes ist schon durchsuchbar.", { dirs: ix.dirs, n: ix.entries });
      else if (ix.status === "error") text = tr("Index unvollständig, das Einlesen wurde abgebrochen. „Neu einlesen“ versucht es noch einmal.");
      else if (ix.status === "done") text = tr("{n} Einträge in {dirs} Ordnern durchsuchbar · Stand {zeit}", { n: ix.entries, dirs: ix.dirs, zeit: ix.finished })
        + (ix.truncated ? " · " + tr("sehr großer Server, nicht alle Ordner erfasst") : "");
      else text = tr("Index wird vorbereitet …");
      status.textContent = text;
      reindex.disabled = ix.status === "running";
      // While the crawl runs, refresh status (and results) every few seconds
      var busy = ix.status === "running" || ix.status === "none";
      if (busy && !poll) poll = setInterval(function () { run(true); }, 4000);
      if (!busy && poll) { clearInterval(poll); poll = null; }
      lastStatus = ix.status;
    }
    function highlight(el, text, terms) {
      var lower = text.toLowerCase(), marks = [];
      terms.forEach(function (t) {
        if (/[*?]/.test(t)) return;
        var i = lower.indexOf(t);
        if (i >= 0) marks.push([i, i + t.length]);
      });
      marks.sort(function (a, b) { return a[0] - b[0]; });
      var pos = 0;
      marks.forEach(function (m) {
        if (m[0] < pos) return;
        el.appendChild(document.createTextNode(text.slice(pos, m[0])));
        var mk = document.createElement("mark");
        mk.textContent = text.slice(m[0], m[1]);
        el.appendChild(mk);
        pos = m[1];
      });
      el.appendChild(document.createTextNode(text.slice(pos)));
    }
    function draw(data, q) {
      list.textContent = "";
      if (!q) { list.hidden = true; return; }
      list.hidden = false;
      var terms = q.toLowerCase().split(/\s+/).filter(Boolean);
      if (!data.results.length) {
        var none = document.createElement("li");
        none.className = "faint small";
        none.textContent = lastStatus === "running" ? tr("Noch nichts gefunden – der Index wird noch aufgebaut.") : tr("Nichts gefunden.");
        list.appendChild(none);
        return;
      }
      data.results.forEach(function (r) {
        var li = document.createElement("li");
        li.className = r.is_dir ? "is-dir" : "is-file";
        var a = document.createElement("a");
        a.className = "file-search-name";
        a.href = r.is_dir ? folderUrl(r.path) : r.editable ? base + "/edit?file=" + encodeURIComponent(r.path) : folderUrl(r.dir);
        highlight(a, r.name + (r.is_dir ? "/" : ""), terms);
        li.appendChild(a);
        var where = document.createElement("a");
        where.className = "file-search-path faint small";
        where.href = folderUrl(r.dir);
        where.title = tr("Ordner öffnen");
        where.textContent = r.dir;
        li.appendChild(where);
        var meta = document.createElement("span");
        meta.className = "file-search-meta faint small";
        meta.textContent = r.is_dir ? tr("Ordner") : r.size;
        li.appendChild(meta);
        if (!r.is_dir && canDownload) {
          var dl = document.createElement("a");
          dl.className = "chip chip-sm";
          dl.href = base + "/download?file=" + encodeURIComponent(r.path);
          dl.textContent = tr("Herunterladen");
          li.appendChild(dl);
        }
        list.appendChild(li);
      });
      if (data.more) {
        var more = document.createElement("li");
        more.className = "faint small";
        more.textContent = tr("Weitere Treffer – Suche genauer eingrenzen.");
        list.appendChild(more);
      }
    }
    function run(quiet) {
      var q = input.value.trim();
      var mine = ++ticket;
      fetch(box.dataset.search + "?q=" + encodeURIComponent(q), { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) {
          if (r.status === 401) { window.location = "/login"; throw new Error("login"); }
          return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || tr("Fehler {code}", { code: r.status })); return d; });
        })
        .then(function (data) {
          if (mine !== ticket) return;
          showStatus(data.index);
          draw(data, q);
        })
        .catch(function (err) { if (!quiet && err.message !== "login") status.textContent = err.message; });
    }
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(run, 180);
    });
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { input.value = ""; run(true); }
      if (ev.key === "Enter") {
        ev.preventDefault();
        var first = list.querySelector("a.file-search-name");
        if (first) window.location = first.href;
      }
    });
    reindex.addEventListener("click", function () {
      reindex.disabled = true;
      postJson(box.dataset.reindex, {})
        .then(function (data) { showStatus(data.index); run(true); })
        .catch(function (err) { reindex.disabled = false; if (err.message !== "login") toast(err.message, false); });
    });
    run(true);   // shows the index state and starts building it when there is none yet
  }

  // ---- Datei-Explorer: Zielordner zum Verschieben per Klick wählen -----------------------
  function startMoveDialog(form, boxes, target) {
    var dlg = document.getElementById("move-dialog");
    var open = form.querySelector("[data-move-open]");
    var submit = form.querySelector("[data-move]");
    if (!dlg || !open || !submit || typeof dlg.showModal !== "function") return;
    var list = dlg.querySelector("[data-move-list]");
    var crumbs = dlg.querySelector("[data-move-crumbs]");
    var what = dlg.querySelector("[data-move-what]");
    var fresh = dlg.querySelector("[data-move-new]");
    var confirmBtn = dlg.querySelector("[data-move-confirm]");
    var destText = dlg.querySelector("[data-move-dest]");
    var here = dlg.dataset.dir;              // Ordner, in dem die Auswahl liegt
    var current = here;
    var selected = [];                       // volle Pfade der ausgewählten Einträge
    var loading = 0;

    function join(dir, name) { return (dir === "/" ? "" : dir) + "/" + name; }
    function parentOf(dir) { var i = dir.lastIndexOf("/"); return i <= 0 ? "/" : dir.slice(0, i); }
    function insideSelection(path) {
      return selected.some(function (s) { return path === s || path.indexOf(s + "/") === 0; });
    }
    function destination() {
      var name = fresh.value.trim();
      return name ? join(current, name) : current;
    }
    function updateConfirm() {
      var name = fresh.value.trim();
      var bad = name && (name === "." || name === ".." || /[\/]/.test(name));
      var dest = destination();
      confirmBtn.disabled = Boolean(bad) || dest === here || insideSelection(dest);
      destText.textContent = dest === here ? tr("Die Auswahl liegt schon in diesem Ordner")
        : bad ? tr("Ungültiger Ordnername") : tr("Ziel: {pfad}", { pfad: dest });
      destText.classList.toggle("faint", dest === here || Boolean(bad));
    }
    function item(label, cls, onClick, disabled) {
      var li = document.createElement("li");
      var b = document.createElement("button");
      b.type = "button";
      b.className = "move-item " + cls;
      b.textContent = label;
      b.disabled = Boolean(disabled);
      if (!disabled) b.addEventListener("click", onClick);
      li.appendChild(b);
      list.appendChild(li);
      return b;
    }
    function drawCrumbs() {
      crumbs.textContent = "";
      var parts = current.split("/").filter(Boolean);
      var path = "";
      var root = document.createElement("button");
      root.type = "button";
      root.textContent = tr("Hauptordner");
      root.addEventListener("click", function () { go("/"); });
      crumbs.appendChild(root);
      parts.forEach(function (p) {
        path += "/" + p;
        var sep = document.createElement("span");
        sep.textContent = "/";
        crumbs.appendChild(sep);
        var b = document.createElement("button");
        b.type = "button";
        b.textContent = p;
        var to = path;
        b.addEventListener("click", function () { go(to); });
        crumbs.appendChild(b);
      });
    }
    function go(dir) {
      current = dir;
      var ticket = ++loading;
      drawCrumbs();
      updateConfirm();
      list.textContent = "";
      var wait = document.createElement("li");
      wait.className = "faint small";
      wait.textContent = tr("Lädt …");
      list.appendChild(wait);
      fetch(dlg.dataset.folders + "?dir=" + encodeURIComponent(dir), { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) {
          if (r.status === 401) { window.location = "/login"; throw new Error("login"); }
          return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || tr("Fehler {code}", { code: r.status })); return d; });
        })
        .then(function (data) {
          if (ticket !== loading) return;
          list.textContent = "";
          if (dir !== "/") item(tr("Übergeordneter Ordner"), "move-up", function () { go(parentOf(dir)); });
          data.folders.forEach(function (name) {
            var path = join(dir, name);
            var mine = insideSelection(path);
            var b = item(name, "move-folder", function () { go(path); }, mine);
            if (mine) b.title = tr("Ausgewählt – kann nicht Ziel sein");
          });
          if (!data.folders.length) {
            var none = document.createElement("li");
            none.className = "faint small";
            none.textContent = tr("Keine Unterordner");
            list.appendChild(none);
          }
        })
        .catch(function (e) {
          if (ticket !== loading) return;
          list.textContent = "";
          var err = document.createElement("li");
          err.className = "bad small";
          err.textContent = e.message;
          list.appendChild(err);
        });
    }
    open.addEventListener("click", function () {
      selected = [];
      boxes.forEach(function (b) { if (b.checked) selected.push(join(here, b.value)); });
      if (!selected.length) return;
      what.textContent = selected.length === 1
        ? tr("„{name}“ verschieben – Zielordner anklicken, dann bestätigen.", { name: selected[0].split("/").pop() })
        : tr("{n} Einträge verschieben – Zielordner anklicken, dann bestätigen.", { n: selected.length });
      fresh.value = "";
      dlg.showModal();
      go(here);
    });
    fresh.addEventListener("input", updateConfirm);
    fresh.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); if (!confirmBtn.disabled) confirmBtn.click(); }
    });
    dlg.querySelector("[data-move-cancel]").addEventListener("click", function () { dlg.close(); });
    confirmBtn.addEventListener("click", function () {
      target.value = destination();
      dlg.close();
      if (form.requestSubmit) form.requestSubmit(submit); else submit.click();
    });
  }

  // ---- Ansicht: Kacheln / Liste (pro Browser gemerkt) ---------------------------
  function startViewToggle() {
    var grid = document.getElementById("server-grid");
    var buttons = document.querySelectorAll("[data-view]");
    if (!grid || !buttons.length) return;
    function apply(view) {
      grid.classList.toggle("list-view", view === "list");
      buttons.forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.view === view)); });
    }
    var saved = "list";
    try { saved = localStorage.getItem("hatchtower.view") || "list"; } catch (e) { /* ohne Speicher: Liste */ }
    apply(saved);
    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        apply(b.dataset.view);
        try { localStorage.setItem("hatchtower.view", b.dataset.view); } catch (e) { /* Speicher nicht verfügbar (z. B. privater Modus) */ }
      });
    });
  }

  // ---- Rechte: Abhängigkeiten ------------------------------------------------
  // Wer X anhakt, bekommt die nötigen Rechte automatisch dazu; wer Y entfernt, verliert alles, was Y braucht.
  function bindDeps(container, deps, labels, note, onChange) {
    var dependents = {};
    Object.keys(deps).forEach(function (key) {
      deps[key].forEach(function (d) { (dependents[d] = dependents[d] || []).push(key); });
    });
    function box(key) { return container.querySelector('input[data-key="' + key + '"]'); }
    function setOn(key, changed) {
      var cb = box(key);
      if (!cb) return;
      if (!cb.checked) { cb.checked = true; changed.push(labels[key]); }
      (deps[key] || []).forEach(function (d) { setOn(d, changed); });
    }
    function setOff(key, changed) {
      var cb = box(key);
      if (!cb) return;
      if (cb.checked) { cb.checked = false; changed.push(labels[key]); }
      (dependents[key] || []).forEach(function (d) { setOff(d, changed); });
    }
    container.addEventListener("change", function (ev) {
      var cb = ev.target;
      if (!cb.matches("input[data-key]")) return;
      var changed = [];
      if (cb.checked) (deps[cb.dataset.key] || []).forEach(function (d) { setOn(d, changed); });
      else (dependents[cb.dataset.key] || []).forEach(function (d) { setOff(d, changed); });
      if (note) {
        note.textContent = changed.length
          ? tr(cb.checked ? "Automatisch mitgesetzt: {list} (gebraucht für „{name}“)." : "Automatisch mitentfernt: {list} (braucht „{name}“).",
               { list: changed.join(", "), name: labels[cb.dataset.key] })
          : "";
      }
      if (onChange) onChange();
    });
  }

  function chipsFor(keys, labels, order) {
    var frag = document.createDocumentFragment();
    order.forEach(function (k) {
      if (keys.indexOf(k) === -1) return;
      var s = document.createElement("span");
      s.className = "pchip";
      s.textContent = labels[k];
      frag.appendChild(s);
    });
    return frag;
  }

  // ---- Rechte eines Benutzers ---------------------------------------------------
  function startPermissions() {
    var form = document.getElementById("perm-form");
    if (!form) return;
    var deps = JSON.parse(form.dataset.deps);
    var labels = JSON.parse(form.dataset.labels);
    var groups = JSON.parse(form.dataset.groups);
    // Reihenfolge wie in der Rechteliste (tojson sortiert die Schlüssel alphabetisch)
    var order = form.dataset.order ? JSON.parse(form.dataset.order) : Object.keys(labels);
    var user = form.dataset.user;
    var note = document.getElementById("dep-note");
    var summaryBox = document.getElementById("summary-box");
    var rows = form.querySelectorAll(".access-row");

    var defaultSel = document.getElementById("default-group");
    var defaultWarn = document.getElementById("default-warn");

    function rowKeys(row) {
      var mode = row.querySelector("[data-mode]").value;
      if (mode === "auto") return defaultSel && defaultSel.value ? (groups[defaultSel.value] || []) : [];
      if (mode.indexOf("g:") === 0) return groups[mode.slice(2)] || [];
      if (mode === "custom") {
        return Array.prototype.filter.call(row.querySelectorAll("input[data-key]"), function (c) { return c.checked; })
          .map(function (c) { return c.dataset.key; });
      }
      return [];
    }

    function refresh() {
      var parts = [];
      rows.forEach(function (row) {
        var mode = row.querySelector("[data-mode]").value;
        var custom = row.querySelector("[data-custom]");
        custom.hidden = mode !== "custom";
        var keys = rowKeys(row);
        var chips = row.querySelector("[data-chips]");
        chips.textContent = "";
        if (keys.indexOf("server.view") === -1) {
          var none = document.createElement("span");
          none.className = "faint small";
          none.textContent = mode === "custom" ? tr("Ohne „Sehen“ bleibt der Server unsichtbar") : tr("unsichtbar");
          chips.appendChild(none);
          row.classList.add("is-off");
        } else {
          chips.appendChild(chipsFor(keys, labels, order));
          row.classList.remove("is-off");
          var sel = row.querySelector("[data-mode]");
          var how = sel.options[sel.selectedIndex].text;
          if (mode === "auto") how = defaultSel.options[defaultSel.selectedIndex].text;
          how = how.indexOf(":") > 0 ? how.replace(/^[^:]+:\s*/, "") : how.replace(" …", "");
          parts.push(row.dataset.name + " (" + how + ")");
        }
      });
      summaryBox.textContent = parts.length
        ? tr("{user} sieht {n} von {total} Servern: {list}.", { user: user, n: parts.length, total: rows.length, list: parts.join(", ") })
        : tr("{user} sieht noch keinen Server.", { user: user });
    }

    rows.forEach(function (row) {
      bindDeps(row.querySelector("[data-custom]"), deps, labels, note, refresh);
      row.querySelector("[data-mode]").addEventListener("change", function () { note.textContent = ""; refresh(); });
    });

    if (defaultSel) {
      var syncDefault = function () {
        var opt = defaultSel.options[defaultSel.selectedIndex];
        if (defaultWarn) defaultWarn.hidden = !(opt && opt.dataset.write === "1");
        refresh();
      };
      defaultSel.addEventListener("change", syncDefault);
      syncDefault();
    }

    var bulk = document.getElementById("bulk-apply");
    if (bulk) {
      bulk.addEventListener("click", function () {
        var value = document.getElementById("bulk-mode").value;
        if (!value) return;
        rows.forEach(function (row) { row.querySelector("[data-mode]").value = value; });
        refresh();
      });
    }
    refresh();
  }

  // ---- Gruppen-Editor --------------------------------------------------------------
  function startGroupEditor() {
    var form = document.querySelector("form.perm-set");
    if (!form) return;
    bindDeps(form, JSON.parse(form.dataset.deps), JSON.parse(form.dataset.labels), form.querySelector(".dep-note"));
  }
  // ---- Ankündigungen ausblenden (pro Browser gemerkt) ------------------------------
  function startAnnouncements() {
    document.querySelectorAll("[data-ann]").forEach(function (box) {
      var key = "hatchtower.ann." + box.dataset.ann;
      try { if (localStorage.getItem(key)) { box.remove(); return; } } catch (e) { /* ohne Speicher: zeigen */ }
      var btn = box.querySelector("[data-ann-close]");
      if (btn) btn.addEventListener("click", function () {
        try { localStorage.setItem(key, "1"); } catch (e) { /* Speicher nicht verfügbar (z. B. privater Modus) */ }
        box.remove();
      });
    });
  }

  // ---- Favoriten -----------------------------------------------------------------------
  function startFavorites() {
    document.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-fav]");
      if (!btn) return;
      postJson("/api/servers/" + btn.dataset.fav + "/favorite", {})
        .then(function (data) {
          document.querySelectorAll('[data-fav="' + btn.dataset.fav + '"]').forEach(function (b) {
            b.setAttribute("aria-pressed", String(data.favorite));
          });
          var card = btn.closest(".server-card");
          if (card) card.classList.toggle("is-favorite", data.favorite);
          if (window.hatchtowerSort) window.hatchtowerSort();
          toast(data.favorite ? tr("Als Favorit angeheftet") : tr("Favorit entfernt"), true);
        })
        .catch(function (e) { toast(e.message, false); });
    });
  }

  // ---- Mehrere Server auf einmal steuern -------------------------------------------------
  var BULK_CONFIRM = {
    start: "{n} Server starten?", stop: "{n} Server stoppen?", restart: "{n} Server neu starten?",
  };
  function startBulk() {
    var bar = document.getElementById("bulk-bar");
    if (!bar) return;
    var count = document.getElementById("bulk-count");
    var all = document.getElementById("bulk-all");
    function boxes() { return Array.prototype.slice.call(document.querySelectorAll("input[data-bulk]")); }
    // „Alle auswählen“ bezieht sich auf die Server, die Status- und Tag-Filter gerade zeigen
    function visibleBoxes() {
      return boxes().filter(function (b) { return !b.closest(".server-card").classList.contains("filtered-out"); });
    }
    function update() {
      var chosen = boxes().filter(function (b) { return b.checked; });
      bar.hidden = chosen.length === 0;
      count.textContent = tr("{n} ausgewählt", { n: chosen.length });
      boxes().forEach(function (b) { b.closest(".server-card").classList.toggle("is-selected", b.checked); });
      if (all) {
        var shown = visibleBoxes();
        var on = shown.filter(function (b) { return b.checked; }).length;
        all.disabled = shown.length === 0;
        all.checked = shown.length > 0 && on === shown.length;
        all.indeterminate = on > 0 && on < shown.length;
      }
    }
    document.addEventListener("change", function (ev) { if (ev.target.matches("input[data-bulk]")) update(); });
    document.addEventListener("hatchtower:filtered", update);
    if (all) {
      all.addEventListener("change", function () {
        var target = all.checked;
        visibleBoxes().forEach(function (b) { b.checked = target; });
        update();
      });
    }
    document.getElementById("bulk-clear").addEventListener("click", function () {
      boxes().forEach(function (b) { b.checked = false; });
      update();
    });
    bar.querySelectorAll("[data-bulk-action]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var signal = btn.dataset.bulkAction;
        var chosen = boxes().filter(function (b) { return b.checked; });
        var allowed = chosen.filter(function (b) { return (b.dataset.can || "").split(" ").indexOf(signal) !== -1; });
        if (!allowed.length) { toast(tr("Für keinen der ausgewählten Server erlaubt."), false); return; }
        var names = allowed.map(function (b) { return b.closest(".server-card").dataset.name; });
        if (!window.confirm(tr(BULK_CONFIRM[signal], { n: allowed.length }) + "\n\n" + names.join("\n"))) return;
        bar.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
        var ok = 0, failed = [];
        var chain = Promise.resolve();
        allowed.forEach(function (b) {
          chain = chain.then(function () {
            return postJson("/api/servers/" + b.dataset.bulk + "/power", { signal: signal })
              .then(function () { ok++; })
              .catch(function (e) { failed.push(b.closest(".server-card").dataset.name + ": " + e.message); });
          });
        });
        chain.then(function () {
          bar.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
          if (ok) toast(tr("{n} Server: Befehl an {panel} gesendet.", { n: ok }), true);
          failed.forEach(function (f) { toast(f, false); });
          if (window.hatchtowerRefresh) {
            var n = 0;
            var fast = setInterval(function () { window.hatchtowerRefresh(); if (++n >= 45) clearInterval(fast); }, 1000);
          }
        });
      });
    });
    update();
  }

  // ---- Log-Archiv -----------------------------------------------------------------------
  function startLogArchive() {
    var sec = document.getElementById("log-archiv");
    var form = document.getElementById("log-filters");
    if (!sec || !form) return;
    var box = document.getElementById("log-lines");
    var more = document.getElementById("log-more");
    var dl = document.getElementById("log-download");
    var oldest = null;
    function params(extra) {
      var p = new URLSearchParams(new FormData(form));
      if (extra) Object.keys(extra).forEach(function (k) { p.set(k, extra[k]); });
      return p.toString();
    }
    function render(lines, append) {
      if (!append) box.textContent = "";
      var frag = document.createDocumentFragment();
      lines.slice().reverse().forEach(function (l) {
        var div = document.createElement("div");
        if (l.level) div.className = "log-" + l.level;
        var t = document.createElement("span");
        t.className = "log-time";
        t.textContent = l.t + "  ";
        div.appendChild(t);
        div.appendChild(document.createTextNode(l.text));
        frag.appendChild(div);
      });
      if (append) box.insertBefore(frag, box.firstChild); else box.appendChild(frag);
      if (!append) box.scrollTop = box.scrollHeight;
    }
    function load(append) {
      var extra = append && oldest ? { before: oldest } : null;
      fetch(sec.dataset.logs + "?" + params(extra), { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var lines = data.lines || [];
          if (!append && !lines.length) box.textContent = tr("Keine Zeilen gefunden.");
          else render(lines, append);
          if (lines.length) oldest = lines[lines.length - 1].id;
          more.hidden = lines.length < 300;
        })
        .catch(function () { box.textContent = tr("Archiv konnte nicht geladen werden."); });
      dl.href = sec.dataset.download + "?" + params();
    }
    form.addEventListener("submit", function (ev) { ev.preventDefault(); oldest = null; load(false); });
    form.querySelectorAll("select").forEach(function (s) { s.addEventListener("change", function () { oldest = null; load(false); }); });
    more.addEventListener("click", function () { load(true); });
    load(false);
  }

  // ---- Öffentliche Statusseite: alle 30 s aktualisieren --------------------------------------
  function startStatusPage() {
    var list = document.getElementById("status-list");
    if (!list) return;
    var stamp = document.getElementById("status-updated");
    function tick() {
      fetch(list.dataset.statusFeed, { headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          (data.servers || []).forEach(function (s) {
            var row = list.querySelector('[data-ident="' + s.identifier + '"]');
            if (!row) return;
            if (s.state) {
              row.className = "status-card state-" + s.state;
              var pill = row.querySelector('[data-f="state"]');
              if (pill) {
                pill.className = "pill state-" + s.state;
                pill.querySelector('[data-f="state_label"]').textContent = s.state_label;
              }
            }
            var started = row.querySelector('[data-f="started"]');
            if (started) started.textContent = s.started || "";
            var ingame = row.querySelector('[data-f="ingame"]');
            if (ingame && s.ingame) ingame.textContent = s.ingame;
          });
          if (stamp) stamp.textContent = tr("Stand {time}", { time: fmtClock(Date.now() / 1000) });
        })
        .catch(function () {});
    }
    setInterval(tick, 30000);
  }

  // ---- Passwörter auf der Statusseite: zusätzliche Rückfrage beim Einschalten ---------------
  function startPasswordOptIn() {
    var box = document.querySelector('input[name="field__password"]');
    if (!box) return;
    box.addEventListener("change", function () {
      if (box.checked && !window.confirm(tr("Passwörter werden für jeden im Internet sichtbar, der die Statusseite aufruft. Wirklich einschalten?"))) {
        box.checked = false;
      }
    });
  }

  // ---- Update aus der Oberfläche: Fortschritt verfolgen ------------------------------------
  function startUpdateProgress() {
    var box = document.getElementById("update-progress");
    if (!box) return;
    var statusEl = document.getElementById("update-status");
    var logEl = document.getElementById("update-log");
    var doneBtn = document.getElementById("update-done");
    var oldVersion = box.dataset.version;
    var sawRestart = false, finished = false, started = Date.now();
    function show(text, cls) {
      statusEl.textContent = text;
      statusEl.className = "update-status" + (cls ? " " + cls : "");
    }
    function finish(text, cls) {
      finished = true;
      var banner = document.getElementById(cls === "ok" ? "update-success" : "update-failed");
      if (banner) {
        // deutliche Meldung statt Statuszeile; bei Erfolg das Protokoll einklappen
        document.getElementById(cls === "ok" ? "update-success-text" : "update-failed-text").textContent = text;
        banner.hidden = false;
        statusEl.hidden = true;
        document.getElementById("update-hint").hidden = true;
        document.getElementById("update-heading").textContent =
          cls === "ok" ? tr("Update abgeschlossen") : tr("Update nicht abgeschlossen");
        if (cls === "ok") document.getElementById("update-log-box").open = false;
      } else {
        show(text, cls);
      }
      doneBtn.hidden = false;
    }
    function tick() {
      if (finished) return;
      fetch(box.dataset.status, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) {
          if (r.status === 401) { window.location = "/login"; throw new Error("login"); }
          if (!r.ok) throw new Error("http");
          return r.json();
        })
        .then(function (s) {
          if (s.log) { logEl.textContent = s.log; logEl.scrollTop = logEl.scrollHeight; }
          if (s.state === "failed") {
            finish(tr("Hatchtower läuft weiter mit der bisherigen Version – Details siehe Protokoll."), "bad");
          } else if (s.state === "interrupted") {
            finish(tr("Das Update wurde unterbrochen (z. B. durch einen Neustart). Hatchtower läuft mit Version {v} – Details siehe Protokoll.", { v: s.running_version }), "bad");
          } else if (s.state === "done" && s.running_version !== oldVersion) {
            finish(tr("Hatchtower läuft jetzt mit Version {v}.", { v: s.running_version }), "ok");
          } else if (s.state === "done" && sawRestart) {
            finish(tr("Hatchtower läuft mit Version {v}.", { v: s.running_version }), "ok");
          } else if (s.state === "requested") {
            show(tr("Update angefordert – startet in wenigen Sekunden …"));
          } else {
            show(tr("Update läuft …"));
          }
        })
        .catch(function (e) {
          if (e.message === "login") return;
          sawRestart = true;
          show(tr("Hatchtower startet neu …"));
        })
        .then(function () {
          if (!finished && Date.now() - started > 20 * 60 * 1000) {
            finish(tr("Das dauert ungewöhnlich lange. Bitte auf dem Server prüfen: journalctl -u hatchtower-update"), "bad");
          }
          if (!finished) setTimeout(tick, 3000);
        });
    }
    tick();
  }

  // ---- Backups: laufende Backups mit Fortschritt ----------------------------------------
  function startBackups() {
    document.querySelectorAll("form[data-backup-download]").forEach(function (f) {
      f.addEventListener("submit", function () {
        toast(tr("Backup startet, der Download beginnt gleich. Den Fortschritt siehst du unter Backups."), true);
      });
    });
    var form = document.getElementById("backup-job-form");
    if (form) {
      var target = document.getElementById("job-target");
      var schedule = document.getElementById("job-schedule");
      var hint = document.getElementById("job-schedule-hint");
      var warn = document.getElementById("job-warn");
      var save = document.getElementById("job-save");
      var sync = function () {
        var download = target.value === "";
        schedule.querySelectorAll("input").forEach(function (i) { i.disabled = download; });
        hint.hidden = !download;
        var stop = form.querySelector('input[name="stop_mode"]:checked');
        warn.hidden = !stop || stop.value === "none";
        // saving without a stop only: a stopping game writes its save anyway
        if (save) save.hidden = !!stop && stop.value !== "none";
      };
      form.addEventListener("change", sync);
      sync();
    }
    var tform = document.getElementById("target-form");
    if (tform) {
      var syncKind = function () {
        var kind = tform.querySelector('input[name="kind"]:checked').value;
        tform.querySelectorAll("[data-kind]").forEach(function (el) {
          el.hidden = el.dataset.kind !== kind;
          el.querySelectorAll("input, select").forEach(function (i) { i.disabled = el.hidden; });
        });
        ["smb_host", "smb_share"].forEach(function (n) { tform.elements[n].required = kind === "smb"; });
      };
      tform.addEventListener("change", syncKind);
      syncKind();
    }
    var rform = document.getElementById("restore-form");
    if (rform) {
      var confirmBox = rform.querySelector("[data-restore-confirm]");
      var syncMode = function () {
        var over = rform.querySelector('input[name="mode"]:checked').value === "overwrite";
        confirmBox.hidden = !over;
        confirmBox.querySelector("input").required = over;
      };
      rform.addEventListener("change", syncMode);
      syncMode();
    }
    startRunSelection();
    startJobBrowser();
    var box = document.getElementById("backup-active");
    if (!box) return;
    // Ergebnismeldung nur einmal zeigen: „done“ aus der Adresse nehmen, damit Neuladen sie nicht wiederholt
    var params = new URLSearchParams(window.location.search);
    if (params.has("done")) {
      params.delete("done");
      var rest = params.toString();
      history.replaceState(null, "", window.location.pathname + (rest ? "?" + rest : "") + window.location.hash);
    }
    var list = document.getElementById("backup-active-list");
    var empty = document.getElementById("backup-active-empty");
    var known = [];
    function el(tag, cls, text) {
      var e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text !== undefined) e.textContent = text;
      return e;
    }
    function render(active) {
      list.textContent = "";
      active.forEach(function (a) {
        var row = el("div", "backup-run");
        var head = el("div", "backup-run-head");
        var title = el("div", "backup-run-title");
        title.appendChild(el("strong", "", a.server));
        var kinds = { download: tr("Direktdownload"), restore: tr("Wiederherstellung"), verify: tr("Prüfung") };
        title.appendChild(el("span", "faint small", " · " + a.job + (kinds[a.kind] ? " · " + kinds[a.kind] : "")));
        head.appendChild(title);
        if (a.can_cancel && a.cancel_url) {
          var btn = el("button", "chip chip-sm chip-danger", tr("Abbrechen"));
          btn.type = "button";
          btn.dataset.cancel = a.cancel_url;
          btn.dataset.kind = a.kind;
          head.appendChild(btn);
        }
        row.appendChild(head);
        var bar = el("div", "bar backup-bar");
        var fill = el("div");
        bar.appendChild(fill);
        row.appendChild(bar);
        var withBar = ["copying", "uploading", "verifying"].indexOf(a.state) >= 0;
        paintBar(fill, withBar ? a.percent : 0);
        fill.classList.remove("warn", "crit");
        var info = a.phase;
        if (a.state === "copying" || a.state === "uploading") {
          info = a.percent + " % · " + fmtBytes(a.done) + " / " + fmtBytes(a.total) + " · " +
            tr("{n} von {m} Dateien", { n: a.files_done, m: a.files_total }) +
            (a.rate ? " · " + fmtBytes(a.rate, true) : "");
          if (a.throttled) info += " · " + tr("wartet kurz auf {panel} (Anfragen-Limit)");
        }
        row.appendChild(el("div", "faint small", info));
        list.appendChild(row);
      });
      empty.hidden = active.length > 0;
      // outside the jobs tab the box only shows while something runs
      if (box.hasAttribute("data-compact")) box.hidden = active.length === 0;
    }
    function tick() {
      fetch(box.dataset.status, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) {
          if (r.status === 401) { window.location = "/login"; throw new Error("login"); }
          if (!r.ok) throw new Error("http");
          return r.json();
        })
        .then(function (data) {
          var now = data.active.map(function (a) { return String(a.id); });
          // Ein Lauf ist fertig geworden: Seite neu laden, damit Tabellen und „Zuletzt“ stimmen,
          // und oben melden, wie er ausgegangen ist
          var finished = known.filter(function (id) { return now.indexOf(id) < 0; });
          if (finished.length) {
            window.location = window.location.pathname + "?done=" + encodeURIComponent(finished.join(","));
            return;
          }
          known = now;
          render(data.active);
        })
        .catch(function () { /* nächster Versuch */ })
        .then(function () { setTimeout(tick, 2000); });
    }
    list.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-cancel]");
      var question = btn && btn.dataset.kind === "restore"
        ? tr("Wiederherstellung abbrechen? Bis dahin zurückgespielte Dateien bleiben auf dem Server.")
        : tr("Dieses Backup abbrechen?");
      if (!btn || !window.confirm(question)) return;
      btn.disabled = true;
      postJson(btn.dataset.cancel, {})
        .then(function (d) { toast(d.message || tr("Wird abgebrochen …"), true); })
        .catch(function (e) { toast(e.message, false); btn.disabled = false; });
    });
    try {
      var initial = JSON.parse(document.getElementById("backup-active-data").textContent);
      known = initial.map(function (a) { return String(a.id); });
      render(initial);
    } catch (e) { /* leer */ }
    tick();
  }

  // ---- Höhe der Kopfzeile für mitlaufende Leisten (bricht je nach Breite um) ------------------
  function startTopbarHeight() {
    var bar = document.querySelector(".sidebar-top");
    if (!bar) return;
    // only the phone layout has a bar at the top; on wider screens the navigation is a sidebar
    var phone = window.matchMedia("(max-width: 900px)");
    function apply() { document.documentElement.style.setProperty("--topbar-h", (phone.matches ? bar.offsetHeight : 0) + "px"); }
    if (phone.addEventListener) phone.addEventListener("change", apply);
    if (window.ResizeObserver) new ResizeObserver(apply).observe(bar);
    apply();
  }

  // ---- Backups: mehrere gespeicherte Backups auswählen (löschen, prüfen) -------------------
  function startRunSelection() {
    var form = document.getElementById("runs-form");
    if (!form) return;
    var boxes = document.querySelectorAll('input[name="run"][form="runs-form"]');
    var all = document.getElementById("runs-all");
    var count = document.getElementById("runs-count");
    var del = form.querySelector('button[value="delete"]');
    var verify = form.querySelector('button[value="verify"]');
    function update() {
      var n = 0, canDelete = true, canVerify = true;
      boxes.forEach(function (b) {
        b.closest("tr").classList.toggle("is-selected", b.checked);
        if (!b.checked) return;
        n++;
        if (b.dataset.canDelete !== "1") canDelete = false;
        if (b.dataset.canVerify !== "1") canVerify = false;
      });
      form.hidden = n === 0;
      count.textContent = n === 1 ? tr("1 Backup ausgewählt") : tr("{n} Backups ausgewählt", { n: n });
      // Eine Aktion geht nur, wenn sie für alle ausgewählten Backups erlaubt ist
      if (del) { del.disabled = !canDelete; del.title = canDelete ? "" : tr("Nicht für alle ausgewählten Backups möglich"); }
      if (verify) { verify.disabled = !canVerify; verify.title = canVerify ? "" : tr("Nicht für alle ausgewählten Backups möglich"); }
      if (all) {
        all.checked = n > 0 && n === boxes.length;
        all.indeterminate = n > 0 && n < boxes.length;
      }
    }
    boxes.forEach(function (b) { b.addEventListener("change", update); });
    if (all) {
      all.addEventListener("change", function () {
        boxes.forEach(function (b) { b.checked = all.checked; });
        update();
      });
    }
    document.getElementById("runs-clear").addEventListener("click", function () {
      boxes.forEach(function (b) { b.checked = false; });
      update();
    });
    form.addEventListener("submit", function (ev) {
      if (ev.submitter && ev.submitter.value === "delete" && !window.confirm(form.dataset.confirmDelete)) ev.preventDefault();
    });
    update();
  }

  // ---- Backup job: tick files and folders in the file browser ----------------------------
  function startJobBrowser() {
    var form = document.getElementById("job-browse-form");
    if (!form) return;
    var boxes = document.querySelectorAll('input[name="names"][form="job-browse-form"]');
    var all = document.getElementById("job-browse-all");
    var count = document.getElementById("job-browse-count");
    var add = document.getElementById("job-browse-add");
    function update() {
      var n = 0;
      boxes.forEach(function (b) {
        b.closest("tr").classList.toggle("is-selected", b.checked);
        if (b.checked) n++;
      });
      count.textContent = tr("{n} ausgewählt", { n: n });
      add.disabled = n === 0;
      if (all) {
        all.checked = n > 0 && n === boxes.length;
        all.indeterminate = n > 0 && n < boxes.length;
        all.disabled = boxes.length === 0;
      }
    }
    boxes.forEach(function (b) { b.addEventListener("change", update); });
    if (all) {
      all.addEventListener("change", function () {
        boxes.forEach(function (b) { b.checked = all.checked; });
        update();
      });
    }
    update();
  }

  // ---- Felder zum Kopieren (z. B. Einladungslink) ----------------------------------------
  function startCopyFields() {
    document.querySelectorAll("[data-select-on-focus]").forEach(function (f) {
      f.addEventListener("focus", function () { f.select(); });
    });
  }

  // ---- Benutzerliste: Suche über Name, E-Mail-Adresse und Notiz ---------------------------
  function startUserFilter() {
    var input = document.getElementById("user-filter");
    if (!input) return;
    var empty = document.getElementById("user-filter-empty");
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      var any = false;
      document.querySelectorAll("[data-user-section]").forEach(function (sec) {
        var shown = 0;
        sec.querySelectorAll("[data-user-row]").forEach(function (row) {
          var hit = !q || row.dataset.userRow.indexOf(q) !== -1;
          row.classList.toggle("filtered-out", !hit);
          if (hit) shown++;
        });
        sec.classList.toggle("filtered-out", shown === 0);
        if (shown) any = true;
      });
      if (empty) empty.hidden = any;
    });
  }

  // ---- Einrichtung: Panel-Auswahl ------------------------------------------------
  // Zeigt zur gewählten Panel-Art die passende Anleitung und Beschriftung des Key-Felds.
  var SINGLE_KEY_PANELS = ["calagopus"];
  // New backup job: "all servers" makes the server list and the scope choice moot
  function startBackupJobNew() {
    var all = document.querySelector("[data-job-all]");
    if (!all) return;
    var parts = document.querySelectorAll("[data-job-servers] input, [data-job-scope] input");
    function sync() { parts.forEach(function (el) { el.disabled = all.checked; }); }
    all.addEventListener("change", sync);
    sync();
  }

  function startPanelChoice() {
    document.querySelectorAll("[data-panel-form]").forEach(function (form) {
      function update() {
        var picked = form.querySelector("[data-panel-choice]:checked");
        if (!picked) return;
        var single = SINGLE_KEY_PANELS.indexOf(picked.value) >= 0;
        form.querySelectorAll("[data-panel-howto]").forEach(function (el) { el.hidden = el.dataset.panelHowto !== picked.value; });
        form.querySelectorAll("[data-panel-label]").forEach(function (el) { el.hidden = (el.dataset.panelLabel === "single") !== single; });
      }
      form.querySelectorAll("[data-panel-choice]").forEach(function (r) { r.addEventListener("change", update); });
      update();
    });
  }

  // Installable app on Android: Chrome wants a service worker (only over HTTPS or on localhost)
  function startServiceWorker() {
    if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
  }

  // Home-screen app on iPad/iPhone (also when added from Chrome, which uses WebKit there): focusing the password
  // field brings up the AutoFill bar and WebKit scrolls the page, leaving it shifted although the short sign-in
  // pages fit on screen. Put it back, but not while the on-screen keyboard is up (then the pan reveals the field).
  function startStandaloneLoginFix() {
    if (!document.body.classList.contains("login-page")) return;
    var standalone = navigator.standalone || (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);
    if (!standalone) return;
    var vv = window.visualViewport;
    function settle() {
      // clientHeight, not innerHeight: WebKit shrinks innerHeight along with its imagined keyboard
      var root = document.scrollingElement || document.documentElement;
      var height = document.documentElement.clientHeight;
      if (root.scrollHeight > height + 1) return;
      // A real on-screen keyboard shrinks the visible area; with a hardware keyboard WebKit (iPadOS) reports a
      // height of zero or less for the password field and scrolls into nothing
      if (vv && vv.height > 0 && vv.height < height - 150) return;
      if (window.scrollY || (vv && vv.offsetTop)) window.scrollTo(0, 0);
    }
    window.addEventListener("scroll", settle, { passive: true });
    if (vv) { vv.addEventListener("resize", settle); vv.addEventListener("scroll", settle); }
    // Typing fast, WebKit scrolls again between events, so check every frame while a field has the focus
    var watching = false;
    function watch() {
      settle();
      if (document.activeElement && document.activeElement.matches("input")) requestAnimationFrame(watch);
      else watching = false;
    }
    document.addEventListener("focusin", function () {
      if (!watching) { watching = true; requestAnimationFrame(watch); }
    });
    document.addEventListener("focusout", function () { setTimeout(settle, 100); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    startServiceWorker();
    startStandaloneLoginFix();
    startAnnouncements();
    startFavorites();
    startBulk();
    startLogArchive();
    startStatusPage();
    startCopyFields();
    startPasswordOptIn();
    startUpdateProgress();
    startBackups();
    paintBars(document);
    document.querySelectorAll("[data-c]").forEach(function (el) { el.style.background = el.dataset.c; });
    startMenu();
    startAccountMenu();
    startThemeSwitch();
    startServerTabs();
    startPresenceTab();
    checkDesktopMode();
    startViewToggle();
    startStatusFilter();
    startUserFilter();
    startTopbarHeight();
    startSort();
    startFileSelection();
    startFileSearch();
    startCharts();
    startControls();
    startLive();
    startConsole();
    startPermissions();
    startGroupEditor();
    startPanelChoice();
    startBackupJobNew();
  });
})();
