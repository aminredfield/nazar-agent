"use strict";
// NAZAR Agent — importing employees: a list (a photo of a notebook, Excel,
// CSV, text) becomes rows checked against NAZAR, the rows become people
// waiting for a photo, and the shooting queue walks along the brigade.
// Uses app.js (el, api, upload, loadAudit), media.js (photoZone) and i18n.js (t).

function showTab(name) {
  for (const tab of ["ask", "import"]) {
    $(`tab-${tab}`).hidden = tab !== name;
    $(`tab-${tab}-button`).setAttribute("aria-selected", String(tab === name));
  }
  if (name === "import") loadWaiting();
}
$("tab-ask-button").addEventListener("click", () => showTab("ask"));
$("tab-import-button").addEventListener("click", () => showTab("import"));

// The list to read: a photo of a notebook, an Excel or CSV file, from the zone.
let importFile = null;
function chooseImportFile(file) {
  importFile = file;
  if (!file) { $("import-chosen").replaceChildren(); return; }
  const drop = el("button", { type: "button", class: "small", "aria-label": t("remove") }, "✕");
  drop.addEventListener("click", () => chooseImportFile(null));
  $("import-chosen").replaceChildren(el("span", { class: "chip" }, t("zone_file", file.name), drop));
}
$("import-zone").append(photoZone({
  accept: "image/jpeg,image/png,image/webp,.xlsx,.csv,.txt", hint: "import_file", onFile: chooseImportFile,
}));

$("import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData();
  if ($("import-text").value.trim()) form.append("text", $("import-text").value);
  if (importFile) form.append("file", importFile);
  form.append("lang", LANG);
  $("import-read").disabled = true;
  $("import-status").className = "status";
  $("import-status").textContent = t("import_reading");
  try {
    renderReview(await upload("/api/import/read", form));
    $("import-status").textContent = "";
  } catch (error) {
    $("import-status").className = "status error"; $("import-status").textContent = error.message;
  } finally {
    $("import-read").disabled = false;
  }
});

function renderReview(data) {
  const siteSelect = (selected) => el("select", {},
    el("option", { value: "" }, t("no_site_option")),
    data.sites.map((site) => {
      const option = el("option", { value: site.id }, site.name);
      if (site.id === selected) option.selected = true;
      return option;
    }));
  const rows = data.rows.map((row) => {
    const include = el("input", { type: "checkbox" }); include.checked = true;
    const name = el("input", { type: "text", maxlength: "120" }); name.value = row.full_name;
    const site = siteSelect(row.site_id);
    const tr = el("tr", { class: row.issues.length ? "attention" : "" },
      el("td", {}, include), el("td", {}, name, el("div", { class: "muted" }, row.as_written)),
      el("td", {}, site, row.site_hint ? el("div", { class: "muted" }, t("in_list", row.site_hint)) : null),
      el("td", {}, row.issues.map((issue) => el("div", { class: "issue" }, issue))));
    return { tr, include, name, site };
  });
  const attention = data.rows.filter((row) => row.issues.length).length;
  const consent = el("input", { type: "date" });
  const create = el("button", { class: "primary", type: "button" }, t("create_drafts"));
  const result = el("div", { class: "status" });
  create.onclick = async () => {
    const chosen = rows.filter((row) => row.include.checked && row.name.value.trim())
      .map((row) => ({ full_name: row.name.value.trim(), site_id: row.site.value || null }));
    if (!chosen.length) return;
    create.disabled = true;
    try {
      await api("/api/import/drafts", { actor: $("actor").value, rows: chosen, consent_signed_at: consent.value || null });
      $("import-review").replaceChildren(el("p", { class: "done" }, t("added", chosen.length)));
      loadWaiting(); loadAudit();
    } catch (error) {
      result.className = "status error"; result.textContent = error.message; create.disabled = false;
    }
  };
  $("import-review").replaceChildren(el("section", { class: "panel glass" },
    el("p", {}, `${t("found_rows", data.rows.length)} ${attention ? t("attention_rows", attention) : t("all_sorted")}`),
    el("table", { class: "people" },
      el("tr", {}, el("th", {}, ""), el("th", {}, t("th_name")), el("th", {}, t("th_site")), el("th", {}, t("th_check"))),
      rows.map((row) => row.tr)),
    el("div", { class: "row section" },
      el("label", { class: "muted" }, t("consent"), consent),
      create),
    result));
}

let waiting = [];

async function loadWaiting() {
  try {
    waiting = await api("/api/import/drafts");
    $("waiting").replaceChildren(...waiting.map((draft) => {
      const pick = photoZone({ compact: true, onFile: (file) => photograph(draft, file, null) });
      const remove = el("button", { type: "button" }, t("remove"));
      remove.onclick = async () => { await api(`/api/import/drafts/${draft.id}/discard`, { actor: $("actor").value }); loadWaiting(); loadAudit(); };
      return el("li", { class: "row" }, `${draft.full_name} · ${draft.site_name || t("no_site")} `, pick, remove);
    }));
    if (!waiting.length) $("waiting").replaceChildren(el("li", { class: "muted" }, t("nobody_waiting")));
  } catch (error) {
    $("waiting").replaceChildren(el("li", { class: "error" }, error.message));
  }
}

async function photograph(draft, file, status) {
  const form = new FormData();
  form.append("actor", $("actor").value);
  form.append("photo", file);
  const note = status || $("import-status");
  note.className = "status"; note.textContent = t("checking_photo", draft.full_name);
  try {
    await upload(`/api/import/drafts/${draft.id}/photo`, form);
    note.className = "status done"; note.textContent = t("created", draft.full_name);
    await loadWaiting(); loadAudit();
    return true;
  } catch (error) {
    note.className = "status error"; note.textContent = error.message;
    return false;
  }
}

// The shooting queue: on a phone the foreman walks along the brigade —
// "Next: Ivanov Alisher" → photo → NAZAR checks it → next.
function shootNext(skipped = new Set()) {
  const next = waiting.find((draft) => !skipped.has(draft.id));
  if (!next) {
    const close = el("button", { type: "button" }, t("close"));
    close.onclick = () => $("shooting").replaceChildren();
    $("shooting").replaceChildren(el("div", { class: "shoot" }, el("div", { class: "next" }, t("all_shot")), close));
    return;
  }
  const status = el("div", { class: "status" });
  const camera = photoZone({ onFile: async (file) => { if (await photograph(next, file, status)) shootNext(skipped); } });
  const skip = el("button", { type: "button" }, t("skip"));
  const stop = el("button", { type: "button" }, t("finish"));
  skip.onclick = () => { skipped.add(next.id); shootNext(skipped); };
  stop.onclick = () => $("shooting").replaceChildren();
  $("shooting").replaceChildren(el("div", { class: "shoot" },
    el("div", { class: "muted" }, t("left", waiting.filter((draft) => !skipped.has(draft.id)).length)),
    el("div", { class: "next" }, t("next", next.full_name)),
    el("div", { class: "muted" }, next.site_name || t("no_site")),
    camera,
    status, el("div", { class: "row", style: "justify-content:center" }, skip, stop)));
}
$("start-shooting").addEventListener("click", async () => { await loadWaiting(); shootNext(); });
$("refresh-waiting").addEventListener("click", loadWaiting);
