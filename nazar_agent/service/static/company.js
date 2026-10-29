"use strict";
// NAZAR Agent — the Company tab: the company as NAZAR keeps it — sites and
// their shifts, groups, people, a week of the timesheet, today's check-ins.
// Read-only. What changed since the tab was last shown (an applied action, a
// site set up, people added) is marked, so "Apply" is something to see.
// Uses app.js ($, el, api) and i18n.js (t, L, LANG).

// Its own scope: setup.js has helpers of the same names (siteCard…); only what
// the other scripts call goes on window, at the end.
(() => {
  let companySeen = null; // key → what it looked like the last time the tab was shown

  async function loadCompany() {
    try {
      const data = await api("/api/company");
      const now = signatures(data);
      renderCompany(data, companySeen || now);
      companySeen = now;
    } catch (error) {
      $("company").replaceChildren(el("p", { class: "error" }, error.message));
    }
  }
  $("refresh-company").addEventListener("click", loadCompany);

  // The company as the window opened, so the first look after an "Apply" already marks it.
  async function companyBaseline() {
    try {
      companySeen = companySeen || signatures(await api("/api/company"));
    } catch {
      // Not reachable now: the tab says why when it is opened.
    }
  }

  // After "Apply" or "Undo": the tab, if open, shows the change at once.
  function companyChanged() {
    if (!$("tab-company").hidden) loadCompany();
  }

  // Next to a done action: where to see what it changed.
  function seeCompany() {
    const go = el("button", { type: "button", class: "ghost" }, t("see_company"));
    go.addEventListener("click", () => { showTab("company"); window.scrollTo({ top: $("tab-company").offsetTop - 80 }); });
    return go;
  }

  function signatures(data) {
    const seen = {};
    for (const site of data.sites) seen[`site:${site.id}`] = JSON.stringify(site);
    for (const group of data.groups) seen[`group:${group.id}`] = JSON.stringify(group);
    for (const person of data.people) seen[`person:${person.id}`] = JSON.stringify([person.name, person.sites, person.groups]);
    for (const row of data.week.rows) seen[`week:${row.id}`] = JSON.stringify(row.days);
    for (const item of data.check_ins) seen[`check:${item.id}`] = JSON.stringify([item.review, item.flags]);
    return seen;
  }

  // "new" — not there before; "changed" — there, but different.
  function mark(before, key, now) {
    if (!(key in before)) return Object.keys(before).length ? "new" : "";
    return before[key] === now ? "" : "changed";
  }
  // Classes apart from the before/after table's own ".changed".
  const marked = (state) => (state ? `mark-${state}` : "");
  const badge = (state) => (state ? el("span", { class: `badge ${marked(state)}` }, t(`company_${state}`)) : null);
  const days = (list) => (list || []).map((day) => L().WEEKDAYS[day]).join(", ");
  const shortDate = (iso) => `${L().WEEKDAYS[new Date(`${iso}T12:00:00`).getDay() || 7]} ${iso.slice(8, 10)}`;
  const hours = (minutes) => (minutes / 60).toFixed(1).replace(".0", "");

  function renderCompany(data, before) {
    const now = signatures(data);
    const state = (key) => mark(before, key, now[key]);
    $("company-asof").textContent = t("company_asof", data.date, data.now);
    $("company").replaceChildren(
      companySection("company_sites", el("div", { class: "company-sites" }, data.sites.map((site) => siteCard(site, state(`site:${site.id}`))))),
      companySection("company_groups", data.groups.length
        ? el("ul", { class: "plain company-groups" }, data.groups.map((group) => groupItem(group, state(`group:${group.id}`))))
        : el("p", { class: "muted" }, t("company_no_groups"))),
      companySection("company_people", peopleTable(data.people, (id) => state(`person:${id}`))),
      companySection("company_week", weekTable(data.week, (id) => state(`week:${id}`))),
      companySection("company_checkins", checkIns(data.check_ins, (id) => state(`check:${id}`))),
    );
  }

  function companySection(title, body) {
    return el("section", { class: "panel glass company-part" }, el("h2", {}, t(title)), body);
  }

  function siteCard(site, state) {
    const map = `https://www.openstreetmap.org/?mlat=${site.latitude}&mlon=${site.longitude}#map=17/${site.latitude}/${site.longitude}`;
    const shift = site.shift_start ? `${t("company_shift", site.shift_start, site.shift_end)} · ${days(site.working_days)}` : t("company_no_shift");
    return el("article", { class: `site-card ${marked(state)}` },
      el("div", { class: "row" }, el("b", {}, site.name), badge(state)),
      el("div", { class: "muted" }, site.address, " · ", el("a", { href: map, target: "_blank", rel: "noopener" }, t("company_map"))),
      el("div", { class: "shift" }, shift),
      site.late_grace_minutes != null ? el("div", { class: "muted small" }, t("company_grace", site.late_grace_minutes)) : null,
      site.groups.map((group) => el("div", { class: "small" }, group.shift_start
        ? t("company_group_shift", group.name, group.shift_start, group.shift_end) : group.name)),
      el("div", { class: "small people-list" }, site.people.length ? `${site.people.length}: ${site.people.join(", ")}` : t("company_nobody")));
  }

  function groupItem(group, state) {
    return el("li", { class: marked(state) },
      el("span", { class: `dot-color ${group.color || "blue"}` }), el("b", {}, group.name), " ", badge(state),
      el("span", { class: "muted" }, ` · ${group.members.length}: ${group.members.join(", ") || t("company_nobody")}`));
  }

  function today(person) {
    const status = person.status ? L().STATUS[person.status] || person.status : "";
    const late = person.late_minutes ? ` · ${t("company_late", person.late_minutes)}` : "";
    return `${status}${person.first_check_in ? ` ${person.first_check_in}` : ""}${late}`;
  }

  function peopleTable(people, state) {
    return el("div", { class: "table-wrap" }, el("table", { class: "people company-table" },
      el("tr", {}, ["th_name", "th_code", "th_sites", "th_groups", "th_today"].map((key) => el("th", {}, t(key)))),
      people.map((person) => {
        const changed = state(person.id);
        return el("tr", { class: marked(changed) },
          el("td", {}, person.name, " ", badge(changed)), el("td", { class: "muted" }, person.code || ""),
          el("td", {}, person.sites.join(", ")), el("td", {}, person.groups.join(", ")),
          el("td", { class: person.late_minutes ? "late" : "" }, today(person)));
      })));
  }

  function weekCell(cell) {
    if (cell.worked_minutes) {
      const text = `${hours(cell.worked_minutes)}${cell.adjusted ? " ✎" : ""}`;
      return el("td", { class: `num ${cell.late_minutes ? "late" : ""}`, title: cell.late_minutes ? t("company_late", cell.late_minutes) : "" }, text);
    }
    if (cell.open) return el("td", { class: "num muted" }, t("company_open"));
    if (cell.code === "absent") return el("td", { class: "num absent" }, t("company_absent"));
    return el("td", { class: "num muted" }, cell.adjusted ? "✎" : "·");
  }

  function weekTable(week, state) {
    return el("div", {},
      el("div", { class: "table-wrap" }, el("table", { class: "company-table week" },
        el("tr", {}, el("th", {}, t("th_name")), week.dates.map((day) => el("th", { class: "num" }, shortDate(day))),
          el("th", { class: "num" }, t("th_hours")), el("th", { class: "num" }, t("th_late_days"))),
        week.rows.map((row) => {
          const changed = state(row.id);
          return el("tr", { class: marked(changed) }, el("td", {}, row.name, " ", badge(changed)), row.days.map(weekCell),
            el("td", { class: "num" }, hours(row.worked_minutes)), el("td", { class: "num" }, row.late_days || ""));
        }))),
      el("p", { class: "muted small" }, t("company_legend")));
  }

  function checkIns(items, state) {
    if (!items.length) return el("p", { class: "muted" }, t("company_no_checkins"));
    return el("ol", { class: "plain company-checkins" }, items.map((item) => {
      const changed = state(item.id);
      const review = item.review && item.review !== "not_reviewed" ? L().REVIEW[item.review] || item.review : "";
      return el("li", { class: marked(changed) },
        el("span", { class: "mono" }, item.time), " ", el("b", {}, item.name), ` · ${item.site} · ${t(item.type === "check_in" ? "company_in" : "company_out")}`,
        item.flags.length ? el("span", { class: "issue" }, ` · ${item.flags.join(", ").replaceAll("_", " ")}`) : null,
        review ? el("span", { class: "reviewed" }, ` · ${review}`) : null, " ", badge(changed));
    }));
  }

  Object.assign(window, { loadCompany, companyBaseline, companyChanged, seeCompany });
})();
