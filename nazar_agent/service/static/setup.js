"use strict";
// NAZAR Agent — setting the company up through the chat: the draft the team
// read from the owner's words, as cards to keep, change or skip; the plan of
// changes in words; "Apply all" and "Undo all". Every change goes to the
// agent, which checks it against NAZAR and sends the cards back: the page
// keeps no setup state of its own. The map is Leaflet over OpenStreetMap,
// loaded only when a site card needs it.
// Uses el, api (app.js) and t, L (i18n.js).

const TASHKENT = [41.3111, 69.2797];
const LEAFLET = "https://unpkg.com/leaflet@1.9.4/dist/leaflet";
const setupState = { view: null, run: null, node: null, busy: false };
let leafletLoaded = null;
let mapParts = null; // { node, map, marker, circle } — one map, moved into each new site card

async function send(method, path, body) {
  const response = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : t("error_status", response.status));
  return data;
}

// The setup block of an answer. `live` — the latest answer: its cards can be changed.
function setupSection(view, live) {
  if (!live) {
    return el("div", { class: "setup past" }, el("ol", { class: "plan" }, view.steps.map((step) => el("li", {}, step.text))));
  }
  setupState.view = view;
  setupState.run = null;
  setupState.node = el("section", { class: "setup" });
  drawSetup();
  refreshSetup(); // the draft may have changed since this answer: show it as it is now
  return setupState.node;
}

async function refreshSetup() {
  try {
    const now = await api("/api/setup");
    setupState.view = now.setup;
    setupState.run = now.run;
    drawSetup();
  } catch (error) {
    say(error.message, "error");
  }
}

async function changeSetup(part) {
  if (!setupState.view || setupState.busy) return;
  setupState.busy = true;
  try {
    const now = await send("PATCH", "/api/setup", { draft_id: setupState.view.draft.id, ...part });
    setupState.view = now.setup;
    say("");
  } catch (error) {
    say(error.message, "error");
  } finally {
    setupState.busy = false;
    drawSetup();
  }
}

function drawSetup() {
  const node = setupState.node;
  if (!node) return;
  const view = setupState.view;
  const parts = [];
  if (view) {
    const notes = [view.link_problem, view.unknown_schedules].filter(Boolean)
      .concat(view.missing.map((item) => item.text));
    parts.push(el("h3", { class: "lead" }, t("setup_title")));
    if (notes.length) parts.push(el("ul", { class: "setup-notes" }, notes.map((note) => el("li", {}, note))));
    parts.push(el("div", { class: "setup-cards" }, siteCard(view), shiftCard(view), groupCard(view), peopleCard(view)));
    parts.push(planBlock(view));
  }
  if (setupState.run) parts.push(runBlock(setupState.run));
  node.replaceChildren(...parts);
  if (view && !view.draft.site.existing_id && view.draft.site.state === "keep") placeMap(view);
}

// ---- Cards -------------------------------------------------------------------

function card(title, state, onToggle, ...body) {
  const skipped = state === "skip";
  const toggle = el("button", { type: "button", class: "small ghost" }, skipped ? t("setup_back") : t("setup_skip"));
  toggle.addEventListener("click", onToggle);
  return el("div", { class: `setup-card${skipped ? " skipped" : ""}` },
    el("div", { class: "row setup-card-head" }, el("h4", {}, title), skipped ? el("span", { class: "muted" }, t("setup_skipped")) : null, toggle),
    skipped ? null : body);
}

function field(label, input) {
  return el("label", { class: "setup-field" }, el("span", { class: "muted" }, label), input);
}

function input(value, onChange, attrs = {}) {
  const node = el("input", { type: "text", ...attrs });
  node.value = value ?? "";
  node.addEventListener("change", () => onChange(node.value));
  return node;
}

function choose(options, selected, onChange, first) {
  const node = el("select", {}, el("option", { value: "" }, first),
    options.map((item) => {
      const option = el("option", { value: item.id }, item.name);
      if (item.id === selected) option.selected = true;
      return option;
    }));
  node.addEventListener("change", () => onChange(node.value));
  return node;
}

