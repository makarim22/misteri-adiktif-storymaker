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
  if (!r.ok) { const e = new Error(data.error || r.statusText); e.status = r.status; e.data = data; throw e; }
  return data;
}

let toastTimer;
function toast(msg) {
  let t = document.querySelector(".toast");
  if (!t) { t = h("div", { class: "toast" }); document.body.append(t); }
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

const fmtDate = (ts) => new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
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

const shortDate = (d) => new Date(d + "T00:00:00").toLocaleDateString([], { month: "short", day: "numeric" });
const stageColor = (s) => `var(--s-${s})`;
function pageHead(title, sub, ...actions) {
  return h("div", { class: "page-h" }, h("div", {}, h("h1", {}, title), sub && h("p", {}, sub)), actions.length ? h("div", {}, actions) : null);
}

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
      h("nav", { class: "nav" }, nav("/", "Board"), nav("/library", "Library"), nav("/settings", "Team and AI")),
      h("div", { class: "sp" }),
      h("div", { class: "me" }, avatar(S.user.display_name), h("span", { class: "nm" }, S.user.display_name),
        h("button", { class: "linkbtn", onclick: async () => { await api("POST", "/logout", {}); S.user = null; render(); } }, "Sign out"))),
    main);
  try {
    S.users = await api("GET", "/users");
    let m;
    if ((m = route.match(/^\/video\/(\d+)$/))) await videoView(main, +m[1]);
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
    name: h("input", { type: "text", placeholder: "Your name" }),
    user: h("input", { type: "text", placeholder: "username", autocomplete: "username" }),
    pw: h("input", { type: "password", placeholder: "password (8+ chars)", autocomplete: setup ? "new-password" : "current-password" }),
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
    h("h1", {}, setup ? "Create the first account" : "Sign in"),
    h("p", { class: "muted" }, setup ? "This account becomes the admin. You can add your teammates afterwards." : "Pick up where the team left off."),
    setup && [h("label", {}, "Your name"), f.name], h("label", {}, "Username"), f.user, h("label", {}, "Password"), f.pw,
    err, h("div", { class: "bar" }, h("button", { class: "btn", style: "width:100%", onclick: submit }, setup ? "Create account" : "Sign in")))));
}

// ---------- board ----------
async function boardView(main) {
  let videos = await api("GET", "/videos");
  const title = h("input", { type: "text", placeholder: "Title of the next video", "aria-label": "New video title" });
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
        h("h3", {}, h("span", {}, cap(st)), h("span", { class: "n" }, items.length)));
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
            v.owner_name ? avatar(v.owner_name) : h("span", {}, "No owner"),
            h("span", { class: "sp" }),
            v.deadline ? h("span", { class: "due" + (late ? " late" : "") }, (late ? "Overdue " : "Due ") + shortDate(v.deadline)) : null));
        card.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", String(v.id)); card.classList.add("dragging"); });
        card.addEventListener("dragend", () => card.classList.remove("dragging"));
        col.append(card);
      });
      if (!items.length) col.append(h("div", { class: "empty-col" }, "No videos at this stage"));
      return col;
    }));
  };
  draw();
  main.append(pageHead("Board", videos.length ? "Drag a card to another column to move that video to the next stage." : "No videos yet. Add the first one to start.",
    h("div", { class: "newvideo" }, title, h("button", { class: "btn", onclick: add }, "New video"))), board);
}

