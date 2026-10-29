"use strict";
// NAZAR Agent — the assistant window. Everything shown comes from the agent's API;
// text from models or NAZAR is always inserted as text, never as markup.

// Words come from i18n.js: t("key") for phrases, L().ACTIONS and the like for names of things.
const number = (value) => Number(value).toLocaleString(LOCALES[LANG]);
const dollars = (value, estimated) => `$${Number(value).toFixed(4)}${estimated ? "*" : ""}`;

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

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : t("error_status", response.status));
  return data;
}

// A form with a file: a photo, a list, a voice message.
async function upload(path, form) {
  const response = await fetch(path, { method: "POST", body: form });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    const message = detail && detail.photo_rejected ? t("photo_rejected", detail.photo_rejected.join("; "))
      : typeof detail === "string" ? detail : t("error_status", response.status);
    throw new Error(message);
  }
  return data;
}

const seconds = (value) => t("seconds", value.toFixed(1).replace(".", LANG === "en" ? "." : ","));

// ---- The team at work ----------------------------------------------------
// While a question is answered the window asks /api/progress for new events
// and shows them: which agent works now, each tool call, what the photo
// inspector saw, the fact checker's rounds, the skeptic's verdicts.

// `id` — the run the events shown belong to; `stale` — the one before, whose events a new run must not show.
const run = { seq: 0, poll: null, clock: null, started: 0, open: {}, analyst: null, active: null, finished: false, inflight: null, id: null, stale: null };

function startRun(question, alreadyFor = 0) {
  Object.assign(run, {
    seq: 0, open: {}, active: null, finished: false, inflight: null, started: performance.now() - alreadyFor * 1000,
    stale: alreadyFor ? null : run.id, id: null,
  });
  $("run").hidden = false;
  $("run").className = "run";
  $("run-title").textContent = t("run_working");
  $("run-question").textContent = question;
  $("feed").replaceChildren();
  for (const node of $("roster").children) node.className = "";
  $("analyst-name").textContent = t("analyst");
  $("team-pill").classList.add("busy");
  $("team-status").textContent = t("team_busy");
  clearInterval(run.clock);
  run.clock = setInterval(() => { $("run-timer").textContent = seconds((performance.now() - run.started) / 1000); }, 100);
  clearInterval(run.poll);
  run.poll = setInterval(pollProgress, 700);
  pollProgress();
}

// One request at a time; an event already shown is never shown twice.
function pollProgress() {
  if (!run.inflight) {
    run.inflight = api(`/api/progress?after=${run.seq}`)
      .then((data) => {
        if (data.run && data.run === run.stale) return data; // the previous question's events: the new run has not begun yet
        if (data.run !== run.id) { run.id = data.run; if (run.seq) { run.seq = 0; return data; } } // a new run: read it from its start
        for (const event of data.events) {
          if (event.seq <= run.seq) continue;
          run.seq = event.seq;
          handle(event);
        }
        return data;
      })
      .catch(() => undefined) // the next tick tries again
      .finally(() => { run.inflight = null; });
  }
  return run.inflight;
}

function stopRun(ok, text) {
  clearInterval(run.poll);
  clearInterval(run.clock);
  run.finished = true;
  setActive(null);
  for (const node of $("roster").children) if (node.className === "active") node.className = "done";
  for (const line of Object.values(run.open).flat()) line.classList.remove("live");
  $("run").className = ok ? "run finished" : "run failed";
  $("run-title").textContent = ok ? t("done_in", $("run-timer").textContent) : t("failed");
  if (text) feed("⚠", text, "err");
  $("team-pill").classList.remove("busy");
  $("team-status").textContent = t("team_idle");
}

function setActive(agent) {
  if (run.active === agent) return;
  const roster = $("roster");
  if (run.active) {
    const previous = roster.querySelector(`[data-agent="${run.active}"]`);
    if (previous) previous.className = "done";
  }
  run.active = agent;
  if (agent) {
    const current = roster.querySelector(`[data-agent="${agent}"]`);
    if (current) current.className = "active";
  }
}