function siteCard(view) {
  const site = view.draft.site;
  const toggle = () => changeSetup({ site: { state: site.state === "skip" ? "keep" : "skip" } });
  const existing = field(t("setup_site_existing"), choose(view.sites, site.existing_id, (id) => changeSetup({ site: { existing_id: id } }), t("setup_site_new")));
  if (site.existing_id) return card(t("setup_site"), site.state, toggle, existing);

  const link = el("input", { type: "text", placeholder: t("setup_link_placeholder") });
  const take = el("button", { type: "button", class: "small" }, t("setup_link_take"));
  take.addEventListener("click", () => pasteLink(link.value));
  link.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); pasteLink(link.value); } });
  const here = el("button", { type: "button", class: "small" }, t("setup_here"));
  here.addEventListener("click", iAmHere);
  const point = site.latitude !== null && site.latitude !== undefined
    ? t("setup_point", site.latitude.toFixed(5), site.longitude.toFixed(5)) : t("setup_no_point");
  return card(t("setup_site"), site.state, toggle,
    existing,
    field(t("setup_site_name"), input(site.name, (value) => changeSetup({ site: { name: value } }), { maxlength: "255" })),
    field(t("setup_site_address"), input(site.address, (value) => changeSetup({ site: { address: value } }), { maxlength: "500" })),
    el("div", { class: "setup-map-slot" }),
    el("div", { class: `muted${site.latitude === null ? " warn" : ""}` }, point),
    el("div", { class: "row" }, here, link, take),
    field(t("setup_radius"), input(String(site.radius_meters), (value) => changeSetup({ site: { radius_meters: Number(value) } }), { type: "number", min: "11", max: "1000", step: "10" })));
}

function shiftCard(view) {
  const shift = view.draft.shift;
  const toggle = () => changeSetup({ shift: { state: shift.state === "skip" ? "keep" : "skip" } });
  const time = (value, key) => input(value, (next) => next && changeSetup({ shift: { [key]: next } }), { type: "time" });
  const days = el("div", { class: "seg days" }, [1, 2, 3, 4, 5, 6, 7].map((day) => {
    const on = shift.days.includes(day);
    const button = el("button", { type: "button", "aria-pressed": String(on) }, L().WEEKDAYS[day]);
    button.addEventListener("click", () => {
      const next = on ? shift.days.filter((item) => item !== day) : [...shift.days, day];
      if (next.length) changeSetup({ shift: { days: next } });
    });
    return button;
  }));
  const preview = view.preview.length ? el("ul", { class: "setup-preview" }, view.preview.map((day) => el("li", { class: day.working ? "" : "off" },
    `${day.date.slice(8, 10)}.${day.date.slice(5, 7)} ${L().WEEKDAYS[new Date(`${day.date}T12:00:00`).getDay() || 7]} · `,
    day.working ? `${day.from}–${day.to}` : t("setup_day_off")))) : null;
  return card(t("setup_shift"), shift.state, toggle,
    el("div", { class: "row" }, field(t("setup_from"), time(shift.start, "start")), field(t("setup_to"), time(shift.end, "end"))),
    days, preview ? el("div", { class: "muted" }, t("setup_preview")) : null, preview);
}

function groupCard(view) {
  const group = view.draft.group;
  const toggle = () => changeSetup({ group: { state: group.state === "skip" ? "keep" : "skip" } });
  return card(t("setup_group"), group.state, toggle,
    field(t("setup_group_existing"), choose(view.groups, group.existing_id, (id) => changeSetup({ group: id ? { existing_id: id } : { name: group.name || t("setup_group") } }), t("setup_group_new"))),
    group.existing_id ? null : field(t("setup_group_name"), input(group.name, (value) => value.trim() && changeSetup({ group: { name: value } }), { maxlength: "100" })));
}

