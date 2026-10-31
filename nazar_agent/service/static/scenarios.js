"use strict";
// NAZAR Agent — the Scenarios page: the new abilities played on staged scenes.
// The visitor plays the other side: answers as the employee, picks the photo,
// or brings their own picture. Each call is one model call, timed on screen.

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

// A button that waits for the model: says so, shows how long it took, never runs twice at once.
async function working(button, place, run) {
  if (button.disabled) return;
  const label = button.textContent;
  const started = performance.now();
  button.disabled = true;
  button.textContent = "Thinking…";
  place.replaceChildren(el("p", { class: "muted" }, "Muse Spark is looking…"));
  try {
    const nodes = await run();
    const seconds = ((performance.now() - started) / 1000).toFixed(1);
    place.replaceChildren(...nodes.filter(Boolean), el("p", { class: "muted small" }, `Muse Spark 1.3 · ${seconds} s`));
  } catch (error) {
    place.replaceChildren(el("p", { class: "error" }, error.message));
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

function sceneChips(holder, scenes, label, onPick) {
  const chips = Object.entries(scenes).map(([key, scene]) => {
    const chip = el("button", { type: "button", class: "chip sc-chip" }, label(scene));
    chip.addEventListener("click", () => {
      for (const other of holder.children) other.classList.remove("on");
      chip.classList.add("on");
      onPick(key, scene);
    });
    return chip;
  });
  holder.replaceChildren(...chips);
  chips[0].click();
}

const picture = (name) => `/static/scenarios/${name}`;
const VERDICTS = {
  consistent: ["✓ Fits the records", "good"],
  contradicts: ["✗ The records say otherwise", "bad"],
  cannot_tell: ["? The records cannot tell", "warn"],
};

// ---- 1. The employee explains ----------------------------------------------------

const explain = { key: null, scene: null, asked: false };

function showExplain(key, scene) {
  Object.assign(explain, { key, scene, asked: false });
  $("explain-card").replaceChildren(el("article", { class: "sc-card" },
    el("div", { class: "muted small" }, `${scene.segment} · ${scene.place}`),
    el("b", {}, scene.employee),
    el("p", {}, scene.finding),
    el("ul", { class: "small" }, scene.records.map((line) => el("li", {}, line)))));
  $("explain-ask").textContent = `Ask ${scene.employee.split(" ")[0]}`;
  $("explain-chat").replaceChildren(el("p", { class: "muted" }, "The agent's question appears here."));
  $("explain-result").replaceChildren();
  $("explain-templates").replaceChildren(el("span", { class: "muted small" }, "Answer as the employee:"),
    ...scene.replies.map((reply) => {
      const pick = el("button", { type: "button", class: "sc-template" },
        el("span", { class: "sc-lang" }, reply.lang.toUpperCase()), reply.text,
        el("small", { class: "muted" }, `expected: ${VERDICTS[reply.expect][0].slice(2)}`));
      pick.addEventListener("click", () => answer(reply.text));
      return pick;
    }));
}

$("explain-ask").addEventListener("click", () => working($("explain-ask"), $("explain-result"), async () => {
  const data = await send("/api/scenarios/explain/ask", { scene: explain.key, lang: $("explain-lang").value });
  explain.asked = true;
  $("explain-chat").replaceChildren(el("div", { class: "sc-bubble from-agent" }, el("small", {}, "NAZAR assistant"), data.message));
  return [el("p", { class: "muted" }, "Sent. Now answer on the employee's phone →")];
}));

function answer(text) {
  if (!text.trim()) return;
  if (!explain.asked) {
    $("explain-result").replaceChildren(el("p", { class: "error" }, "First ask the employee — the button on the left."));
    return;
  }
  $("explain-chat").append(el("div", { class: "sc-bubble from-person" }, el("small", {}, explain.scene.employee), text));
  $("explain-text").value = "";
  working($("explain-ask"), $("explain-result"), async () => {
    const data = await send("/api/scenarios/explain/check", { scene: explain.key, said: text, lang: "en" });
    const [label, tone] = VERDICTS[data.verdict];
    return [el("div", { class: `sc-verdict ${tone}` }, label), el("p", {}, data.why), el("p", { class: "muted" }, `Next: ${data.next_step}`)];
  });
}
$("explain-form").addEventListener("submit", (event) => { event.preventDefault(); answer($("explain-text").value); });

// ---- 2. A company from one picture -----------------------------------------------

let rosterScene = null;
let rosterOwn = null;

function rosterNodes(data) {
  return [
    el("h3", { class: "lead" }, "Plan of changes"),
    el("ol", { class: "plan" }, data.plan.map((step) => el("li", {}, step))),
    el("details", { class: "fold" }, el("summary", {}, `People read: ${data.people.length}`),
      el("table", { class: "company-table" },
        el("tr", {}, ["Name", "Workplace", "Days", "Shift"].map((head) => el("th", {}, head))),
        data.people.map((person) => el("tr", {}, el("td", {}, person.name), el("td", {}, person.workplace),
          el("td", {}, person.days.join(", ")), el("td", {}, `${person.shift_start}–${person.shift_end}`))))),
    data.unclear.length ? el("p", { class: "issue" }, `Unclear: ${data.unclear.join("; ")}`) : null,
    el("p", { class: "muted small" }, "In the product this plan goes to “Apply all”, as setting up a site in the assistant does."),
  ];
}

$("roster-read").addEventListener("click", () => working($("roster-read"), $("roster-result"), async () => {
  const form = new FormData();
  if (rosterOwn) form.append("file", rosterOwn); else form.append("scene", rosterScene);
  return rosterNodes(await send("/api/scenarios/roster", form));
}));
$("roster-file").addEventListener("change", (event) => {
  rosterOwn = event.target.files[0] || null;
  if (rosterOwn) $("roster-picture").src = URL.createObjectURL(rosterOwn);
});

// ---- 3. Who is on shift ----------------------------------------------------------

let headcountScene = null;
let headcountOwn = null;

$("headcount-count").addEventListener("click", () => working($("headcount-count"), $("headcount-result"), async () => {
  const form = new FormData();
  form.append("scene", headcountScene);
  if (headcountOwn) form.append("file", headcountOwn);
  const data = await send("/api/scenarios/headcount", form);
  const tone = data.in_team === data.checked_in ? "good" : "warn";
  return [
    el("div", { class: `sc-verdict ${tone}` }, data.verdict),
    el("p", {}, `In the photo: ${data.people} ${data.people === 1 ? "person" : "people"}, ${data.in_team} of them on the team.`),
    data.notes ? el("p", { class: "muted" }, data.notes) : null,
  ];
}));
$("headcount-file").addEventListener("change", (event) => {
  headcountOwn = event.target.files[0] || null;
  if (headcountOwn) $("headcount-picture").src = URL.createObjectURL(headcountOwn);
});

// ---- 4. The place behind a check-in ----------------------------------------------

let placeScene = null;
const placeLock = el("button", { type: "button", hidden: "" });  // one comparison at a time

function judgePlace(form) {
  return working(placeLock, $("place-result"), async () => {
    const data = await send("/api/scenarios/place", form);
    return [el("div", { class: `sc-verdict ${data.same_place ? "good" : "bad"}` },
      data.same_place ? "✓ The same place" : "✗ Not this place — though the GPS says it is"), el("p", {}, data.why)];
  });
}

function showPlace(key, scene, gps) {
  placeScene = key;
  $("place-gps").textContent = gps;
  $("place-references").replaceChildren(...scene.references.map((name) => el("img", { src: picture(name), alt: "An earlier check-in", class: "sc-thumb" })));
  $("place-candidates").replaceChildren(...scene.candidates.map((item, index) => {
    const pick = el("button", { type: "button", class: "sc-candidate" },
      el("img", { src: picture(item.file), alt: `${item.who}'s check-in`, class: "sc-thumb" }),
      el("small", {}, item.who), el("small", { class: "muted" }, "GPS ✓ inside the zone"));
    pick.addEventListener("click", () => {
      for (const other of $("place-candidates").children) other.classList.remove("on");
      pick.classList.add("on");
      const form = new FormData();
      form.append("scene", key);
      form.append("candidate", String(index));
      judgePlace(form);
    });
    return pick;
  }));
  $("place-result").replaceChildren();
}
$("place-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("scene", placeScene);
  form.append("file", file);
  judgePlace(form);
});

// ---- The scenes ------------------------------------------------------------------

fetch("/api/scenarios").then((response) => response.json()).then((scenes) => {
  sceneChips($("explain-scenes"), scenes.explain, (scene) => `${scene.segment}: ${scene.employee}`, showExplain);
  sceneChips($("roster-scenes"), scenes.roster, (scene) => scene.title, (key, scene) => {
    rosterScene = key; rosterOwn = null;
    $("roster-picture").src = picture(scene.file);
    $("roster-result").replaceChildren(el("p", { class: "muted" }, scene.note));
  });
  sceneChips($("headcount-scenes"), scenes.headcount, (scene) => `${scene.segment}: ${scene.place}`, (key, scene) => {
    headcountScene = key; headcountOwn = null;
    $("headcount-picture").src = picture(scene.file);
    $("headcount-roster").replaceChildren(el("p", {}, el("b", {}, `Checked in for this shift: ${scene.checked_in.length}`)),
      el("p", { class: "muted small" }, scene.checked_in.join(", ")));
    $("headcount-result").replaceChildren();
  });
  sceneChips($("place-scenes"), scenes.places, (scene) => `${scene.segment}: ${scene.place}`, (key, scene) => showPlace(key, scene, scenes.place_gps));
}).catch((error) => {
  document.querySelector("main").prepend(el("p", { class: "error" }, `The scenes did not load: ${error.message}`));
});
