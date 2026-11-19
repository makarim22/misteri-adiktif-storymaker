"use strict";
// Story Studio front end. No frameworks. All user text is inserted via textContent
// (never innerHTML), so stored content cannot inject markup.

const S = { user: null, statuses: [], users: [], leave: null };
const app = document.getElementById("app");

// ---------- tiny helpers ----------
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "class") e.className = v;
    else if (k === "value") e.value = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return e;
}

// Replace an element's children; accepts nested arrays (e.g. from .map()) and skips null/false.
function fill(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch("/api" + path, opts);
  let data = {};
  try { data = await r.json(); } catch (_) {}
  if (r.status === 401 && path !== "/login") { S.user = null; render(); throw new Error("Signed out"); }
  if (!r.ok) {
    const msg = data.code === "conflict" ? t("conflict", { author: data.author || t("someone") }) : errText(data.error || r.statusText);
    const e = new Error(msg); e.status = r.status; e.data = data; throw e;
  }
  return data;
}

let toastTimer;
function toast(msg) {
  let t = document.querySelector(".toast");
  if (!t) { t = h("div", { class: "toast" }); document.body.append(t); }
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

const fmtDate = (ts) => new Date(ts * 1000).toLocaleString(dateLocale(), { dateStyle: "medium", timeStyle: "short" });
const words = (s) => (s.trim() ? s.trim().split(/\s+/).length : 0);
const todayStr = () => new Date().toISOString().slice(0, 10);
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

function fail(e) { if (e.message !== "Signed out") toast(e.message); }

// Monochrome initials: colour is reserved for stage and for editorial marks.
function avatar(name) {
  const n = name || "?";
  const ini = n.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
  return h("span", { class: "av", title: n }, ini);
}

const shortDate = (d) => new Date(d + "T00:00:00").toLocaleDateString(dateLocale(), { month: "short", day: "numeric" });
// Language and theme switches. Choices are stored in this browser only.
function setPref(key, value) { localStorage.setItem("ss." + key, value); applyPrefs(); render(); }
function prefsBar() {
  const seg = (label, key, current, options) => h("div", { class: "seg", role: "group", "aria-label": label },
    options.map(([val, text]) => h("button", { type: "button", "aria-pressed": String(current === val), onclick: () => setPref(key, val) }, text)));
  return h("div", { class: "prefs" },
    seg(t("pref.lang"), "lang", PREFS.lang, [["id", "ID"], ["en", "EN"]]),
    seg(t("pref.theme"), "theme", PREFS.theme, [["light", t("theme.light")], ["dark", t("theme.dark")], ["system", t("theme.system")]]));
}
const stageName = (s) => t("stage." + s);
const stageColor = (s) => `var(--s-${s})`;
function pageHead(title, sub, ...actions) {
  return h("div", { class: "page-h" }, h("div", {}, h("h1", {}, title), sub && h("p", { class: "standfirst" }, sub)), actions.length ? h("div", {}, actions) : null);
}

// ---------- running order: sections of a script ----------
// A section starts at a line that opens with an ALL-CAPS label (PEMBUKA, KETEGANGAN, OUTRO / CTA),
// optionally followed by " - note" or "(0:00-0:30)". [bracketed notes] are not spoken, so they are not counted.
// Indonesian and English opening headings are both recognised for the long-opening warning.
const HEADING = /^([A-Z][A-Z0-9 \/&']*[A-Z0-9])(?=\s*(?:[-\u2013(:]|$))/;
const OPENING = /^(PEMBUKA|HOOK)\b/;
function parseSections(text, wpm) {
  const out = []; let pos = 0, cur = null;
  for (const line of text.split("\n")) {
    const m = HEADING.exec(line);
    if (m && m[1].length >= 3) { cur = { label: m[1], pos, words: 0 }; out.push(cur); }
    else if (cur) cur.words += words(line.replace(/\[[^\]]*\]/g, " "));
    pos += line.length + 1;
  }
  return out.map((s) => ({ ...s, secs: Math.round((s.words / wpm) * 60) }));
}
const fmtSecs = (s) => (s >= 60 ? t("t.ms", { m: Math.floor(s / 60), s: String(s % 60).padStart(2, "0") }) : t("t.s", { s }));

// ---------- routing ----------
async function render() {
  if (S.leave) { const f = S.leave; S.leave = null; try { await f(); } catch (_) {} }
  app.replaceChildren();
  if (!S.user) return loginView();
  const route = location.hash.slice(1) || "/";
  const on = (href) => (href === "/" ? route === "/" || route.startsWith("/video") : route.startsWith(href));
  const nav = (href, label) => h("a", { href: "#" + href, class: on(href) ? "on" : "" }, label);
  const main = h("main", { class: "content" });
  app.append(
    h("header", { class: "top" },
      h("a", { class: "wordmark", href: "#/" }, "Story Studio"),
      h("nav", { class: "nav" }, nav("/", t("nav.board")), nav("/calendar", t("nav.calendar")), nav("/library", t("nav.library")), nav("/settings", t("nav.settings"))),
      h("div", { class: "sp" }),
      prefsBar(),
      h("div", { class: "me" }, avatar(S.user.display_name), h("span", { class: "nm" }, S.user.display_name),
        h("button", { class: "linkbtn", onclick: async () => { await api("POST", "/logout", {}); S.user = null; render(); } }, t("signout")))),
    main);
  try {
    S.users = await api("GET", "/users");
    let m;
    if ((m = route.match(/^\/video\/(\d+)$/))) await videoView(main, +m[1]);
    else if (route === "/calendar") await calendarView(main);
    else if (route === "/library") await libraryView(main);
    else if (route === "/settings") await settingsView(main);
    else await boardView(main);
  } catch (e) { fail(e); main.append(h("p", { class: "err" }, e.message)); }
}
window.addEventListener("hashchange", render);

// ---------- login / setup ----------
async function loginView() {
  const st = await api("GET", "/state");
  if (st.user) { S.user = st.user; S.statuses = st.statuses; return render(); }
  S.statuses = st.statuses;
  const setup = st.needs_setup;
  const err = h("p", { class: "err" });
  const f = {
    name: h("input", { type: "text", placeholder: t("field.name"), "aria-label": t("field.name") }),
    user: h("input", { type: "text", placeholder: t("ph.username"), "aria-label": t("field.username"), autocomplete: "username" }),
    pw: h("input", { type: "password", placeholder: t("ph.password"), "aria-label": t("field.password"), autocomplete: setup ? "new-password" : "current-password" }),
  };
  const submit = async () => {
    err.textContent = "";
    try {
      const r = await api("POST", setup ? "/setup" : "/login",
        { username: f.user.value, password: f.pw.value, display_name: f.name.value });
      S.user = r.user; location.hash = "#/"; render();
    } catch (e) { err.textContent = e.message; }
  };
  [f.user, f.pw, f.name].forEach((i) => i.addEventListener("keydown", (e) => e.key === "Enter" && submit()));
  app.replaceChildren(h("div", { class: "auth" }, h("div", { class: "sheet" },
    h("span", { class: "wordmark" }, "Story Studio"),
    h("h1", {}, setup ? t("login.setup") : t("login.title")),
    h("p", { class: "muted" }, setup ? t("login.setup.sub") : t("login.sub")),
    setup && [h("label", {}, t("field.name")), f.name], h("label", {}, t("field.username")), f.user, h("label", {}, t("field.password")), f.pw,
    err, h("div", { class: "bar" }, h("button", { class: "btn", style: "width:100%", onclick: submit }, setup ? t("btn.create") : t("btn.login"))),
    h("div", { class: "auth-prefs" }, prefsBar()))));
}

// ---------- board ----------
async function boardView(main) {
  let videos = await api("GET", "/videos");
  const title = h("input", { type: "text", placeholder: t("board.newph"), "aria-label": t("board.newaria") });
  const add = async () => {
    if (!title.value.trim()) return;
    try { const r = await api("POST", "/videos", { title: title.value }); location.hash = "#/video/" + r.id; } catch (e) { fail(e); }
  };
  title.addEventListener("keydown", (e) => e.key === "Enter" && add());
  const board = h("div", { class: "board" });
  const draw = () => {
    board.replaceChildren(...S.statuses.map((st) => {
      const items = videos.filter((v) => v.status === st);
      const col = h("div", { class: "col", style: "--c:" + stageColor(st) },
        h("h3", {}, h("span", {}, stageName(st)), h("span", { class: "n" }, items.length)));
      col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("over"); });
      col.addEventListener("dragleave", () => col.classList.remove("over"));
      col.addEventListener("drop", async (e) => {
        e.preventDefault(); col.classList.remove("over");
        const id = +e.dataTransfer.getData("text/plain");
        const v = videos.find((x) => x.id === id);
        if (!v || v.status === st) return;
        const old = v.status; v.status = st; draw();
        try { await api("PATCH", "/videos/" + id, { status: st }); } catch (er) { v.status = old; draw(); fail(er); }
      });
      items.forEach((v) => {
        const late = v.deadline && v.deadline < todayStr() && v.status !== "published";
        const card = h("a", { class: "vcard", href: "#/video/" + v.id, draggable: "true" },
          h("div", { class: "t" }, v.title),
          h("div", { class: "m" },
            v.owner_name ? avatar(v.owner_name) : h("span", {}, t("board.noowner")),
            h("span", { class: "sp" }),
            v.deadline ? h("span", { class: "due" + (late ? " late" : "") }, t(late ? "board.overdue" : "board.due", { d: shortDate(v.deadline) })) : null));
        card.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", String(v.id)); card.classList.add("dragging"); });
        card.addEventListener("dragend", () => card.classList.remove("dragging"));
        col.append(card);
      });
      if (!items.length) col.append(h("div", { class: "empty-col" }, t("board.empty")));
      return col;
    }));
  };
  draw();
  main.append(pageHead(t("board.title"), videos.length ? t("board.sub") : t("board.sub.empty"),
    h("div", { class: "newvideo" }, title, h("button", { class: "btn", onclick: add }, t("board.new")))), board);
}