function peopleCard(view) {
  const clashes = Object.fromEntries(view.clashes.map((item) => [item.employee_id, item]));
  const rows = view.draft.people.map((person) => personRow(person, clashes[person.employee_id]));
  const name = el("input", { type: "text", list: "setup-names", placeholder: t("setup_add_placeholder"), maxlength: "200" });
  const add = el("button", { type: "button", class: "small" }, t("setup_add"));
  const addName = () => { if (name.value.trim().length > 1) changeSetup({ add_people: [name.value.trim()] }); };
  add.addEventListener("click", addName);
  name.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); addName(); } });
  return el("div", { class: "setup-card people" },
    el("div", { class: "row setup-card-head" }, el("h4", {}, t("setup_people"))),
    rows.length ? el("ul", { class: "setup-people" }, rows) : el("p", { class: "muted" }, t("setup_nobody")),
    el("div", { class: "row" }, name, add),
    el("datalist", { id: "setup-names" }, view.employees.map((item) => el("option", { value: item.name }))));
}

function personRow(person, clash) {
  const skipped = person.state === "skip";
  const label = person.state === "new" ? t("setup_person_new") : skipped ? t("setup_person_skip") : t("setup_person_add");
  const toggle = el("button", { type: "button", class: "small ghost" }, skipped ? t("setup_back") : t("setup_person_leave"));
  toggle.addEventListener("click", () => changeSetup({
    person: { written: person.written, state: skipped ? (person.employee_id ? "add" : "new") : "skip" },
  }));
  const choices = person.choices.length && !skipped ? choose(person.choices.map(([id, name]) => ({ id, name })), "",
    (id) => id && changeSetup({ person: { written: person.written, employee_id: id } }), t("setup_choose")) : null;
  // Leaving the person out is the row's own button; here only the shifts that clash with nothing.
  const ways = clash && !skipped ? el("div", { class: "row" }, clash.options.filter((option) => option.kind === "shift").map((option) => {
    const button = el("button", { type: "button", class: "small" }, t("setup_option_shift", option.shift.start, option.shift.end));
    button.addEventListener("click", () => changeSetup({ shift: { start: option.shift.start, end: option.shift.end } }));
    return button;
  })) : null;
  return el("li", { class: skipped ? "skipped" : "" },
    el("div", { class: "row" }, el("b", {}, person.full_name || person.written), el("span", { class: `chip ${person.state}` }, label), choices, toggle),
    clash && !skipped ? el("div", { class: "issue" }, clash.text) : null, ways);
}

// ---- The plan --------------------------------------------------------------------

function planBlock(view) {
  const apply = el("button", { type: "button", class: "primary" }, t("setup_apply"));
  apply.disabled = !view.ready;
  const discard = el("button", { type: "button" }, t("setup_discard"));
  apply.addEventListener("click", async () => {
    apply.disabled = true; discard.disabled = true;
    say(t("setup_applying"));
    try {
      const now = await send("POST", "/api/setup/apply", { draft_id: view.draft.id, actor: $("actor").value });
      setupState.view = now.setup;
      setupState.run = now.run;
      say(now.run.status === "applied" ? t("setup_applied") : t("setup_failed", now.run.error || ""), now.run.status === "applied" ? "done" : "error");
    } catch (error) {
      say(error.message, "error");
    }
    drawSetup();
    loadAudit();
  });
  discard.addEventListener("click", async () => {
    await send("POST", "/api/setup/discard", {}).catch(() => undefined);
    setupState.view = null;
    say(t("setup_discarded"));
    drawSetup();
  });
  return el("div", { class: "setup-plan" },
    el("h3", { class: "lead" }, t("setup_plan")),
    view.steps.length ? el("ol", { class: "plan" }, view.steps.map((step) => el("li", {}, step.text))) : el("p", { class: "muted" }, t("setup_no_steps")),
    el("div", { class: "row" }, apply, discard));
}

