"use strict";
// NAZAR Agent — the employee's cabinet in the "Ask before you fine" scene. The agent's
// question arrives here; the employee answers by a tap or in their own words. The
// employee never sees the verdict: that goes to the owner's window.
// Uses i18n.js (t, L, LANG, setLang, translatePage).

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const scene = params.get("scene") || "cafe-late";
const embedded = params.get("embed") === "1";
const POLL_MS = 1500;
let shown = null; // the conversation as last drawn, to redraw only when it changes
let answering = false; // the employee chose to answer again

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

async function read() {
  const response = await fetch(`/api/scenarios/inbox?scene=${encodeURIComponent(scene)}`);
  if (!response.ok) throw new Error(t("cab_failed"));
  return response.json();
}

function draw(data) {
  shown = data;
  $("cab-me").replaceChildren(el("span", { class: "face initials" }, data.employee.split(" ").map((part) => part[0]).join("")),
    el("div", {}, el("b", {}, data.employee), el("small", {}, data.place)));
  if (!data.message) {
    $("cab-thread").replaceChildren(el("p", { class: "muted cab-empty" }, t("cab_empty")));
    $("cab-answer").hidden = true;
    return;
  }
  $("cab-thread").replaceChildren(...[
    el("div", { class: "sc-bubble from-agent" }, el("small", {}, t("cab_from")), data.message),
    data.reply ? el("div", { class: "sc-bubble from-person" }, el("small", {}, t("cab_you")), data.reply) : null,
    data.reply ? el("p", { class: "muted cab-sent" }, t("cab_sent")) : null,
    data.reply && !answering ? changeButton() : null,
  ].filter(Boolean));
  $("cab-answer").hidden = Boolean(data.reply) && !answering;
  $("cab-quick").replaceChildren(...data.replies.map((reply) => {
    const said = reply.text[LANG] || reply.text.en;
    const pick = el("button", { type: "button", class: "sc-template" }, el("span", {}, said),
      // Inside the scene's page the visitor plays the employee: say where each answer leads.
      embedded ? el("small", {}, t("sc_expected", t(`sc_v_${reply.expect}`).slice(2))) : null);
    pick.addEventListener("click", () => answer(said));
    return pick;
  }));
}

function changeButton() {
  const again = el("button", { type: "button", class: "ghost small" }, t("cab_change"));
  again.addEventListener("click", () => { answering = true; draw(shown); });
  return again;
}

async function answer(said) {
  if (!said.trim()) return;
  for (const button of document.querySelectorAll("#cab-answer button")) button.disabled = true;
  $("cab-error").hidden = true;
  $("cab-thread").append(el("div", { class: "sc-bubble from-person" }, el("small", {}, t("cab_you")), said),
    el("p", { class: "muted sc-wait" }, t("cab_sending")));
  $("cab-answer").hidden = true;
  try {
    const response = await fetch("/api/scenarios/inbox/reply", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scene, said }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : t("cab_failed"));
    answering = false;
    $("cab-text").value = "";
    draw(await read());
  } catch (error) {
    $("cab-error").textContent = error.message;
    $("cab-error").hidden = false;
    $("cab-answer").hidden = false;
  } finally {
    for (const button of document.querySelectorAll("#cab-answer button")) button.disabled = false;
  }
}

$("cab-form").addEventListener("submit", (event) => { event.preventDefault(); answer($("cab-text").value); });

// The question comes from the owner's window, maybe in another tab: look for it now and then.
async function poll() {
  try {
    const data = await read();
    const changed = !shown || data.message !== shown.message || data.reply !== shown.reply;
    if (changed && !answering) draw(data);
  } catch {
    // The next look tries again.
  }
}

function showPrefs() {
  for (const button of $("lang-seg").children) button.setAttribute("aria-checked", String(button.dataset.lang === LANG));
  document.title = `NAZAR · ${t("cab_title")}`;
}
for (const button of $("lang-seg").children) {
  button.addEventListener("click", () => { setLang(button.dataset.lang); showPrefs(); if (shown) draw(shown); });
}
// The language switched on the scene's page (or in another tab) reaches the cabinet too:
// its ready answers were left in English when the page around them turned Russian.
window.addEventListener("storage", (event) => {
  if (event.key !== "lang" || event.newValue === LANG) return;
  setLang(event.newValue);
  showPrefs();
  if (shown) draw(shown);
});
document.body.classList.toggle("cab-embedded", embedded);
// Inside the scene's page the frame grows with the conversation, so there is one scroll, the page's.
if (embedded) {
  new ResizeObserver(() => parent.postMessage({ cabinetHeight: document.documentElement.scrollHeight }, location.origin))
    .observe(document.body);
}
translatePage();
showPrefs();
poll();
setInterval(poll, POLL_MS);