function feed(icon, text, kind = "", sub = null, at = null) {
  const t = at === null ? (performance.now() - run.started) / 1000 : at;
  const line = el("li", { class: kind }, el("span", { class: "t" }, `+${seconds(t)}`), el("span", { class: "ic" }, icon),
    el("span", { class: "tx" }, text, sub ? el("span", { class: "sub" }, sub) : null));
  $("feed").append(line);
  $("feed").scrollTop = $("feed").scrollHeight;
  return line;
}

// A model's own words appear as it would type them.
function typed(icon, text, at) {
  const line = feed(icon, "", "thought", null, at);
  const target = line.querySelector(".tx");
  const caret = el("span", { class: "caret" });
  target.append(caret);
  let shown = 0;
  const step = Math.max(1, Math.round(text.length / 60));
  const timer = setInterval(() => {
    const next = Math.min(text.length, shown + step);
    caret.before(document.createTextNode(text.slice(shown, next)));
    shown = next;
    if (shown >= text.length) { clearInterval(timer); caret.remove(); }
    $("feed").scrollTop = $("feed").scrollHeight;
  }, 18);
}

const day = (iso) => (typeof iso === "string" && /^\d{4}-\d{2}-\d{2}/.test(iso) ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}` : iso);

function argsText(args = {}) {
  const parts = [];
  if (args.date_from) parts.push(`${day(args.date_from)}–${day(args.date_to || args.date_from)}`);
  if (args.work_date) parts.push(t("arg_day", day(args.work_date)));
  if (args.limit) parts.push(t("arg_limit", args.limit));
  if (args.suspicious_only) parts.push(t("arg_flagged"));
  if (args.event_ids) parts.push(t("arg_events", args.event_ids));
  return parts.join(", ");
}

function handle(event) {
  const at = event.t;
  switch (event.kind) {
    case "route": {
      setActive("dispatcher");
      run.analyst = event.analyst;
      $("analyst-name").textContent = L().ANALYSTS[event.analyst] || t("analyst");
      feed("⇢", t("routed", L().ANALYSTS[event.analyst] || event.analyst), "", event.text || null, at);
      break;
    }
    case "reply":
      setActive("dispatcher");
      feed("⇢", t("replied", L().REPLIES[event.intent] || event.intent), "", event.text || null, at);
      break;
    case "thinking":
      setActive("analyst");
      if (event.turn === 1) feed("◆", t("starts", L().ANALYSTS[run.analyst] || t("analyst")), "", null, at);
      break;
    case "note":
      setActive("analyst");
      typed("💭", event.text, at);
      break;
    case "tool": {
      if (event.name !== "inspect_check_in_photos") setActive("analyst");
      const open = run.open[event.name] || (run.open[event.name] = []);
      if (event.status === "start") {
        const details = argsText(event.args);
        open.push(feed("›", L().TOOLS[event.name] || event.name, "live", details || null, at));
      } else {
        const line = open.shift();
        const result = event.status === "error" ? t("tool_failed", event.text)
          : `${event.found !== null && event.found !== undefined ? t("tool_found", event.found) : t("tool_done")} · ${seconds(event.ms / 1000)}`;
        if (line) {
          line.classList.remove("live");
          line.classList.add(event.status === "error" ? "err" : "ok");
          line.querySelector(".tx").append(" ", el("b", {}, event.status === "error" ? "✗" : "✓"), el("em", {}, ` ${result}`));
        }
      }
      break;
    }
    case "photos":
      setActive("photos");
      if (event.status === "start") feed("◉", t("photos_start", event.count), "", event.model, at);
      else {
        const looks = el("span", {}, (event.looks || []).map((look) => el("span", { class: `kind ${look.kind === "live_selfie" ? "live" : look.kind === "unclear" ? "" : "screen"}` }, `${look.name || "?"} — ${L().PHOTO_KIND[look.kind] || look.kind}`)));
        feed("◉", t("photos_done"), "", looks, at);
        if (event.same_background) feed("◉", t("same_background", event.same_background), "warn", null, at);
        setActive("analyst");
      }
      break;
    case "report":
      setActive("analyst");
      feed("✎", event.attempt === 1 ? t("report_first") : t("report_retry", event.attempt), "", null, at);
      break;
    case "checker":
      setActive("checker");
      if (event.issues && event.issues.length) {
        feed("✗", event.round ? t("checker_back", event.issues.length) : event.issues[0], "warn",
          event.round ? event.issues.join(" · ") : null, at);
      } else {
        feed("✓", t("checker_ok", event.findings, event.facts), "ok", null, at);
      }
      break;
    case "skeptic":
      setActive("skeptic");
      if (event.status === "start") feed("⚖", t("skeptic_start", event.cases), "", event.model, at);
      else {
        for (const item of event.verdicts || []) {
          feed("⚖", `«${item.title}»${item.subject ? ` — ${item.subject}` : ""}`, item.verdict === "innocent_likely" ? "ok" : "",
            t("skeptic_says", L().VERDICT[item.verdict] || t("no_opinion")), at);
        }
        if (event.text) feed("⚠", event.text, "warn", null, at);
      }
      break;
    case "writer":
      setActive("writer");
      if (event.status === "start") feed("✦", t("writer_start"), "", null, at);
      else feed("✦", t("writer_done", event.actions), "", null, at);
      break;
    case "validator":
      setActive("validator");
      feed(event.ok ? "✓" : "✗", `${L().ACTIONS[event.action] || event.action} — ${event.ok ? t("validated_ok") : t("validated_no")}`, event.ok ? "ok" : "warn", event.ok ? null : event.text, at);
      break;
    case "finished":
      if (!event.ok) stopRun(false, event.text);
      break;
    default:
      break;
  }
}

// ---- Answer ----------------------------------------------------------------

function section(title, items, listClass = "") {
  if (!items || !items.length) return null;
  return el("div", { class: "section" }, el("h3", {}, title), el("ul", { class: listClass }, items.map((item) => el("li", {}, item))));
}

function shown(value) {
  if (Array.isArray(value)) return value.map((day) => L().WEEKDAYS[day] || day).join(", ");
  return value ?? "—";
}

function previewTable(preview, fields) {
  if (!preview || !preview.before) return null;
  const rows = Object.entries(fields).map(([field, label]) => {
    const before = shown(preview.before[field]), after = shown(preview.after ? preview.after[field] : null);
    return el("tr", {}, el("th", {}, label), el("td", {}, before), el("td", { class: before !== after ? "changed" : "" }, after));
  });
  return el("table", { class: "diff" }, el("tr", {}, el("th", {}, ""), el("th", {}, t("before")), el("th", {}, t("after"))), rows);
}

// One action the team proposes: what will change, said by code, and the buttons.
function actionBlock(action, card) {
  const result = el("div", { class: "muted" });
  const buttons = el("div", { class: "row" });
  const pays = action.level === "changes_pay";
  const block = el("div", { class: "action" },
    el("div", { class: "title" }, L().ACTIONS[action.name] || action.name,
      el("span", { class: pays ? "level pay" : "level" }, pays ? t("pays") : t("no_pay"))),
    el("div", { class: "what" }, action.text),
    previewTable(action.preview, action.name === "change_schedule" ? L().SCHEDULE_FIELDS : L().DAY_FIELDS),
    action.reason ? el("div", { class: "muted" }, t("reason_written", action.reason)) : null,
    card ? el("div", { class: "muted from" }, t("why", card.title, card.subject.name)) : null,
    buttons, result);

  const decide = async (kind, body) => {
    for (const button of buttons.querySelectorAll("button")) button.disabled = true;
    result.className = "muted"; result.textContent = t("running_nazar");
    try {
      const updated = await api(`/api/proposals/${action.proposal_id}/${kind}`, body);
      render(updated.status);
      loadAudit();
    } catch (error) {
      result.className = "error"; result.textContent = error.message;
      for (const button of buttons.querySelectorAll("button")) button.disabled = false;
    }
  };

  function askAgain(question, confirmLabel, onConfirm, withReason) {
    const reason = withReason ? el("input", { type: "text", maxlength: "500", placeholder: t("reason_placeholder") }) : null;
    const yes = el("button", { class: "primary", type: "button" }, confirmLabel);
    const back = el("button", { type: "button" }, t("back"));
    buttons.replaceChildren(el("span", {}, question), reason, yes, back);
    yes.onclick = () => {
      if (withReason && !reason.value.trim()) { reason.focus(); return; }
      onConfirm(withReason ? reason.value : undefined);
    };
    back.onclick = () => render("pending");
    (reason || yes).focus();
  }

  function render(status) {
    buttons.replaceChildren();
    result.className = "muted"; result.textContent = "";
    if (status === "pending") {
      const apply = el("button", { class: "primary", type: "button" }, t("apply"));
      const disagree = el("button", { type: "button" }, t("disagree"));
      const later = el("button", { type: "button" }, t("later"));
      buttons.append(apply, disagree, later);
      apply.onclick = () => {
        const go = () => decide("confirm", { actor: $("actor").value });
        if (pays) askAgain(t("confirm_pay"), t("confirm_yes"), go, false); else go();
      };
      disagree.onclick = () => askAgain(t("why_q"), t("send_reason"),
        (reason) => decide("decline", { actor: $("actor").value, reason }), true);
      later.onclick = () => { buttons.replaceChildren(); result.textContent = t("postponed"); };
    } else if (status === "executed") {
      result.className = "done"; result.textContent = t("executed");
      const undo = el("button", { type: "button" }, t("undo"));
      undo.onclick = () => decide("undo", { actor: $("actor").value });
      buttons.append(undo);
    } else {
      result.textContent = { declined: t("declined"), undone: t("undone"), stale: t("stale"), failed: t("not_done") }[status] || status;
    }
  }
  render(action.status);
  return block;
}

// A finding, folded: the title and the confidence; the reasoning behind "Подробнее".
function cardView(card, index) {
  const skeptic = card.skeptic ? L().VERDICT[card.skeptic.verdict] : t("skeptic_silent");
  const node = el("details", { class: `card ${card.confidence}`, style: `animation-delay:${index * 120}ms` },
    el("summary", { class: "card-head" },
      el("div", {}, el("h2", {}, card.title), el("div", { class: "subject" }, card.subject.name),
        el("span", { class: "more" }, t("more"))),
      el("div", { class: `meter ${card.confidence}` }, el("span", { class: "bars" }, el("i"), el("i"), el("i")),
        el("small", {}, t("confidence", L().CONFIDENCE[card.confidence])))),
    el("div", { class: "badges" },
      el("span", { class: "badge" }, t("badge_analyst"), el("b", {}, L().CONFIDENCE[card.analyst_confidence])),
      el("span", { class: "badge" }, t("badge_skeptic"), el("b", {}, skeptic))),
    el("div", { class: "section" }, el("h3", {}, t("sec_facts")),
      el("ul", { class: "facts" }, card.facts.map((fact) => el("li", {}, fact.text, " ",
        fact.evidence.map((ref) => el("span", { class: "ref" }, `${ref.kind}:${ref.id.slice(0, 8)}`)))))),
    el("div", { class: "section" }, el("h3", {}, t("sec_hypothesis")), el("div", { class: "hypothesis" }, card.hypothesis)),
    section(t("sec_alts"), card.alternatives, "alts"),
    section(t("sec_owner"), (card.owner_said || []).map((text) => `«${text}»`), "owner"),
    section(t("sec_check"), card.check_steps),
    card.actions_why ? el("div", { class: "section" }, el("h3", {}, t("sec_why_action")), el("div", { class: "muted" }, card.actions_why)) : null);
  return node;
}

// A question to ask back: pressing it asks the team again, with the name in full.
function choiceButton(choice) {
  const button = el("button", { type: "button" }, choice.label);
  button.addEventListener("click", () => {
    $("question").value = choice.question;
    $("ask").requestSubmit();
  });
  return button;
}

// `live` — the latest answer: a setup in it can still be changed and applied.
function answerNodes(data, live = false) {
  const removed = data.removed_by_fact_checker.map((item) => `${item.fact || item.finding}: ${item.reasons.join("; ")}`);
  const rounds = data.fact_checker_rounds.flat();
  const actions = data.cards.flatMap((card) => card.actions.map((action) => actionBlock(action, card)));
  // Short first: what the team thinks and what it will do if the owner agrees; the reasoning is folded below.
  return [
    actions.length ? el("h3", { class: "lead" }, t("lead_think")) : null,
    el("p", { class: "summary" }, data.summary),
    (data.answer || []).length ? el("ul", { class: "answer-facts" }, data.answer.map((fact) => el("li", {}, fact.text))) : null,
    (data.choices || []).length ? el("div", { class: "suggest choices" }, data.choices.map(choiceButton)) : null,
    data.setup ? setupSection(data.setup, live) : null,
    actions.length ? el("h3", { class: "lead" }, t("lead_do")) : null,
    actions,
    data.cards.length ? el("h3", { class: "lead" }, t("lead_why")) : null,
    data.cards.map(cardView),
    section(t("sec_gaps"), data.data_gaps.concat(data.notes)),
    rounds.length || removed.length ? el("details", { class: "trace" },
      el("summary", {}, t("checker_trace", rounds.length, removed.length)),
      el("ul", {}, rounds.concat(removed).map((item) => el("li", {}, item)))) : null,
    el("div", { class: "stats" },
      el("span", { class: "chip" }, t("nazar_calls", data.stats.tool_calls)),
      data.stats.models.map((item) => el("span", { class: "chip", title: item.model },
        `${L().ROLES[item.role] || item.role}: ${number(item.input_tokens)} → ${number(item.output_tokens)} ${t("tokens")} · ${dollars(item.cost_usd, item.estimated)}`)),
      el("span", { class: "chip" }, t("total", dollars(data.stats.cost_usd, data.stats.cost_estimated)))),
    data.stats.cost_estimated ? el("p", { class: "muted" }, t("estimated_note")) : null,
  ].flat().filter(Boolean);
}

// ---- The conversation --------------------------------------------------------
// The last answer on top; the earlier ones of this conversation below it,
// newest first and folded — their buttons still work. A follow-up says how
// the team took it ("I took the question as: …").
let shownAnswer = null; // drawn again in another language
let shownDialog = [];

const meantLine = (meant) => (meant ? [el("p", { class: "muted meant" }, t("meant", meant))] : []);

function renderAnswer(data) {
  shownAnswer = data;
  $("answer").replaceChildren(...meantLine(data.meant), ...answerNodes(data, true));
}

function renderHistory() {
  const earlier = shownDialog.slice(0, -1).reverse();
  $("history").replaceChildren(...(earlier.length ? [
    el("h3", { class: "lead" }, t("history")),
    ...earlier.map((item) => el("details", { class: "turn" },
      el("summary", {}, item.question), ...meantLine(item.meant), ...answerNodes(item.answer))),
  ] : []));
  $("new-dialog").hidden = !shownDialog.length;
}

async function loadDialog() {
  try {
    shownDialog = await api("/api/dialog");
  } catch (error) {
    say(error.message, "error");
    return;
  }
  if (shownDialog.length) renderAnswer(shownDialog[shownDialog.length - 1].answer);
  else await loadLast();
  renderHistory();
}

$("new-dialog").addEventListener("click", async () => {
  try {
    await api("/api/dialog/reset", {});
  } catch (error) {
    say(error.message, "error");
    return;
  }
  shownDialog = [];
  shownAnswer = null;
  $("answer").replaceChildren();
  $("history").replaceChildren();
  $("run").hidden = true;
  $("new-dialog").hidden = true;
  say(t("dialog_reset"));
  $("question").focus();
});

async function loadLast() {
  try {
    const last = await api("/api/last");
    if (last) renderAnswer(last);
  } catch (error) {
    $("status").className = "status error"; $("status").textContent = error.message;
  }
}

async function loadUsage() {
  try {
    const usage = await api("/api/usage");
    if (!usage.all) { $("usage").replaceChildren(el("p", { class: "muted" }, t("usage_off"))); return; }
    const tile = (label, item) => el("div", { class: "tile" }, el("small", {}, label), el("b", {}, dollars(item.cost_usd, item.estimated)),
      el("small", {}, t("runs_tokens", item.runs, number(item.input_tokens), number(item.output_tokens))));
    $("usage").replaceChildren(
      el("div", { class: "tiles" }, tile(t("today"), usage.today), tile(t("week"), usage.week), tile(t("all"), usage.all)),
      usage.all.per_run_usd !== null ? el("p", { class: "muted" }, t("per_run", dollars(usage.all.per_run_usd, usage.all.estimated))) : null,
      usage.by_role.length ? el("table", {},
        el("tr", {}, el("th", {}, t("th_role")), el("th", {}, t("th_model")), el("th", { class: "num" }, t("th_calls")),
          el("th", { class: "num" }, t("th_tokens")), el("th", { class: "num" }, t("th_price"))),
        usage.by_role.map((row) => el("tr", {}, el("td", {}, L().ROLES[row.role] || row.role), el("td", {}, row.model),
          el("td", { class: "num" }, row.calls), el("td", { class: "num" }, `${number(row.input_tokens)} → ${number(row.output_tokens)}`),
          el("td", { class: "num" }, dollars(row.cost_usd, row.estimated))))) : el("p", { class: "muted" }, t("no_calls")));
  } catch (error) {
    $("usage").replaceChildren(el("p", { class: "error" }, error.message));
  }
}

async function loadAudit() {
  loadUsage();
  try {
    const [entries, stats] = await Promise.all([api("/api/audit?limit=30"), api("/api/stats")]);
    $("decisions").textContent = stats.agreement === null ? t("no_decisions")
      : t("decisions", stats.applied, stats.applied + stats.declined, Math.round(stats.agreement * 100), stats.undone, stats.pending);
    $("audit").replaceChildren(...entries.map((entry) => el("li", { class: entry.event },
      el("span", { class: "when" }, entry.at.replace("T", " ").slice(0, 16)),
      `${L().EVENTS[entry.event] || entry.event} · ${L().ACTIONS[entry.action] || entry.action || ""} · ${entry.details.text || entry.subject || entry.details.full_name || ""} · ${entry.actor}`,
      entry.details.reasons ? el("span", { class: "ref" }, entry.details.reasons.join("; ")) : null,
      entry.details.reason ? el("span", { class: "ref" }, `«${entry.details.reason}»`) : null,
      entry.details.error ? el("span", { class: "ref" }, entry.details.error) : null)));
  } catch (error) {
    $("audit").replaceChildren(el("li", { class: "error" }, error.message));
  }
}

// ---- Asking ----------------------------------------------------------------

function chooseAnalyst(value) {
  for (const button of $("analyst-seg").children) button.setAttribute("aria-checked", String(button.dataset.value === value));
}
const chosenAnalyst = () => [...$("analyst-seg").children].find((button) => button.getAttribute("aria-checked") === "true").dataset.value;
for (const button of $("analyst-seg").children) button.addEventListener("click", () => chooseAnalyst(button.dataset.value));
// "Try these": each card asks its question at once; the line under it says what should happen.
function renderTry() {
  $("try-list").replaceChildren(...L().TRY.map((item) => {
    const card = el("button", { type: "button", class: "try-card" },
      el("b", {}, item.t), el("span", { class: "try-q" }, `“${item.q}”`),
      el("small", {}, el("i", {}, t("try_expect")), " ", item.e));
    card.addEventListener("click", () => {
      if ($("send").disabled) return; // one question at a time: the team is still on the last one
      $("question").value = item.q;
      chooseAnalyst("auto");
      $("ask").requestSubmit();
    });
    return card;
  }));
}
renderTry();

// Enter asks, Shift+Enter starts a new line.
// ---- Voice ------------------------------------------------------------------
// The microphone records in the browser; the agent turns it into text (the
// same model as for the bot's voice messages) and puts it in the box, for the
// owner to read and send.
const MAX_VOICE_SECONDS = 120;
const mic = { recorder: null, timer: null, started: 0 };

function showMic(recording) {
  $("mic").classList.toggle("recording", recording);
  $("mic").textContent = recording ? "■" : "🎤";
  $("mic").dataset.i18nTitle = recording ? "mic_stop" : "mic";
  $("mic").title = t($("mic").dataset.i18nTitle);
}

function say(text, kind = "") {
  $("status").className = `status ${kind}`;
  $("status").textContent = text;
}

async function hear(blob, type) {
  const form = new FormData();
  form.append("audio", blob, type.includes("mp4") ? "voice.mp4" : type.includes("ogg") ? "voice.ogg" : "voice.webm");
  form.append("lang", LANG);
  say(t("mic_hearing"));
  $("mic").disabled = true;
  try {
    const { text } = await upload("/api/voice", form);
    const typed = $("question").value.trim();
    $("question").value = typed ? `${typed} ${text}` : text;
    $("question").focus();
    say(t("mic_heard"));
  } catch (error) {
    say(error.message, "error");
  } finally {
    $("mic").disabled = false;
  }
}

$("mic").addEventListener("click", async () => {
  if (mic.recorder) { mic.recorder.stop(); return; }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    say(t("mic_denied"), "error");
    return;
  }
  const recorder = new MediaRecorder(stream);
  const chunks = [];
  recorder.addEventListener("dataavailable", (event) => { if (event.data.size) chunks.push(event.data); });
  recorder.addEventListener("stop", () => {
    for (const track of stream.getTracks()) track.stop();
    clearInterval(mic.timer);
    mic.recorder = null;
    showMic(false);
    const type = recorder.mimeType || "audio/webm";
    hear(new Blob(chunks, { type }), type);
  });
  Object.assign(mic, { recorder, started: performance.now() });
  recorder.start();
  showMic(true);
  const tick = () => {
    const elapsed = (performance.now() - mic.started) / 1000;
    say(t("mic_recording", seconds(elapsed)));
    if (elapsed >= MAX_VOICE_SECONDS && recorder.state === "recording") recorder.stop();
  };
  tick();
  mic.timer = setInterval(tick, 200);
});
$("mic").hidden = !(window.MediaRecorder && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

$("question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!$("send").disabled) $("ask").requestSubmit();
  }
});

$("ask").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("send").disabled = true;
  $("status").textContent = "";
  $("answer").replaceChildren();
  startRun($("question").value);
  // The team's work is the first thing to see. Not "smooth": the feed scrolling itself would stop it halfway.
  $("run").scrollIntoView({ block: "start" });
  try {
    const answer = await api("/api/ask", { question: $("question").value, analyst: chosenAnalyst(), lang: LANG });
    await pollProgress(); // a tick may have been in flight: one more for the last events
    await pollProgress();
    stopRun(true);
    $("question").value = "";
    await loadDialog();
    loadAudit();
  } catch (error) {
    await pollProgress();
    stopRun(false, error.message);
  } finally {
    $("send").disabled = false;
  }
});

// Opened while the team is already working (a reload, another person of the
// company): follow the same run, then show its answer.
async function followRunning() {
  let state;
  try { state = await api("/api/progress?after=0"); } catch { return false; }
  if (!state.running) {
    // The last run, finished: how the team got to the answer below.
    if (state.question && state.events.length) {
      startRun(state.question, state.events[state.events.length - 1].t);
      run.id = state.run;
      clearInterval(run.poll);
      for (const event of state.events) { run.seq = event.seq; handle(event); }
      $("run-timer").textContent = seconds(state.events[state.events.length - 1].t);
      if (!run.finished) stopRun(true);
      if (!$("run").classList.contains("failed")) $("run-title").textContent = t("how_got", $("run-timer").textContent);
    }
    return false;
  }
  $("send").disabled = true;
  const elapsed = state.events.length ? state.events[state.events.length - 1].t : 0;
  startRun(state.question, elapsed);
  run.id = state.run;
  const watch = setInterval(async () => {
    const now = await pollProgress();
    if (now && !now.running) {
      clearInterval(watch);
      stopRun(!(now.events || []).some((item) => item.kind === "finished" && !item.ok));
      $("send").disabled = false;
      loadDialog(); loadAudit();
    }
  }, 1000);
  return true;
}

$("refresh-audit").addEventListener("click", loadAudit);
$("refresh-usage").addEventListener("click", loadUsage);

// ---- Who is here -------------------------------------------------------------
// Opened from the NAZAR cabinet, the address carries a one-minute pass in the
// fragment. It is removed from the address bar at once and exchanged by this
// service for the person's own NAZAR session; from then on decisions carry
// the name NAZAR knows them by, not the name field.
async function signIn() {
  const pass = location.hash.match(/^#handoff=(.+)$/);
  if (pass) {
    history.replaceState(null, "", location.pathname);
    try {
      await api("/api/session", { handoff: decodeURIComponent(pass[1]) });
    } catch (error) {
      notice(error.message);
      return false;
    }
  }
  try {
    const me = await api("/api/me");
    if (me.mode === "person" || me.mode === "demo") {
      const initials = me.full_name.split(/\s+/).map((part) => part[0] || "").join("").slice(0, 2).toUpperCase();
      $("who").replaceChildren(el("span", { class: "avatar" }, initials), el("span", {}, me.full_name, el("small", {}, me.company_name)));
    }
    if (me.mode === "demo") showDemo();
    return true;
  } catch (error) {
    notice(error.message);
    return false;
  }
}

// The demo company lives in this process: a judge can undo everything they
// applied and start again from this morning's data.
function showDemo() {
  // data-i18n: the language switch renames them with the rest of the page.
  const reset = el("button", { type: "button", class: "ghost small", "data-i18n": "reset_demo" }, t("reset_demo"));
  reset.addEventListener("click", async () => {
    if (!confirm(t("reset_confirm"))) return;
    reset.disabled = true;
    try {
      await api("/api/demo/reset", {});
      location.reload();
    } catch (error) {
      reset.disabled = false;
      alert(error.message);
    }
  });
  $("who").prepend(el("span", { class: "demo-tag", title: t("demo_title"), "data-i18n": "demo", "data-i18n-title": "demo_title" }, t("demo")), reset);
  $("demo-line").hidden = false;
}

function notice(text) {
  $("sign-in-notice").hidden = false;
  $("sign-in-notice").textContent = text;
}

// ---- Language and theme ------------------------------------------------------
// The window's words change at once; the owner's question decides the answer's language.
function showPrefs() {
  for (const button of $("lang-seg").children) button.setAttribute("aria-checked", String(button.dataset.lang === LANG));
  const dark = document.documentElement.dataset.theme === "dark";
  $("theme-toggle").textContent = dark ? "☀" : "☾";
  $("theme-toggle").title = t(dark ? "theme_light" : "theme_dark");
  $("theme-toggle").setAttribute("aria-label", $("theme-toggle").title);
  $("team-status").textContent = t(run.poll && !run.finished ? "team_busy" : "team_idle");
}
for (const button of $("lang-seg").children) {
  button.addEventListener("click", () => {
    setLang(button.dataset.lang);
    showPrefs();
    renderTry();
    renderSamples();
    if (shownAnswer) renderAnswer(shownAnswer);
    renderHistory();
    loadAudit();
    if (!$("tab-import").hidden) loadWaiting();
  });
}
$("theme-toggle").addEventListener("click", () => { toggleTheme(); showPrefs(); });
translatePage();
showPrefs();

signIn().then(async (ready) => {
  if (!ready) return;
  loadAudit();
  if (!(await followRunning())) loadDialog();
});