function runBlock(run) {
  const undo = el("button", { type: "button" }, t("setup_undo"));
  undo.disabled = run.status === "undone" || run.status === "undoing";
  undo.addEventListener("click", async () => {
    undo.disabled = true;
    try {
      const now = await send("POST", `/api/setup/runs/${run.id}/undo`, { actor: $("actor").value });
      setupState.run = now.run;
      say(now.run.error ? t("setup_undo_left", now.run.error) : t("setup_undone"), now.run.error ? "error" : "done");
    } catch (error) {
      say(error.message, "error");
    }
    drawSetup();
    loadAudit();
    if (!$("tab-import").hidden) loadWaiting();
  });
  return el("div", { class: `setup-run ${run.status}` },
    el("h3", { class: "lead" }, t(`setup_run_${run.status}`)),
    el("ol", { class: "plan" }, run.steps.map((step) => el("li", { class: step.undone ? "undone" : step.done ? "done" : "" }, step.text))),
    run.error ? el("p", { class: "issue" }, run.error) : null,
    run.status === "undone" ? null : el("div", { class: "row" }, undo));
}

// ---- The point: the map, "I am here", a pasted link ------------------------------

async function pasteLink(link) {
  if (!link.trim() || !setupState.view) return;
  try {
    const now = await send("POST", "/api/setup/link", { draft_id: setupState.view.draft.id, url: link.trim() });
    setupState.view = now.setup;
    say("");
  } catch (error) {
    say(error.message, "error");
  }
  drawSetup();
}

function iAmHere() {
  if (!navigator.geolocation) { say(t("setup_here_denied"), "error"); return; }
  navigator.geolocation.getCurrentPosition(
    (position) => changeSetup({ site: { latitude: position.coords.latitude, longitude: position.coords.longitude, point_source: "here" } }),
    () => say(t("setup_here_denied"), "error"),
    { enableHighAccuracy: true, timeout: 15000 },
  );
}

function loadLeaflet() {
  if (!leafletLoaded) {
    leafletLoaded = new Promise((resolve, reject) => {
      document.head.append(el("link", { rel: "stylesheet", href: `${LEAFLET}.css` }));
      const script = el("script", { src: `${LEAFLET}.js` });
      script.addEventListener("load", () => resolve(window.L));
      script.addEventListener("error", () => { leafletLoaded = null; reject(new Error("map")); });
      document.head.append(script);
    });
  }
  return leafletLoaded;
}

async function placeMap(view) {
  const slot = setupState.node.querySelector(".setup-map-slot");
  if (!slot) return;
  let leaflet;
  try {
    leaflet = await loadLeaflet();
  } catch {
    slot.replaceChildren(el("p", { class: "muted" }, t("setup_map_off")));
    return;
  }
  if (!slot.isConnected) return;
  const site = view.draft.site;
  const known = view.sites.find((item) => item.latitude !== null);
  const point = site.latitude !== null ? [site.latitude, site.longitude] : known ? [known.latitude, known.longitude] : TASHKENT;
  if (!mapParts) {
    const node = el("div", { class: "setup-map" });
    const map = leaflet.map(node).setView(point, 15);
    leaflet.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "&copy; OpenStreetMap",
    }).addTo(map);
    map.on("click", (event) => changeSetup({ site: { latitude: event.latlng.lat, longitude: event.latlng.lng, point_source: "map" } }));
    mapParts = { node, map, marker: null, circle: null };
  }
  slot.replaceChildren(mapParts.node);
  const { map } = mapParts;
  map.invalidateSize();
  if (mapParts.marker) { mapParts.marker.remove(); mapParts.circle.remove(); mapParts.marker = null; }
  if (site.latitude !== null) {
    mapParts.marker = leaflet.marker(point, { draggable: true }).addTo(map);
    mapParts.circle = leaflet.circle(point, { radius: site.radius_meters, color: "#7c3aed", weight: 1 }).addTo(map);
    mapParts.marker.on("dragend", () => {
      const moved = mapParts.marker.getLatLng();
      changeSetup({ site: { latitude: moved.lat, longitude: moved.lng, point_source: "map" } });
    });
    map.setView(point, Math.max(map.getZoom(), 15));
  }
}
