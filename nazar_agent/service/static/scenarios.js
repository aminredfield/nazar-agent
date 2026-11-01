"use strict";
// NAZAR Agent — the Scenarios page: the new abilities played on staged scenes.
// The visitor plays the other side: answers as the employee, picks the photo,
// or brings their own picture. Each call is one model call, timed on screen.
// Uses i18n.js (t, L, LANG, setLang, translatePage, toggleTheme).

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined) continue;
    if (key === "class") node.className = value; else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function send(path, body) {
  const options = body instanceof FormData ? { method: "POST", body }
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Error ${response.status}`);
  return data;
}

function form(fields) {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) if (value !== null && value !== undefined) data.append(key, value);
  data.append("lang", LANG);
  return data;
}

// A button that waits for the model: says so, shows how long it took, never runs twice at once.
async function working(button, place, run) {
  if (button.disabled) return;
  const label = button.textContent;
  const started = performance.now();
  button.disabled = true;
  button.textContent = t("sc_thinking");
  place.replaceChildren(el("p", { class: "muted sc-wait" }, t("sc_looking")));
  try {
    const nodes = await run();
    const seconds = ((performance.now() - started) / 1000).toFixed(1);
    place.replaceChildren(...nodes.filter(Boolean), el("p", { class: "sc-time" }, `Muse Spark 1.3 · ${seconds} s`));
  } catch (error) {
    place.replaceChildren(el("p", { class: "error" }, error.message));
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

// The scenes of a section as chips: ``label`` gives the title and an optional line under it.
function sceneChips(holder, scenes, label, onPick, chosen) {
  const keys = Object.keys(scenes);
  const chips = keys.map((key) => {
    const [title, under] = label(scenes[key], key);
    const chip = el("button", { type: "button", class: "sc-chip" }, el("b", {}, title), under ? el("small", {}, under) : null);
    chip.addEventListener("click", () => {
      for (const other of holder.children) other.classList.remove("on");
      chip.classList.add("on");
      onPick(key, scenes[key]);
    });
    return chip;
  });
  holder.replaceChildren(...chips);
  chips[Math.max(0, keys.indexOf(chosen))].click();
}

const segment = (scene) => L().SEGMENTS[scene.segment] || scene.segment;
const picture = (name) => `/static/scenarios/${name}`;
const verdict = (text, tone) => el("div", { class: `sc-verdict ${tone}` }, text);
const VERDICT_TONE = { consistent: "good", contradicts: "bad", cannot_tell: "warn" };
let scenes = null;

// ---- 1. The employee explains ----------------------------------------------------

const explain = { key: null, scene: null, asked: false };

function shown(scene) {
  return (scene.shown || {})[LANG] || { finding: scene.finding, records: scene.records };
}

function showExplain(key, scene) {
  Object.assign(explain, { key, scene, asked: false });
  const text = shown(scene);
  $("explain-card").replaceChildren(el("article", { class: "sc-card" },
    el("div", { class: "sc-where" }, `${segment(scene)} · ${scene.place}`),
    el("b", { class: "sc-name" }, scene.employee),
    el("p", { class: "sc-finding" }, text.finding),
    el("ul", { class: "sc-records" }, text.records.map((line) => el("li", {}, line)))));
  $("explain-lang").value = LANG;
  $("explain-ask").textContent = t("sc_ask", scene.employee.split(" ")[0]);
  $("explain-chat").replaceChildren(el("p", { class: "muted sc-empty" }, t("sc_chat_empty")));
  $("explain-result").replaceChildren();
  $("explain-templates").replaceChildren(...scene.replies.map((reply) => {
    const pick = el("button", { type: "button", class: "sc-template" },
      el("span", { class: "sc-lang" }, reply.lang.toUpperCase()), el("span", {}, reply.text),
      el("small", {}, t("sc_expected", t(`sc_v_${reply.expect}`).slice(2))));
    pick.addEventListener("click", () => answer(reply.text));
    return pick;
  }));
}

$("explain-ask").addEventListener("click", () => working($("explain-ask"), $("explain-result"), async () => {
  const data = await send("/api/scenarios/explain/ask", { scene: explain.key, lang: $("explain-lang").value });
  explain.asked = true;
  $("explain-chat").replaceChildren(el("div", { class: "sc-bubble from-agent" }, el("small", {}, t("sc_assistant")), data.message));
  return [el("p", { class: "muted" }, t("sc_sent"))];
}));

function answer(text) {
  if (!text.trim()) return;
  if (!explain.asked) {
    $("explain-result").replaceChildren(el("p", { class: "error" }, t("sc_ask_first")));
    return;
  }
  $("explain-chat").append(el("div", { class: "sc-bubble from-person" }, el("small", {}, explain.scene.employee), text));
  $("explain-text").value = "";
  working($("explain-ask"), $("explain-result"), async () => {
    const data = await send("/api/scenarios/explain/check", { scene: explain.key, said: text, lang: LANG });
    return [verdict(t(`sc_v_${data.verdict}`), VERDICT_TONE[data.verdict]), el("p", {}, data.why),
      el("p", { class: "muted" }, t("sc_next", data.next_step))];
  });
}
$("explain-form").addEventListener("submit", (event) => { event.preventDefault(); answer($("explain-text").value); });

// ---- 2. A company from one picture -----------------------------------------------

const roster = { key: null, own: null };

function rosterNodes(data) {
  return [
    el("h3", { class: "sc-label" }, t("sc_plan")),
    el("ol", { class: "plan sc-plan" }, data.plan.map((step) => el("li", {}, step))),
    data.unclear.length ? el("p", { class: "issue" }, t("sc_unclear", data.unclear.join("; "))) : null,
    el("details", { class: "fold sc-fold" }, el("summary", {}, t("sc_people_read", data.people.length)),
      el("div", { class: "table-wrap" }, el("table", { class: "company-table" },
        el("tr", {}, ["sc_th_name", "sc_th_work", "sc_th_days", "sc_th_shift"].map((head) => el("th", {}, t(head)))),
        data.people.map((person) => el("tr", {}, el("td", {}, person.name), el("td", {}, person.workplace || "—"),
          el("td", {}, person.days.map((day) => L().WEEKDAYS[day]).join(", ")),
          el("td", {}, `${person.shift_start}–${person.shift_end}`)))))),
    el("p", { class: "muted sc-small" }, t("sc_apply_note")),
  ];
}

function showRoster(key, scene) {
  Object.assign(roster, { key, own: null });
  $("roster-picture").src = picture(scene.file);
  $("roster-result").replaceChildren(el("p", { class: "muted" }, t(`sc_roster_${key}_note`)));
}

$("roster-read").addEventListener("click", () => working($("roster-read"), $("roster-result"), async () =>
  rosterNodes(await send("/api/scenarios/roster", form(roster.own ? { file: roster.own } : { scene: roster.key })))));
$("roster-file").addEventListener("change", (event) => {
  roster.own = event.target.files[0] || null;
  if (roster.own) $("roster-picture").src = URL.createObjectURL(roster.own);
});

// ---- 3. Who is on shift ----------------------------------------------------------

const headcount = { key: null, own: null };

function showHeadcount(key, scene) {
  Object.assign(headcount, { key, own: null });
  $("headcount-picture").src = picture(scene.file);
  $("headcount-roster").replaceChildren(el("p", { class: "sc-name" }, t("sc_checked_in", scene.checked_in.length)),
    el("p", { class: "muted sc-small" }, scene.checked_in.join(", ")));
  $("headcount-result").replaceChildren();
}

function headcountLine(data) {
  if (data.in_team === data.checked_in) return [t("sc_all_here", data.in_team, data.checked_in), "good"];
  if (data.in_team < data.checked_in) return [t("sc_missing", data.checked_in, data.in_team, data.checked_in - data.in_team), "warn"];
  return [t("sc_extra", data.checked_in, data.in_team), "bad"];
}

$("headcount-count").addEventListener("click", () => working($("headcount-count"), $("headcount-result"), async () => {
  const data = await send("/api/scenarios/headcount", form({ scene: headcount.key, file: headcount.own }));
  const [line, tone] = headcountLine(data);
  return [verdict(line, tone), el("p", {}, t("sc_in_photo", data.people, data.in_team)),
    data.notes ? el("p", { class: "muted" }, data.notes) : null];
}));
$("headcount-file").addEventListener("change", (event) => {
  headcount.own = event.target.files[0] || null;
  if (headcount.own) $("headcount-picture").src = URL.createObjectURL(headcount.own);
});

// ---- 4. The place behind a check-in ----------------------------------------------

const place = { key: null };
const placeLock = el("button", { type: "button", hidden: "" });  // one comparison at a time

function judgePlace(fields) {
  return working(placeLock, $("place-result"), async () => {
    const data = await send("/api/scenarios/place", form(fields));
    return [verdict(t(data.same_place ? "sc_same" : "sc_not_same"), data.same_place ? "good" : "bad"), el("p", {}, data.why)];
  });
}

function showPlace(key, scene) {
  place.key = key;
  $("place-references").replaceChildren(...scene.references.map((name) => el("img", { src: picture(name), alt: "", class: "sc-thumb" })));
  $("place-candidates").replaceChildren(...scene.candidates.map((item, index) => {
    const pick = el("button", { type: "button", class: "sc-candidate" },
      el("img", { src: picture(item.file), alt: "", class: "sc-thumb" }),
      el("b", {}, item.who), el("small", {}, t("sc_gps_ok")));
    pick.addEventListener("click", () => {
      for (const other of $("place-candidates").children) other.classList.remove("on");
      pick.classList.add("on");
      judgePlace({ scene: key, candidate: String(index) });
    });
    return pick;
  }));
  $("place-result").replaceChildren();
}
$("place-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (file) judgePlace({ scene: place.key, file });
});

// ---- The page: the gallery, or one scenario ---------------------------------------
// /scenarios shows a card per problem; /scenarios/<name> shows that one scene with
// why it matters and its three steps. The names are the server's SCENE_PAGES.

const PAGES = [
  { name: "gps", part: "place", icon: "📍" },
  { name: "explain", part: "explain", icon: "💬" },
  { name: "headcount", part: "headcount", icon: "👥" },
  { name: "roster", part: "roster", icon: "📋" },
];
const opened = PAGES.find((page) => page.name === location.pathname.split("/")[2]) || null;

function drawPage() {
  $("gallery").hidden = Boolean(opened);
  $("scene-intro").hidden = !opened;
  for (const page of PAGES) $(page.part).hidden = page !== opened;
  if (!opened) {
    $("sc-cards").replaceChildren(...PAGES.map((page) => el("a", { class: "sc-card-link", href: `/scenarios/${page.name}` },
      el("span", { class: "try-icon", "aria-hidden": "true" }, page.icon),
      el("b", {}, t(`sc_${page.name}_title`)), el("span", { class: "sc-pain" }, t(`sc_${page.name}_pain`)),
      el("span", { class: "sc-meta" }, t(`sc_${page.name}_meta`)), el("span", { class: "try-go" }, t("sc_try")))));
    document.title = t("sc_title");
    return;
  }
  const key = (part) => t(`sc_${opened.name}_${part}`);
  $("scene-title").textContent = key("title");
  $("scene-why").textContent = key("why");
  $("scene-does").textContent = key("does");
  $("scene-steps").replaceChildren(...[1, 2, 3].map((step) => el("li", {}, key(`s${step}`))));
  $(opened.part).append($("scene-nazar"));
  $("scene-nazar").textContent = key("nazar");
  $("scene-nazar").hidden = false;
  document.title = `NAZAR · ${key("title")}`;
}

function drawScenes() {
  sceneChips($("explain-scenes"), scenes.explain, (scene) => [segment(scene), scene.employee], showExplain, explain.key);
  sceneChips($("roster-scenes"), scenes.roster, (_, key) => [t(`sc_roster_${key}`)], showRoster, roster.key);
  sceneChips($("headcount-scenes"), scenes.headcount, (scene) => [segment(scene), scene.place], showHeadcount, headcount.key);
  sceneChips($("place-scenes"), scenes.places, (scene) => [segment(scene), scene.place], showPlace, place.key);
}

function showPrefs() {
  for (const button of $("lang-seg").children) button.setAttribute("aria-checked", String(button.dataset.lang === LANG));
  $("theme-toggle").textContent = document.documentElement.dataset.theme === "dark" ? "☀" : "☾";
}
for (const button of $("lang-seg").children) {
  button.addEventListener("click", () => { setLang(button.dataset.lang); showPrefs(); drawPage(); if (scenes) drawScenes(); });
}
$("theme-toggle").addEventListener("click", () => { toggleTheme(); showPrefs(); });
translatePage();
showPrefs();
drawPage();

fetch("/api/scenarios").then((response) => response.json()).then((data) => {
  scenes = data;
  drawScenes();
}).catch((error) => {
  document.querySelector("main").prepend(el("p", { class: "error" }, t("sc_failed", error.message)));
});