// ---------- video / editor ----------
async function videoView(main, id) {
  const [video, stages, settings] = await Promise.all([api("GET", "/videos/" + id), api("GET", "/ai/stages"), api("GET", "/settings")]);
  let rev = video.rev, dirty = false, saving = false, blocked = false, timer = null;

  const banner = h("div", { class: "banner", style: "display:none" });
  const title = h("input", { type: "text", value: video.title, class: "title-in", "aria-label": t("field.title") });
  const status = h("select", {}, S.statuses.map((s) => h("option", { value: s }, stageName(s))));
  status.value = video.status;
  const stageDot = h("span", { class: "dot", style: "--c:" + stageColor(video.status) });
  const owner = h("select", {}, h("option", { value: "" }, t("unassigned")), S.users.map((u) => h("option", { value: u.id }, u.display_name)));
  owner.value = video.owner_id ?? "";
  const deadline = h("input", { type: "date", value: video.deadline || "" });
  const patch = (obj) => api("PATCH", "/videos/" + id, obj).then(() => toast(t("toast.saved"))).catch(fail);
  title.addEventListener("change", () => patch({ title: title.value }));
  status.addEventListener("change", () => { stageDot.style.setProperty("--c", stageColor(status.value)); patch({ status: status.value }); });
  owner.addEventListener("change", () => patch({ owner_id: owner.value ? +owner.value : null }));
  deadline.addEventListener("change", () => patch({ deadline: deadline.value }));

  const script = h("textarea", { class: "script", "aria-label": t("script.aria"), spellcheck: "true", placeholder: t("script.ph") });
  script.value = video.content;
  const stat = h("span", { class: "muted" });
  setTimeout(() => setSave(t("save.clean")));
  const saveState = h("span", { class: "savestate" });
  const setSave = (txt, cls) => { saveState.textContent = txt; saveState.className = "savestate" + (cls ? " " + cls : ""); };
  const wpm = settings.wpm || 150;
  const updateStat = () => {
    const w = words(script.value);
    stat.textContent = t("stat", { w: w.toLocaleString(dateLocale()), m: (w / wpm).toLocaleString(dateLocale(), { minimumFractionDigits: 1, maximumFractionDigits: 1 }) });
  };
  updateStat();

  // running-order strip
  const order = h("nav", { class: "order", "aria-label": t("order.title") });
  function jumpTo(pos) {
    const probe = script.cloneNode(false);
    probe.className = "script"; probe.removeAttribute("aria-label");
    probe.style.cssText = `position:absolute;visibility:hidden;height:0;min-height:0;width:${script.clientWidth}px;left:-9999px`;
    probe.value = script.value.slice(0, pos); script.parentNode.append(probe);
    const y = probe.scrollHeight; probe.remove();
    script.focus({ preventScroll: true }); script.setSelectionRange(pos, pos);
    script.scrollTop = Math.max(0, y - 80);
  }
  function drawOrder() {
    const secs = parseSections(script.value, wpm);
    const total = secs.reduce((n, s) => n + s.secs, 0);
    if (!secs.length) {
      fill(order, h("h2", {}, t("order.title")), h("p", { class: "muted" }, t("order.hint")));
      return;
    }
    const maxW = Math.max(...secs.map((s) => s.words), 1);
    fill(order, h("h2", {}, t("order.title")),
      h("p", { class: "muted order-total" }, t("order.about", { t: fmtSecs(total) })),
      h("ol", {}, secs.map((s) => {
        const longHook = OPENING.test(s.label) && s.secs > 30;
        return h("li", {}, h("button", { class: "ord" + (longHook ? " warn" : ""), style: `--w:${Math.max(6, Math.round((s.words / maxW) * 100))}%`,
          onclick: () => jumpTo(s.pos), title: longHook ? t("order.longtip") : t("order.jump") },
          h("span", { class: "ord-name" }, cap(s.label.toLowerCase()).replace(/\bcta\b/g, "CTA")),
          h("span", { class: "ord-len" }, fmtSecs(s.secs) + (longHook ? t("order.longnote") : "")),
          h("span", { class: "ord-bar", "aria-hidden": "true" })));
      })));
  }
  drawOrder();

  async function save(label) {
    if (blocked || saving) return;
    saving = true; clearTimeout(timer); setSave(t("save.saving"), "dirty");
    try {
      const r = await api("PUT", `/videos/${id}/script`, { content: script.value, base_rev: rev, label: label || "" });
      rev = r.rev; dirty = false; setSave(t("save.saved", { time: new Date().toLocaleTimeString(dateLocale()) }));
      if (label) { toast(t("toast.versionSaved")); loadHistory(); }
    } catch (e) {
      if (e.status === 409) {
        blocked = true; banner.style.display = ""; banner.textContent = e.message;
        setSave(t("save.conflict"), "bad");
      } else { setSave(t("save.failed"), "bad"); fail(e); }
    } finally { saving = false; }
  }
  script.addEventListener("input", () => {
    dirty = true; updateStat(); drawOrder(); setSave(t("save.dirty"), "dirty");
    clearTimeout(timer); timer = setTimeout(() => save(), 2500);
  });
  const beforeUnload = (e) => { if (dirty || blocked) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", beforeUnload);
  S.leave = async () => {
    window.removeEventListener("beforeunload", beforeUnload);
    clearTimeout(timer);
    if (dirty && !blocked) await save();
  };

  // --- side panel ---
  const panel = h("div", { class: "tools" });
  const tabs = {};
  const tabBar = h("div", { class: "tabs" });
  const body = h("div", { class: "pb" });
  const histBox = h("div"), comBox = h("div"), aiBox = h("div"), notesBox = h("div");
  const show = (name) => {
    for (const [k, b] of Object.entries(tabs)) b.classList.toggle("on", k === name);
    body.replaceChildren({ ai: aiBox, comments: comBox, history: histBox, notes: notesBox }[name]);
  };
  [["ai", t("tab.ai")], ["comments", t("tab.comments")], ["history", t("tab.history")], ["notes", t("tab.notes")]].forEach(([k, label]) => {
    tabs[k] = h("button", { onclick: () => show(k) }, label); tabBar.append(tabs[k]);
  });
  panel.append(tabBar, body);

  // AI tab
  const providerOk = settings.providers[settings.provider].has_key;
  const stageSel = h("select", {}, stages.map((s) => h("option", { value: s.id }, t("ai." + s.id))));
  const aiInput = h("textarea", { rows: "6", placeholder: t("ai.ph"), "aria-label": t("ai.ph") });
  const hint = h("p", { class: "muted" });
  const out = h("div", { class: "out", style: "display:none" });
  const outBtns = h("div", { class: "bar", style: "display:none" });
  const verb = () => t("verb." + stageSel.value);
  const runBtn = h("button", { class: "btn" }, verb());
  const runsBox = h("div");
  let lastText = "";
  const syncStage = () => {
    const st = stages.find((s) => s.id === stageSel.value);
    aiInput.style.display = st.needs === "script" ? "none" : "";
    runBtn.textContent = verb();
    hint.textContent = st.needs === "script" ? t("ai.hint.script") : st.needs === "both" ? t("ai.hint.both") : "";
  };
  stageSel.addEventListener("change", syncStage); syncStage();
  const insertText = (txt, where) => {
    if (where === "cursor") { const a = script.selectionStart, b = script.selectionEnd; script.setRangeText(txt, a, b, "end"); }
    else script.value = script.value.replace(/\s*$/, "") + "\n\n" + txt + "\n";
    script.dispatchEvent(new Event("input")); toast(t("toast.inserted"));
  };
  outBtns.append(
    h("button", { class: "btn ghost sm", onclick: () => insertText(lastText, "end") }, t("ai.addEnd")),
    h("button", { class: "btn ghost sm", onclick: () => insertText(lastText, "cursor") }, t("ai.atCursor")),
    h("button", { class: "btn ghost sm", onclick: () => navigator.clipboard.writeText(lastText).then(() => toast(t("toast.copied"))) }, t("copy")));
  runBtn.addEventListener("click", async () => {
    runBtn.disabled = true; runBtn.replaceChildren(h("span", { class: "spin" }), t("ai.working"));
    try {
      const r = await api("POST", "/ai", { video_id: id, stage: stageSel.value, input: aiInput.value, script: script.value });
      lastText = r.text; out.textContent = r.text; out.style.display = ""; outBtns.style.display = ""; loadRuns();
    } catch (e) { fail(e); out.textContent = e.message; out.style.display = ""; outBtns.style.display = "none"; }
    finally { runBtn.disabled = false; runBtn.textContent = verb(); }
  });
  async function loadRuns() {
    const runs = await api("GET", `/videos/${id}/ai_runs`);
    fill(runsBox, h("h2", { style: "margin-top:28px" }, t("ai.earlier")),
      runs.length ? runs.map((r) => h("details", { class: "item" },
        h("summary", {}, t("ai." + r.stage), h("span", {}, r.author + ", " + fmtDate(r.created))),
        h("div", { class: "out" }, r.output),
        h("button", { class: "btn ghost sm", onclick: () => navigator.clipboard.writeText(r.output).then(() => toast(t("toast.copied"))) }, t("copy")))) : h("div", { class: "empty" }, t("ai.none")));
  }
  aiBox.append(!providerOk && h("p", { class: "err" }, t("ai.nokey", { p: settings.providers[settings.provider].label })),
    h("label", {}, t("ai.task")), stageSel, hint, aiInput, h("div", { class: "bar" }, runBtn), out, outBtns, runsBox);
  loadRuns().catch(fail);

  // Comments tab
  const comInput = h("textarea", { rows: "3", placeholder: t("com.ph"), "aria-label": t("com.ph") });
  async function loadComments() {
    const cs = await api("GET", `/videos/${id}/comments`);
    fill(comList, cs.length ? cs.map((c) => h("div", { class: "item" },
      h("div", { class: "h" }, h("span", { class: "who" }, avatar(c.author), h("strong", {}, c.author)),
        h("span", {}, fmtDate(c.created), " ",
          (c.user_id === S.user.id || S.user.is_admin) && h("a", { href: "#", onclick: async (e) => { e.preventDefault(); await api("DELETE", "/comments/" + c.id).catch(fail); loadComments(); } }, t("delete")))),
      h("div", { class: "b" }, c.body))) : h("div", { class: "empty" }, t("com.none")));
  }
  const comList = h("div");
  comBox.append(comList, h("div", { class: "bar" }, h("button", { class: "btn sm", onclick: async () => {
    if (!comInput.value.trim()) return;
    try { await api("POST", `/videos/${id}/comments`, { body: comInput.value }); comInput.value = ""; loadComments(); } catch (e) { fail(e); }
  } }, t("com.post"))));
  comBox.insertBefore(comInput, comBox.lastChild);
  loadComments().catch(fail);

  // History tab
  const preview = h("div");
  async function loadHistory() {
    const vs = await api("GET", `/videos/${id}/versions`);
    fill(histBox, 
      h("div", { class: "bar" }, h("button", { class: "btn ghost sm", onclick: () => {
        const l = prompt(t("hist.prompt")); if (l) save(l);
      } }, t("hist.name"))),
      vs.length ? vs.map((v) => h("div", { class: "item" },
        h("div", { class: "h" }, h("strong", {}, v.label || t("hist.auto")), h("span", {}, fmtDate(v.created))),
        h("div", { class: "muted" }, t("hist.by", { a: v.author || t("unknown"), n: v.size.toLocaleString(dateLocale()) }) + " "),
        h("a", { href: "#", onclick: async (e) => {
          e.preventDefault();
          const full = await api("GET", "/versions/" + v.id).catch(fail); if (!full) return;
          fill(preview, h("div", { class: "out" }, full.content),
            h("div", { class: "bar" }, h("button", { class: "btn sm", onclick: () => {
              if (confirm(t("confirm.restore"))) {
                save(t("hist.beforeRestore")).then(() => { script.value = full.content; script.dispatchEvent(new Event("input")); toast(t("toast.restored")); });
              } } }, t("hist.restore"))));
        } }, t("hist.view")))) : h("div", { class: "empty" }, t("hist.none")),
      preview);
  }
  loadHistory().catch(fail);

  // Notes tab
  const notes = h("textarea", { rows: "12", placeholder: t("notes.ph"), "aria-label": t("tab.notes") });
  notes.value = video.notes || "";
  notes.addEventListener("change", () => patch({ notes: notes.value }));
  notesBox.append(h("p", { class: "muted" }, t("notes.help")), notes);
  show("ai");

  const del = h("button", { class: "btn ghost sm", onclick: async () => {
    if (!confirm(t("confirm.delvideo"))) return;
    dirty = false; blocked = false; clearTimeout(timer);
    try { await api("DELETE", "/videos/" + id); location.hash = "#/"; } catch (e) { fail(e); }
  } }, t("btn.delete"));

  main.append(banner,
    h("a", { class: "crumb", href: "#/" }, t("back")),
    title,
    h("div", { class: "props" },
      h("div", { class: "stage-pick" }, h("label", {}, t("field.stage")), h("div", { class: "sel" }, stageDot, status)),
      h("div", {}, h("label", {}, t("field.owner")), owner),
      h("div", {}, h("label", {}, t("field.deadline")), deadline)),
    h("div", { class: "editor-wrap" },
      h("div", { class: "paper" }, script,
        h("div", { class: "foot" }, stat, h("span", { class: "sp" }), saveState,
          h("button", { class: "btn sm", onclick: () => save() }, t("btn.savenow")), del)),
      order, panel));
}

// ---------- calendar ----------
async function calendarView(main) {
  const videos = await api("GET", "/videos");
  const now = new Date();
  let year = now.getFullYear(), month = now.getMonth();
  const grid = h("div", { class: "cal", role: "grid" });
  const label = h("h2", { class: "cal-title", "aria-live": "polite" });
  const undated = h("div");
  const iso = (y, m, d) => `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  const draw = () => {
    label.textContent = new Date(year, month, 1).toLocaleDateString(dateLocale(), { month: "long", year: "numeric" });
    const lead = (new Date(year, month, 1).getDay() + 6) % 7; // Monday first
    const days = new Date(year, month + 1, 0).getDate();
    const cells = [0, 1, 2, 3, 4, 5, 6].map((i) => h("div", { class: "cal-dow", role: "columnheader" }, new Date(2024, 0, 1 + i).toLocaleDateString(dateLocale(), { weekday: "short" })));
    for (let i = 0; i < lead; i++) cells.push(h("div", { class: "cal-cell blank" }));
    for (let d = 1; d <= days; d++) {
      const key = iso(year, month, d);
      const due = videos.filter((v) => v.deadline === key);
      cells.push(h("div", { class: "cal-cell" + (key === todayStr() ? " today" : ""), role: "gridcell" },
        h("span", { class: "cal-day" }, d),
        due.map((v) => {
          const done = v.status === "published", late = key < todayStr() && !done;
          return h("a", { class: "cal-item" + (late ? " late" : done ? " done" : ""), href: "#/video/" + v.id, style: "--c:" + stageColor(v.status),
            title: `${v.title} (${stageName(v.status)}${late ? t("cal.late") : ""})` }, v.title);
        })));
    }
    while ((cells.length - 7) % 7) cells.push(h("div", { class: "cal-cell blank" }));
    fill(grid, cells);
  };
  const go = (delta) => { month += delta; if (month < 0) { month = 11; year--; } if (month > 11) { month = 0; year++; } draw(); };
  const nodate = videos.filter((v) => !v.deadline);
  fill(undated, nodate.length && [h("h2", {}, t("cal.nodl")),
    h("p", { class: "muted" }, t("cal.nodl.help")),
    h("ul", { class: "plain" }, nodate.map((v) => h("li", {}, h("a", { href: "#/video/" + v.id }, v.title), h("span", { class: "muted" }, " " + stageName(v.status)))))]);
  draw();
  main.append(pageHead(t("cal.title"), t("cal.sub")),
    h("div", { class: "cal-bar" }, label, h("span", { class: "sp" }),
      h("button", { class: "btn ghost sm", onclick: () => go(-1) }, t("cal.prev")),
      h("button", { class: "btn ghost sm", onclick: () => { year = now.getFullYear(); month = now.getMonth(); draw(); } }, t("cal.today")),
      h("button", { class: "btn ghost sm", onclick: () => go(1) }, t("cal.next"))),
    grid, undated);
}

// ---------- library ----------
async function libraryView(main) {
  const lib = await api("GET", "/library");
  const guide = h("textarea", { rows: "16", "aria-label": t("lib.style") }); guide.value = lib.style_guide;
  const tmpl = h("textarea", { rows: "12", "aria-label": t("lib.tmpl") }); tmpl.value = lib.script_template;
  const saveLib = (key, el) => api("PUT", "/library", { [key]: el.value }).then(() => toast(t("toast.saved"))).catch(fail);
  const exTitle = h("input", { type: "text", placeholder: t("lib.ex.titleph"), "aria-label": t("lib.ex.titleph") });
  const exBody = h("textarea", { rows: "8", placeholder: t("lib.ex.bodyph"), "aria-label": t("lib.ex.bodyph") });
  const list = h("div");
  const drawEx = (items) => fill(list, items.length ? items.map((x) => h("div", { class: "item" },
    h("div", { class: "h" }, h("strong", {}, x.title), h("span", {}, t("lib.chars", { n: x.size.toLocaleString(dateLocale()) }) + " ",
      h("a", { href: "#", onclick: async (e) => { e.preventDefault(); if (confirm(t("confirm.rmex"))) { await api("DELETE", "/examples/" + x.id).catch(fail); render(); } } }, t("remove")))))) :
    h("div", { class: "empty" }, t("lib.ex.none")));
  drawEx(lib.examples);
  main.append(pageHead(t("lib.title"), t("lib.sub")),
    h("div", { class: "grid2" },
      h("div", { class: "stack" },
        h("div", { class: "card" }, h("h2", {}, t("lib.style")),
          h("p", { class: "muted" }, t("lib.style.help")), guide,
          h("div", { class: "bar" }, h("button", { class: "btn", onclick: () => saveLib("style_guide", guide) }, t("lib.savestyle")))),
        h("div", { class: "card" }, h("h2", {}, t("lib.tmpl")),
          h("p", { class: "muted" }, t("lib.tmpl.help")), tmpl,
          h("div", { class: "bar" }, h("button", { class: "btn", onclick: () => saveLib("script_template", tmpl) }, t("lib.savetmpl"))))),
      h("div", { class: "card" }, h("h2", {}, t("lib.ex")), list,
        h("label", {}, t("lib.addex.label")), exTitle, exBody,
        h("div", { class: "bar" }, h("button", { class: "btn", onclick: async () => {
          try { await api("POST", "/examples", { title: exTitle.value, body: exBody.value }); render(); } catch (e) { fail(e); }
        } }, t("lib.addex"))))));
}

// ---------- team & AI settings ----------
async function settingsView(main) {
  const [set] = await Promise.all([api("GET", "/settings")]);
  const admin = S.user.is_admin;
  const provider = h("select", { disabled: !admin }, Object.entries(set.providers).map(([k, v]) => h("option", { value: k }, v.label)));
  provider.value = set.provider;
  const model = h("input", { type: "text", value: set.model, disabled: !admin, placeholder: t("set.model.ph"), "aria-label": t("set.model") });
  const limit = h("input", { type: "number", min: "0", max: "20", value: set.example_limit, disabled: !admin });
  const wpmIn = h("input", { type: "number", min: "60", max: "300", value: set.wpm, disabled: !admin });
  const keys = {};
  const keyRows = Object.entries(set.providers).map(([k, v]) => {
    keys[k] = h("input", { type: "password", autocomplete: "off", disabled: !admin, placeholder: v.has_key ? t("set.key.set") : t("set.key.paste"), "aria-label": t("set.key", { p: v.label }) });
    return [h("label", {}, t("set.key", { p: v.label })), keys[k]];
  });
  const defaultHint = h("p", { class: "muted" });
  const syncHint = () => { defaultHint.textContent = t("set.model.default", { m: set.providers[provider.value].default_model }); };
  provider.addEventListener("change", syncHint); syncHint();

  const nu = { name: h("input", { type: "text", placeholder: t("ph.dispname"), "aria-label": t("ph.dispname") }), user: h("input", { type: "text", placeholder: t("ph.username"), "aria-label": t("field.username") }),
    pw: h("input", { type: "password", placeholder: t("ph.tmppw"), "aria-label": t("ph.tmppw"), autocomplete: "new-password" }) };
  const cur = h("input", { type: "password", placeholder: t("pw.cur"), "aria-label": t("pw.cur"), autocomplete: "current-password" });
  const np = h("input", { type: "password", placeholder: t("pw.new"), "aria-label": t("pw.new"), autocomplete: "new-password" });

  main.append(pageHead(t("set.title"), t("set.sub")), h("div", { class: "grid2" },
    h("div", { class: "card" }, h("h2", {}, t("set.ai")),
      !admin && h("p", { class: "muted" }, t("set.adminonly")),
      h("label", {}, t("set.provider")), provider, h("label", {}, t("set.model")), model, defaultHint,
      keyRows, h("label", {}, t("set.limit")), limit,
      h("label", {}, t("set.wpm")), wpmIn, h("p", { class: "muted" }, t("set.wpm.help")),
      h("p", { class: "muted" }, t("set.keys.note")),
      admin && h("div", { class: "bar" }, h("button", { class: "btn", onclick: async () => {
        const api_keys = {}; for (const [k, el] of Object.entries(keys)) if (el.value.trim()) api_keys[k] = el.value;
        try { await api("PUT", "/settings", { provider: provider.value, model: model.value, example_limit: +limit.value, wpm: +wpmIn.value, api_keys }); toast(t("toast.saved")); render(); } catch (e) { fail(e); }
      } }, t("set.save")))),
    h("div", { class: "stack" },
      h("div", { class: "card" }, h("h2", {}, t("team")),
        h("table", {}, S.users.map((u) => h("tr", {}, h("td", {}, h("span", { class: "who", style: "display:flex;align-items:center;gap:10px" }, avatar(u.display_name), u.display_name)), h("td", { class: "muted" }, u.username), h("td", { class: "muted" }, u.is_admin ? t("role.admin") : t("role.member")),
          h("td", {}, admin && u.id !== S.user.id && h("a", { href: "#", onclick: async (e) => { e.preventDefault(); if (confirm(t("confirm.rmuser", { n: u.display_name }))) { await api("DELETE", "/users/" + u.id).catch(fail); render(); } } }, t("remove")))))),
        admin && [h("label", {}, t("team.add")), nu.name, h("div", { style: "height:6px" }), nu.user, h("div", { style: "height:6px" }), nu.pw,
          h("div", { class: "bar" }, h("button", { class: "btn", onclick: async () => {
            try { await api("POST", "/users", { display_name: nu.name.value, username: nu.user.value, password: nu.pw.value }); toast(t("toast.added")); render(); } catch (e) { fail(e); }
          } }, t("team.addbtn")))]),
      h("div", { class: "card" }, h("h2", {}, t("pw.title")), cur, h("div", { style: "height:6px" }), np,
        h("div", { class: "bar" }, h("button", { class: "btn", onclick: async () => {
          try { await api("POST", "/password", { current: cur.value, new: np.value }); cur.value = np.value = ""; toast(t("toast.pwchanged")); } catch (e) { fail(e); }
        } }, t("pw.btn")))))));
}

render();
