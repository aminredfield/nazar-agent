"use strict";
// NAZAR Agent — the assistant window. Everything shown comes from the agent's API;
// text from models or NAZAR is always inserted as text, never as markup.

const CONFIDENCE = { high: "высокая", medium: "средняя", low: "низкая" };
const VERDICT = { concern_supported: "повод есть", inconclusive: "не уверен", innocent_likely: "скорее всё в порядке" };
const ACTIONS = {
  review_event: "Записать итог разбора отметки",
  adjust_shift: "Исправить засчитанное время смены",
  void_shift: "Не засчитывать день",
  change_schedule: "Изменить график объекта",
};
const REVIEW_STATUS = { no_violation: "нарушения нет", confirmed: "нарушение подтверждено", insufficient_data: "недостаточно данных", not_reviewed: "не разобрано" };
const DAY_FIELDS = { worked_minutes: "Отработано, мин", paid_minutes: "К оплате, мин", late_minutes: "Опоздание, мин", early_leave_minutes: "Ранний уход, мин" };
const SCHEDULE_FIELDS = { shift_start: "Начало смены", shift_end: "Конец смены", working_days: "Рабочие дни" };
const WEEKDAYS = ["", "пн", "вт", "ср", "чт", "пт", "сб", "вс"];
const EVENTS = {
  proposed: "предложено агентом", executed: "выполнено в NAZAR", declined: "отклонено", refused: "не выполнено", failed: "ошибка", undone: "отменено",
  draft_created: "добавлен, ждёт фото", employee_created: "создан в NAZAR", photo_rejected: "фото не подошло", draft_discarded: "убран из списка",
};
const ANALYSTS = { attendance: "Аналитик отметок", timesheet: "Аналитик табеля" };
// What each tool does, in the owner's words.
const TOOLS = {
  get_shared_phones: "Считаю телефоны, с которых отмечаются несколько человек",
  get_attendance_events: "Читаю отметки",
  list_objects: "Смотрю объекты и геозоны",
  get_event_detail: "Открываю отметку целиком",
  get_presence_today: "Смотрю, кто сейчас на объектах",
  inspect_check_in_photos: "Отдаю фото отметок инспектору",
  get_timesheet_summary: "Читаю табель, посчитанный NAZAR",
  get_timesheet_day: "Открываю день табеля",
  get_setup_check: "Смотрю проверку настройки NAZAR",
  compare_site_schedules: "Сравниваю график каждого объекта с реальным днём",
};
const PHOTO_KIND = { live_selfie: "живое селфи", photo_of_screen: "фото экрана", photo_of_print: "фото распечатки", unclear: "неясно" };

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
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Ошибка ${response.status}`);
  return data;
}

const seconds = (value) => `${value.toFixed(1).replace(".", ",")} с`;

// ---- The team at work ----------------------------------------------------
// While a question is answered the window asks /api/progress for new events
// and shows them: which agent works now, each tool call, what the photo
// inspector saw, the fact checker's rounds, the skeptic's verdicts.

const run = { seq: 0, poll: null, clock: null, started: 0, open: {}, analyst: null, active: null, finished: false, inflight: null };

function startRun(question, alreadyFor = 0) {
  Object.assign(run, { seq: 0, open: {}, active: null, finished: false, inflight: null, started: performance.now() - alreadyFor * 1000 });
  $("run").hidden = false;
  $("run").className = "run";
  $("run-title").textContent = "Команда работает";
  $("run-question").textContent = question;
  $("feed").replaceChildren();
  for (const node of $("roster").children) node.className = "";
  $("analyst-name").textContent = "Аналитик";
  $("team-pill").classList.add("busy");
  $("team-status").textContent = "Команда разбирает вопрос";
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
  $("run-title").textContent = ok ? `Готово за ${$("run-timer").textContent}` : "Не получилось";
  if (text) feed("⚠", text, "err");
  $("team-pill").classList.remove("busy");
  $("team-status").textContent = "Команда на месте";
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
  if (args.work_date) parts.push(`день ${day(args.work_date)}`);
  if (args.limit) parts.push(`до ${args.limit}`);
  if (args.suspicious_only) parts.push("только с флагами");
  if (args.event_ids) parts.push(`отметок: ${args.event_ids}`);
  return parts.join(", ");
}

function handle(event) {
  const at = event.t;
  switch (event.kind) {
    case "route": {
      setActive("dispatcher");
      run.analyst = event.analyst;
      $("analyst-name").textContent = ANALYSTS[event.analyst] || "Аналитик";
      feed("⇢", `Диспетчер передал вопрос: ${ANALYSTS[event.analyst] || event.analyst}`, "", event.text || null, at);
      break;
    }
    case "thinking":
      setActive("analyst");
      if (event.turn === 1) feed("◆", `${ANALYSTS[run.analyst] || "Аналитик"} начинает расследование`, "", null, at);
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
        open.push(feed("›", TOOLS[event.name] || event.name, "live", details || null, at));
      } else {
        const line = open.shift();
        const result = event.status === "error" ? `не вышло: ${event.text}`
          : `${event.found !== null && event.found !== undefined ? `получено ${event.found}` : "готово"} · ${(event.ms / 1000).toFixed(1).replace(".", ",")} с`;
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
      if (event.status === "start") feed("◉", `Инспектор фото смотрит ${event.count} снимк${event.count === 1 ? "" : event.count < 5 ? "а" : "ов"}`, "", event.model, at);
      else {
        const looks = el("span", {}, (event.looks || []).map((look) => el("span", { class: `kind ${look.kind === "live_selfie" ? "live" : look.kind === "unclear" ? "" : "screen"}` }, `${look.name || "?"} — ${PHOTO_KIND[look.kind] || look.kind}`)));
        feed("◉", "Инспектор описал фото", "", looks, at);
        if (event.same_background) feed("◉", `Одинаковый фон: ${event.same_background} групп${event.same_background === 1 ? "а" : "ы"} снимков`, "warn", null, at);
        setActive("analyst");
      }
      break;
    case "report":
      setActive("analyst");
      feed("✎", event.attempt === 1 ? "Аналитик пишет отчёт: факты отдельно от гипотезы" : `Аналитик исправляет отчёт (попытка ${event.attempt})`, "", null, at);
      break;
    case "checker":
      setActive("checker");
      if (event.issues && event.issues.length) {
        feed("✗", event.round ? `Проверщик фактов вернул отчёт: ${event.issues.length} замечани${event.issues.length === 1 ? "е" : "я"}` : event.issues[0], "warn",
          event.round ? event.issues.join(" · ") : null, at);
      } else {
        feed("✓", `Проверщик фактов: ${event.findings} находк${event.findings === 1 ? "а" : event.findings < 5 && event.findings > 0 ? "и" : "ок"}, ${event.facts} фактов — каждый есть в данных NAZAR`, "ok", null, at);
      }
      break;
    case "skeptic":
      setActive("skeptic");
      if (event.status === "start") feed("⚖", `Скептик на другой модели ищет невиновные объяснения (${event.cases})`, "", event.model, at);
      else {
        for (const item of event.verdicts || []) {
          feed("⚖", `«${item.title}»${item.subject ? ` — ${item.subject}` : ""}`, item.verdict === "innocent_likely" ? "ok" : "",
            `скептик: ${VERDICT[item.verdict] || "без мнения"}`, at);
        }
        if (event.text) feed("⚠", event.text, "warn", null, at);
      }
      break;
    case "writer":
      setActive("writer");
      if (event.status === "start") feed("✦", "Составитель пишет рекомендации и выбирает действия из каталога", "", null, at);
      else feed("✦", `Предложено действий: ${event.actions}`, "", null, at);
      break;
    case "validator":
      setActive("validator");
      feed(event.ok ? "✓" : "✗", `${ACTIONS[event.action] || event.action} — ${event.ok ? "прошло проверку" : "отклонено"}`, event.ok ? "ok" : "warn", event.ok ? null : event.text, at);
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
  if (Array.isArray(value)) return value.map((day) => WEEKDAYS[day] || day).join(", ");
  return value ?? "—";
}

function previewTable(preview, fields) {
  if (!preview || !preview.before) return null;
  const rows = Object.entries(fields).map(([field, label]) => {
    const before = shown(preview.before[field]), after = shown(preview.after ? preview.after[field] : null);
    return el("tr", {}, el("th", {}, label), el("td", {}, before), el("td", { class: before !== after ? "changed" : "" }, after));
  });
  return el("table", { class: "diff" }, el("tr", {}, el("th", {}, ""), el("th", {}, "Было"), el("th", {}, "Станет")), rows);
}

function describeParams(action) {
  const params = action.params;
  if (action.name === "review_event") return `Итог: ${REVIEW_STATUS[params.status] || params.status}. ${params.comment}`;
  if (action.name === "adjust_shift") {
    const spans = params.intervals.map((item) => `${item.start_at.slice(11, 16)}–${item.end_at.slice(11, 16)}`).join(", ");
    return `${params.work_date}: засчитать ${spans}. ${params.reason}`;
  }
  if (action.name === "change_schedule") {
    return `С сегодняшнего дня смена ${params.shift_start}–${params.shift_end}; прошлые дни NAZAR не пересчитывает. ${params.reason}`;
  }
  return `${params.work_date}: ${params.reason}`;
}

function actionBlock(action) {
  const result = el("div", { class: "muted" });
  const buttons = el("div", { class: "row" });
  const pays = action.level === "changes_pay";
  const block = el("div", { class: "action" },
    el("div", { class: "title" }, ACTIONS[action.name] || action.name),
    el("span", { class: pays ? "level pay" : "level" }, pays ? "Меняет оплату — было и станет считает NAZAR" : "Не меняет оплату"),
    el("div", {}, describeParams(action)),
    previewTable(action.preview, action.name === "change_schedule" ? SCHEDULE_FIELDS : DAY_FIELDS),
    buttons, result);

  const decide = async (kind, body) => {
    for (const button of buttons.querySelectorAll("button")) button.disabled = true;
    result.className = "muted"; result.textContent = "Выполняю в NAZAR…";
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
    const reason = withReason ? el("input", { type: "text", maxlength: "500", placeholder: "Например: у них правда нет смартфонов" }) : null;
    const yes = el("button", { class: "primary", type: "button" }, confirmLabel);
    const back = el("button", { type: "button" }, "Назад");
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
      const apply = el("button", { class: "primary", type: "button" }, "Применить");
      const disagree = el("button", { type: "button" }, "Не согласен");
      const later = el("button", { type: "button" }, "Отложить");
      buttons.append(apply, disagree, later);
      apply.onclick = () => {
        const go = () => decide("confirm", { actor: $("actor").value });
        if (pays) askAgain("Это изменит данные в NAZAR.", "Да, применить", go, false); else go();
      };
      disagree.onclick = () => askAgain("Почему? Команда учтёт это дальше.", "Отправить",
        (reason) => decide("decline", { actor: $("actor").value, reason }), true);
      later.onclick = () => { buttons.replaceChildren(); result.textContent = "Отложено — предложение сохранено."; };
    } else if (status === "executed") {
      result.className = "done"; result.textContent = "✓ Выполнено в NAZAR.";
      const undo = el("button", { type: "button" }, "Отменить");
      undo.onclick = () => decide("undo", { actor: $("actor").value });
      buttons.append(undo);
    } else {
      result.textContent = { declined: "Отклонено — причина сохранена.", undone: "Отменено — NAZAR вернул как было.", stale: "Данные изменились — спросите заново.", failed: "Не выполнено." }[status] || status;
    }
  }
  render(action.status);
  return block;
}

function cardView(card, index) {
  const skeptic = card.skeptic ? VERDICT[card.skeptic.verdict] : "не высказался";
  const node = el("article", { class: `card ${card.confidence}`, style: `animation-delay:${index * 120}ms` },
    el("div", { class: "card-head" },
      el("div", {}, el("h2", {}, card.title), el("div", { class: "subject" }, card.subject.name)),
      el("div", { class: `meter ${card.confidence}` }, el("span", { class: "bars" }, el("i"), el("i"), el("i")),
        el("small", {}, `уверенность ${CONFIDENCE[card.confidence]}`))),
    el("div", { class: "badges" },
      el("span", { class: "badge" }, "аналитик: ", el("b", {}, CONFIDENCE[card.analyst_confidence])),
      el("span", { class: "badge" }, "скептик: ", el("b", {}, skeptic))),
    el("div", { class: "section" }, el("h3", {}, "Что видно в данных"),
      el("ul", { class: "facts" }, card.facts.map((fact) => el("li", {}, fact.text, " ",
        fact.evidence.map((ref) => el("span", { class: "ref" }, `${ref.kind}:${ref.id.slice(0, 8)}`)))))),
    el("div", { class: "section" }, el("h3", {}, "Возможное объяснение — это предположение"), el("div", { class: "hypothesis" }, card.hypothesis)),
    section("Может быть и иначе", card.alternatives, "alts"),
    section("Раньше вы не согласились и объяснили", (card.owner_said || []).map((text) => `«${text}»`), "owner"),
    section("Как проверить", card.check_steps),
    card.actions.length || card.actions_why ? el("div", { class: "section" },
      el("h3", {}, "Что можно сделать"),
      card.actions_why ? el("div", { class: "muted" }, card.actions_why) : null,
      card.actions.map(actionBlock)) : null);
  return node;
}

function renderAnswer(data) {
  const removed = data.removed_by_fact_checker.map((item) => `${item.fact || item.finding}: ${item.reasons.join("; ")}`);
  const rounds = data.fact_checker_rounds.flat();
  $("answer").replaceChildren(...[
    el("p", { class: "summary" }, data.summary),
    data.cards.map(cardView),
    section("Чего не видно в данных", data.data_gaps.concat(data.notes)),
    rounds.length || removed.length ? el("details", { class: "trace" },
      el("summary", {}, `Проверщик фактов: ${rounds.length} замечаний исправлено, ${removed.length} удалено`),
      el("ul", {}, rounds.concat(removed).map((item) => el("li", {}, item)))) : null,
    el("div", { class: "stats" },
      el("span", { class: "chip" }, `${data.stats.tool_calls} запросов к NAZAR`),
      data.stats.models.map((item) => el("span", { class: "chip" }, `${item.role}: ${item.model}`)),
      el("span", { class: "chip" }, `≈ $${data.stats.cost_usd}`)),
  ].flat().filter(Boolean));
}

async function loadLast() {
  try {
    const last = await api("/api/last");
    if (last) renderAnswer(last);
  } catch (error) {
    $("status").className = "status error"; $("status").textContent = error.message;
  }
}

async function loadAudit() {
  try {
    const [entries, stats] = await Promise.all([api("/api/audit?limit=30"), api("/api/stats")]);
    $("decisions").textContent = stats.agreement === null ? "Решений по предложениям пока нет."
      : `Применено ${stats.applied} из ${stats.applied + stats.declined} решённых предложений (${Math.round(stats.agreement * 100)}%) · отменено после применения: ${stats.undone} · ждут решения: ${stats.pending}`;
    $("audit").replaceChildren(...entries.map((entry) => el("li", { class: entry.event },
      el("span", { class: "when" }, entry.at.replace("T", " ").slice(0, 16)),
      `${EVENTS[entry.event] || entry.event} · ${ACTIONS[entry.action] || entry.action || ""} · ${entry.subject || entry.details.full_name || ""} · ${entry.actor}`,
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
for (const button of $("suggest").children) {
  button.addEventListener("click", () => {
    $("question").value = button.dataset.q;
    chooseAnalyst(button.dataset.a);
    $("question").focus();
  });
}

$("ask").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("send").disabled = true;
  $("status").textContent = "";
  $("answer").replaceChildren();
  startRun($("question").value);
  try {
    const answer = await api("/api/ask", { question: $("question").value, analyst: chosenAnalyst() });
    await pollProgress(); // a tick may have been in flight: one more for the last events
    await pollProgress();
    stopRun(true);
    renderAnswer(answer);
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
      clearInterval(run.poll);
      for (const event of state.events) { run.seq = event.seq; handle(event); }
      $("run-timer").textContent = seconds(state.events[state.events.length - 1].t);
      if (!run.finished) stopRun(true);
      if (!$("run").classList.contains("failed")) $("run-title").textContent = `Как команда пришла к ответу · ${$("run-timer").textContent}`;
    }
    return false;
  }
  $("send").disabled = true;
  const elapsed = state.events.length ? state.events[state.events.length - 1].t : 0;
  startRun(state.question, elapsed);
  const watch = setInterval(async () => {
    const now = await pollProgress();
    if (now && !now.running) {
      clearInterval(watch);
      stopRun(!(now.events || []).some((item) => item.kind === "finished" && !item.ok));
      $("send").disabled = false;
      loadLast(); loadAudit();
    }
  }, 1000);
  return true;
}

// ---- Import of employees ---------------------------------------------------

function showTab(name) {
  for (const tab of ["ask", "import"]) {
    $(`tab-${tab}`).hidden = tab !== name;
    $(`tab-${tab}-button`).setAttribute("aria-selected", String(tab === name));
  }
  if (name === "import") loadWaiting();
}
$("tab-ask-button").addEventListener("click", () => showTab("ask"));
$("tab-import-button").addEventListener("click", () => showTab("import"));

async function upload(path, form) {
  const response = await fetch(path, { method: "POST", body: form });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    const message = detail && detail.photo_rejected ? `Фото не подошло: ${detail.photo_rejected.join("; ")}`
      : typeof detail === "string" ? detail : `Ошибка ${response.status}`;
    throw new Error(message);
  }
  return data;
}

$("import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData();
  if ($("import-text").value.trim()) form.append("text", $("import-text").value);
  if ($("import-file").files[0]) form.append("file", $("import-file").files[0]);
  $("import-read").disabled = true;
  $("import-status").className = "status";
  $("import-status").textContent = "Агент читает список и сверяет его с сотрудниками и объектами NAZAR…";
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
    el("option", { value: "" }, "— без объекта —"),
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
      el("td", {}, site, row.site_hint ? el("div", { class: "muted" }, `в списке: ${row.site_hint}`) : null),
      el("td", {}, row.issues.map((issue) => el("div", { class: "issue" }, issue))));
    return { tr, include, name, site };
  });
  const attention = data.rows.filter((row) => row.issues.length).length;
  const consent = el("input", { type: "date" });
  const create = el("button", { class: "primary", type: "button" }, "Создать — будут ждать фото");
  const result = el("div", { class: "status" });
  create.onclick = async () => {
    const chosen = rows.filter((row) => row.include.checked && row.name.value.trim())
      .map((row) => ({ full_name: row.name.value.trim(), site_id: row.site.value || null }));
    if (!chosen.length) return;
    create.disabled = true;
    try {
      await api("/api/import/drafts", { actor: $("actor").value, rows: chosen, consent_signed_at: consent.value || null });
      $("import-review").replaceChildren(el("p", { class: "done" }, `Добавлено ${chosen.length}. Теперь снимите каждого — «Очередь съёмки».`));
      loadWaiting(); loadAudit();
    } catch (error) {
      result.className = "status error"; result.textContent = error.message; create.disabled = false;
    }
  };
  $("import-review").replaceChildren(el("section", { class: "panel glass" },
    el("p", {}, `Найдено ${data.rows.length}. ${attention ? `Посмотрите подсвеченные строки (${attention}) — остальное агент разложил сам.` : "Всё разобрано, проверять нечего."}`),
    el("table", { class: "people" },
      el("tr", {}, el("th", {}, ""), el("th", {}, "ФИО"), el("th", {}, "Объект"), el("th", {}, "Что проверить")),
      rows.map((row) => row.tr)),
    el("div", { class: "row section" },
      el("label", { class: "muted" }, "Согласия на биометрию подписаны (дата), если в компании это обязательно: ", consent),
      create),
    result));
}

let waiting = [];

async function loadWaiting() {
  try {
    waiting = await api("/api/import/drafts");
    $("waiting").replaceChildren(...waiting.map((draft) => {
      const pick = el("input", { type: "file", accept: "image/*" });
      pick.onchange = () => pick.files[0] && photograph(draft, pick.files[0], null);
      const remove = el("button", { type: "button" }, "Убрать");
      remove.onclick = async () => { await api(`/api/import/drafts/${draft.id}/discard`, { actor: $("actor").value }); loadWaiting(); loadAudit(); };
      return el("li", {}, `${draft.full_name} · ${draft.site_name || "без объекта"} `, el("label", { class: "muted" }, " фото: ", pick), remove);
    }));
    if (!waiting.length) $("waiting").replaceChildren(el("li", { class: "muted" }, "Никто не ждёт фото."));
  } catch (error) {
    $("waiting").replaceChildren(el("li", { class: "error" }, error.message));
  }
}

async function photograph(draft, file, status) {
  const form = new FormData();
  form.append("actor", $("actor").value);
  form.append("photo", file);
  const note = status || $("import-status");
  note.className = "status"; note.textContent = `Проверяю фото ${draft.full_name} и создаю в NAZAR…`;
  try {
    await upload(`/api/import/drafts/${draft.id}/photo`, form);
    note.className = "status done"; note.textContent = `${draft.full_name} — создан в NAZAR.`;
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
    const close = el("button", { type: "button" }, "Закрыть");
    close.onclick = () => $("shooting").replaceChildren();
    $("shooting").replaceChildren(el("div", { class: "shoot" }, el("div", { class: "next" }, "Все сняты."), close));
    return;
  }
  const camera = el("input", { type: "file", accept: "image/*", capture: "environment", id: "camera" });
  const status = el("div", { class: "status" });
  const skip = el("button", { type: "button" }, "Пропустить");
  const stop = el("button", { type: "button" }, "Закончить");
  camera.onchange = async () => {
    if (camera.files[0] && await photograph(next, camera.files[0], status)) shootNext(skipped);
  };
  skip.onclick = () => { skipped.add(next.id); shootNext(skipped); };
  stop.onclick = () => $("shooting").replaceChildren();
  $("shooting").replaceChildren(el("div", { class: "shoot" },
    el("div", { class: "muted" }, `Осталось: ${waiting.filter((draft) => !skipped.has(draft.id)).length}`),
    el("div", { class: "next" }, `Следующий: ${next.full_name}`),
    el("div", { class: "muted" }, next.site_name || "без объекта"),
    el("label", { class: "camera", for: "camera" }, "📷 Снять"), camera,
    status, el("div", { class: "row", style: "justify-content:center" }, skip, stop)));
}
$("start-shooting").addEventListener("click", async () => { await loadWaiting(); shootNext(); });
$("refresh-waiting").addEventListener("click", loadWaiting);
$("refresh-audit").addEventListener("click", loadAudit);

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
    if (me.mode === "person") {
      const initials = me.full_name.split(/\s+/).map((part) => part[0] || "").join("").slice(0, 2).toUpperCase();
      $("who").replaceChildren(el("span", { class: "avatar" }, initials), el("span", {}, me.full_name, el("small", {}, me.company_name)));
      $("actor-label").hidden = true;
    }
    return true;
  } catch (error) {
    notice(error.message);
    return false;
  }
}

function notice(text) {
  $("sign-in-notice").hidden = false;
  $("sign-in-notice").textContent = text;
}

signIn().then(async (ready) => {
  if (!ready) return;
  loadAudit();
  if (!(await followRunning())) loadLast();
});