// ---------- video / editor ----------
async function videoView(main, id) {
  const [video, stages, settings] = await Promise.all([api("GET", "/videos/" + id), api("GET", "/ai/stages"), api("GET", "/settings")]);
  let rev = video.rev, dirty = false, saving = false, blocked = false, timer = null;

  const banner = h("div", { class: "banner", style: "display:none" });
  const title = h("input", { type: "text", value: video.title, class: "title-in", "aria-label": "Video title" });
  const status = h("select", {}, S.statuses.map((s) => h("option", { value: s }, cap(s))));
  status.value = video.status;
  const stageDot = h("span", { class: "dot", style: "--c:" + stageColor(video.status) });
  const owner = h("select", {}, h("option", { value: "" }, "Unassigned"), S.users.map((u) => h("option", { value: u.id }, u.display_name)));
  owner.value = video.owner_id ?? "";
  const deadline = h("input", { type: "date", value: video.deadline || "" });
  const patch = (obj) => api("PATCH", "/videos/" + id, obj).then(() => toast("Saved")).catch(fail);
  title.addEventListener("change", () => patch({ title: title.value }));
  status.addEventListener("change", () => { stageDot.style.setProperty("--c", stageColor(status.value)); patch({ status: status.value }); });
  owner.addEventListener("change", () => patch({ owner_id: owner.value ? +owner.value : null }));
  deadline.addEventListener("change", () => patch({ deadline: deadline.value }));

  const script = h("textarea", { id: "script", spellcheck: "true", placeholder: "Write or paste your script here..." });
  script.value = video.content;
  const stat = h("span", { class: "muted" });
  setTimeout(() => setSave("All changes saved"));
  const saveState = h("span", { class: "savestate" });
  const setSave = (txt, cls) => { saveState.textContent = txt; saveState.className = "savestate" + (cls ? " " + cls : ""); };
  const updateStat = () => { const w = words(script.value); stat.textContent = `${w} words, about ${(w / 150).toFixed(1)} min spoken`; };
  updateStat();

  async function save(label) {
    if (blocked || saving) return;
    saving = true; clearTimeout(timer); setSave("Saving...", "dirty");
    try {
      const r = await api("PUT", `/videos/${id}/script`, { content: script.value, base_rev: rev, label: label || "" });
      rev = r.rev; dirty = false; setSave("Saved " + new Date().toLocaleTimeString());
      if (label) { toast("Version saved"); loadHistory(); }
    } catch (e) {
      if (e.status === 409) {
        blocked = true; banner.style.display = ""; banner.textContent = e.message;
        setSave("Not saved - conflict", "bad");
      } else { setSave("Save failed", "bad"); fail(e); }
    } finally { saving = false; }
  }
  script.addEventListener("input", () => {
    dirty = true; updateStat(); setSave("Unsaved changes", "dirty");
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
  [["ai", "Write with AI"], ["comments", "Comments"], ["history", "History"], ["notes", "Notes"]].forEach(([k, label]) => {
    tabs[k] = h("button", { onclick: () => show(k) }, label); tabBar.append(tabs[k]);
  });
  panel.append(tabBar, body);

  // AI tab
  const providerOk = settings.providers[settings.provider].has_key;
  const stageSel = h("select", {}, stages.map((s) => h("option", { value: s.id }, s.label)));
  const aiInput = h("textarea", { rows: "6", placeholder: "Topic, notes, outline or instruction..." });
  const hint = h("p", { class: "muted" });
  const out = h("div", { class: "out", style: "display:none" });
  const outBtns = h("div", { class: "bar", style: "display:none" });
  const VERBS = { ideas: "Brainstorm angles", research: "Build research brief", outline: "Draft outline", draft: "Write first draft",
    critique: "Critique script", rewrite: "Rewrite section", package: "Create titles and description", repurpose: "Create Shorts and post" };
  const verb = () => VERBS[stageSel.value] || "Generate";
  const runBtn = h("button", { class: "btn" }, "Generate");
  const runsBox = h("div");
  let lastText = "";
  const syncStage = () => {
    const st = stages.find((s) => s.id === stageSel.value);
    aiInput.style.display = st.needs === "script" ? "none" : "";
    runBtn.textContent = verb();
    hint.textContent = st.needs === "script" ? "Uses the whole script in the editor." :
      st.needs === "both" ? "Describe what to rewrite. The full script is sent as context." : "";
  };
  stageSel.addEventListener("change", syncStage); syncStage();
  const insertText = (txt, where) => {
    if (where === "cursor") { const a = script.selectionStart, b = script.selectionEnd; script.setRangeText(txt, a, b, "end"); }
    else script.value = script.value.replace(/\s*$/, "") + "\n\n" + txt + "\n";
    script.dispatchEvent(new Event("input")); toast("Inserted into script");
  };
  outBtns.append(
    h("button", { class: "btn ghost sm", onclick: () => insertText(lastText, "end") }, "Add to end of script"),
    h("button", { class: "btn ghost sm", onclick: () => insertText(lastText, "cursor") }, "Insert at cursor"),
    h("button", { class: "btn ghost sm", onclick: () => navigator.clipboard.writeText(lastText).then(() => toast("Copied")) }, "Copy"));
  runBtn.addEventListener("click", async () => {
    runBtn.disabled = true; runBtn.replaceChildren(h("span", { class: "spin" }), "Working on it");
    try {
      const r = await api("POST", "/ai", { video_id: id, stage: stageSel.value, input: aiInput.value, script: script.value });
      lastText = r.text; out.textContent = r.text; out.style.display = ""; outBtns.style.display = ""; loadRuns();
    } catch (e) { fail(e); out.textContent = e.message; out.style.display = ""; outBtns.style.display = "none"; }
    finally { runBtn.disabled = false; runBtn.textContent = verb(); }
  });
  async function loadRuns() {
    const runs = await api("GET", `/videos/${id}/ai_runs`);
    fill(runsBox, h("h2", { style: "margin-top:28px" }, "Earlier results"),
      runs.length ? runs.map((r) => h("details", { class: "item" },
        h("summary", {}, (stages.find((s) => s.id === r.stage) || { label: r.stage }).label, h("span", {}, r.author + ", " + fmtDate(r.created))),
        h("div", { class: "out" }, r.output),
        h("button", { class: "btn ghost sm", onclick: () => navigator.clipboard.writeText(r.output).then(() => toast("Copied")) }, "Copy"))) : h("div", { class: "empty" }, "Nothing yet. Choose a task and run it."));
  }
  aiBox.append(!providerOk && h("p", { class: "err" }, "No API key for " + settings.providers[settings.provider].label + ". An admin can add it under Team and AI."),
    h("label", {}, "Task"), stageSel, hint, aiInput, h("div", { class: "bar" }, runBtn), out, outBtns, runsBox);
  loadRuns().catch(fail);

  // Comments tab
  const comInput = h("textarea", { rows: "3", placeholder: "Write a comment..." });
  async function loadComments() {
    const cs = await api("GET", `/videos/${id}/comments`);
    fill(comList, cs.length ? cs.map((c) => h("div", { class: "item" },
      h("div", { class: "h" }, h("span", { class: "who" }, avatar(c.author), h("strong", {}, c.author)),
        h("span", {}, fmtDate(c.created), " ",
          (c.user_id === S.user.id || S.user.is_admin) && h("a", { href: "#", onclick: async (e) => { e.preventDefault(); await api("DELETE", "/comments/" + c.id).catch(fail); loadComments(); } }, "Delete"))),
      h("div", { class: "b" }, c.body))) : h("div", { class: "empty" }, "No comments yet. Leave a note for the team."));
  }
  const comList = h("div");
  comBox.append(comList, h("div", { class: "bar" }, h("button", { class: "btn sm", onclick: async () => {
    if (!comInput.value.trim()) return;
    try { await api("POST", `/videos/${id}/comments`, { body: comInput.value }); comInput.value = ""; loadComments(); } catch (e) { fail(e); }
  } }, "Post")));
  comBox.insertBefore(comInput, comBox.lastChild);
  loadComments().catch(fail);

  // History tab
  const preview = h("div");
  async function loadHistory() {
    const vs = await api("GET", `/videos/${id}/versions`);
    fill(histBox, 
      h("div", { class: "bar" }, h("button", { class: "btn ghost sm", onclick: () => {
        const l = prompt("Name this version (e.g. 'After editor pass')"); if (l) save(l);
      } }, "Name this version")),
      vs.length ? vs.map((v) => h("div", { class: "item" },
        h("div", { class: "h" }, h("strong", {}, v.label || "Autosave"), h("span", {}, fmtDate(v.created))),
        h("div", { class: "muted" }, "Saved by " + (v.author || "unknown") + ", " + v.size.toLocaleString() + " characters. "),
        h("a", { href: "#", onclick: async (e) => {
          e.preventDefault();
          const full = await api("GET", "/versions/" + v.id).catch(fail); if (!full) return;
          fill(preview, h("div", { class: "out" }, full.content),
            h("div", { class: "bar" }, h("button", { class: "btn sm", onclick: () => {
              if (confirm("Replace the editor text with this version? Your current text is saved as a version first.")) {
                save("Before restore").then(() => { script.value = full.content; script.dispatchEvent(new Event("input")); toast("Restored - saving"); });
              } } }, "Restore this version")));
        } }, "View"))) : h("div", { class: "empty" }, "No versions yet. One is saved automatically as you write."),
      preview);
  }
  loadHistory().catch(fail);

  // Notes tab
  const notes = h("textarea", { rows: "12", placeholder: "Sources, links, reminders for the team..." });
  notes.value = video.notes || "";
  notes.addEventListener("change", () => patch({ notes: notes.value }));
  notesBox.append(h("p", { class: "muted" }, "Shared scratchpad for this video (not part of the script)."), notes);
  show("ai");

  const del = h("button", { class: "btn ghost sm", onclick: async () => {
    if (!confirm("Delete this video, its script, history and comments? This cannot be undone.")) return;
    dirty = false; blocked = false; clearTimeout(timer);
    try { await api("DELETE", "/videos/" + id); location.hash = "#/"; } catch (e) { fail(e); }
  } }, "Delete video");

  main.append(banner,
    h("a", { class: "crumb", href: "#/" }, "Back to board"),
    title,
    h("div", { class: "props" },
      h("div", { class: "stage-pick" }, h("label", {}, "Stage"), h("div", { class: "sel" }, stageDot, status)),
      h("div", {}, h("label", {}, "Owner"), owner),
      h("div", {}, h("label", {}, "Deadline"), deadline)),
    h("div", { class: "editor-wrap" },
      h("div", { class: "paper" }, script,
        h("div", { class: "foot" }, stat, h("span", { class: "sp" }), saveState,
          h("button", { class: "btn sm", onclick: () => save() }, "Save now"), del)),
      panel));
}

// ---------- library ----------
async function libraryView(main) {
  const lib = await api("GET", "/library");
  const guide = h("textarea", { rows: "16" }); guide.value = lib.style_guide;
  const tmpl = h("textarea", { rows: "12" }); tmpl.value = lib.script_template;
  const saveLib = (key, el) => api("PUT", "/library", { [key]: el.value }).then(() => toast("Saved")).catch(fail);
  const exTitle = h("input", { type: "text", placeholder: "Title of a script you are proud of" });
  const exBody = h("textarea", { rows: "8", placeholder: "Paste the full script text here" });
  const list = h("div");
  const drawEx = (items) => fill(list, items.length ? items.map((x) => h("div", { class: "item" },
    h("div", { class: "h" }, h("strong", {}, x.title), h("span", {}, x.size.toLocaleString() + " characters ",
      h("a", { href: "#", onclick: async (e) => { e.preventDefault(); if (confirm("Remove this example?")) { await api("DELETE", "/examples/" + x.id).catch(fail); render(); } } }, "Remove"))))) :
    h("div", { class: "empty" }, "No examples yet. Add 3 to 5 of your best scripts. The AI learns your voice from them."));
  drawEx(lib.examples);
  main.append(pageHead("Library", "Everything here is sent to the AI as context with every request. The better this is, the more the drafts sound like you."),
    h("div", { class: "grid2" },
      h("div", { class: "stack" },
        h("div", { class: "card" }, h("h2", {}, "Style guide"),
          h("p", { class: "muted" }, "Voice, tone, pacing, hook patterns, phrases to use and to avoid."), guide,
          h("div", { class: "bar" }, h("button", { class: "btn", onclick: () => saveLib("style_guide", guide) }, "Save style guide"))),
        h("div", { class: "card" }, h("h2", {}, "Script template"),
          h("p", { class: "muted" }, "Pre-filled into every new video."), tmpl,
          h("div", { class: "bar" }, h("button", { class: "btn", onclick: () => saveLib("script_template", tmpl) }, "Save template")))),
      h("div", { class: "card" }, h("h2", {}, "Example scripts"), list,
        h("label", {}, "Add an example"), exTitle, exBody,
        h("div", { class: "bar" }, h("button", { class: "btn", onclick: async () => {
          try { await api("POST", "/examples", { title: exTitle.value, body: exBody.value }); render(); } catch (e) { fail(e); }
        } }, "Add example")))));
}

// ---------- team & AI settings ----------
async function settingsView(main) {
  const [set] = await Promise.all([api("GET", "/settings")]);
  const admin = S.user.is_admin;
  const provider = h("select", { disabled: !admin }, Object.entries(set.providers).map(([k, v]) => h("option", { value: k }, v.label)));
  provider.value = set.provider;
  const model = h("input", { type: "text", value: set.model, disabled: !admin, placeholder: "leave blank for the default" });
  const limit = h("input", { type: "number", min: "0", max: "20", value: set.example_limit, disabled: !admin });
  const keys = {};
  const keyRows = Object.entries(set.providers).map(([k, v]) => {
    keys[k] = h("input", { type: "password", autocomplete: "off", disabled: !admin, placeholder: v.has_key ? "A key is set (type to replace)" : "Paste API key" });
    return [h("label", {}, v.label + " API key"), keys[k]];
  });
  const defaultHint = h("p", { class: "muted" });
  const syncHint = () => { defaultHint.textContent = "Default model: " + set.providers[provider.value].default_model; };
  provider.addEventListener("change", syncHint); syncHint();

  const nu = { name: h("input", { type: "text", placeholder: "Display name" }), user: h("input", { type: "text", placeholder: "username" }),
    pw: h("input", { type: "password", placeholder: "temporary password (8+)", autocomplete: "new-password" }) };
  const cur = h("input", { type: "password", placeholder: "Current password", autocomplete: "current-password" });
  const np = h("input", { type: "password", placeholder: "New password (8+)", autocomplete: "new-password" });

  main.append(pageHead("Team and AI", "Add your teammates and choose which AI writes with you."), h("div", { class: "grid2" },
    h("div", { class: "card" }, h("h2", {}, "AI settings"),
      !admin && h("p", { class: "muted" }, "Only an admin can change these."),
      h("label", {}, "Provider"), provider, h("label", {}, "Model"), model, defaultHint,
      keyRows, h("label", {}, "How many example scripts to include with each request"), limit,
      h("p", { class: "muted" }, "Keys are stored in the local database file. You can also set ANTHROPIC_API_KEY / OPENAI_API_KEY as environment variables instead."),
      admin && h("div", { class: "bar" }, h("button", { class: "btn", onclick: async () => {
        const api_keys = {}; for (const [k, el] of Object.entries(keys)) if (el.value.trim()) api_keys[k] = el.value;
        try { await api("PUT", "/settings", { provider: provider.value, model: model.value, example_limit: +limit.value, api_keys }); toast("Saved"); render(); } catch (e) { fail(e); }
      } }, "Save AI settings"))),
    h("div", { class: "stack" },
      h("div", { class: "card" }, h("h2", {}, "Team"),
        h("table", {}, S.users.map((u) => h("tr", {}, h("td", {}, h("span", { class: "who", style: "display:flex;align-items:center;gap:10px" }, avatar(u.display_name), u.display_name)), h("td", { class: "muted" }, u.username), h("td", { class: "muted" }, u.is_admin ? "Admin" : "Member"),
          h("td", {}, admin && u.id !== S.user.id && h("a", { href: "#", onclick: async (e) => { e.preventDefault(); if (confirm("Remove " + u.display_name + "?")) { await api("DELETE", "/users/" + u.id).catch(fail); render(); } } }, "Remove"))))),
        admin && [h("label", {}, "Add a teammate"), nu.name, h("div", { style: "height:6px" }), nu.user, h("div", { style: "height:6px" }), nu.pw,
          h("div", { class: "bar" }, h("button", { class: "btn", onclick: async () => {
            try { await api("POST", "/users", { display_name: nu.name.value, username: nu.user.value, password: nu.pw.value }); toast("Teammate added"); render(); } catch (e) { fail(e); }
          } }, "Add teammate"))]),
      h("div", { class: "card" }, h("h2", {}, "Change my password"), cur, h("div", { style: "height:6px" }), np,
        h("div", { class: "bar" }, h("button", { class: "btn", onclick: async () => {
          try { await api("POST", "/password", { current: cur.value, new: np.value }); cur.value = np.value = ""; toast("Password changed"); } catch (e) { fail(e); }
        } }, "Change password"))))));
}

render();
