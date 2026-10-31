"use strict";
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? esc(iso) : d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
};

async function api(path, options = {}) {
  const opts = { credentials: "same-origin", ...options };
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  }
  const r = await fetch(path, opts);
  let data = null;
  try { data = await r.json(); } catch (e) { /* пустой ответ */ }
  if (!r.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map((m) => (m.loc ? m.loc.slice(1).join(".") + ": " : "") + m.msg).join("; ");
    throw new Error(msg || `Ошибка ${r.status}`);
  }
  return data;
}

function say(el, text, kind) {
  el.textContent = text || "";
  el.className = "form-message" + (kind ? " " + kind : "");
}

const state = { connections: [], sources: [], mappings: [], unmapped: [] };

// ───── Яндекс ID ─────
async function loadConnections() {
  const data = await api("/api/metrika/connections");
  state.connections = data.items || [];
  $("connectYandex").disabled = !data.oauth_configured;
  if (!data.oauth_configured) say($("accountsMsg"), "OAuth Яндекса не настроен на сервере (METRIKA_OAUTH_* и ключ шифрования в .env).", "error");
  const rows = state.connections.map((c) => `<tr>
      <td><b>${esc(c.label)}</b></td>
      <td><span class="lk-tag ${c.status === "connected" ? "ok" : "err"}">${c.status === "connected" ? "подключён" : "нужно переподключить"}</span></td>
      <td>${c.expires_at ? fmtTime(c.expires_at) : "без срока"}</td>
      <td class="lk-actions"><button class="button button-ghost button-small" data-disconnect="${c.id}">Отключить</button></td></tr>`);
  $("accountsRows").innerHTML = rows.join("") || `<tr><td colspan="4" class="empty">Пока нет подключённых Яндекс ID</td></tr>`;
  const options = state.connections.filter((c) => c.status === "connected")
    .map((c) => `<option value="${c.id}">${esc(c.label)}</option>`).join("");
  $("mapConn").innerHTML = `<option value="">—</option>` + options;
  $("mapCounter").innerHTML = `<option value="">Сначала выберите Яндекс ID</option>`;
}

$("connectYandex").addEventListener("click", () => {
  const label = prompt("Название для этого Яндекс ID (например, почта или имя клиента):", "Яндекс ID");
  if (label && label.trim()) location.href = "/oauth/yandex/start?return_to=/lk&label=" + encodeURIComponent(label.trim());
});

$("accountsRows").addEventListener("click", async (e) => {
  const id = e.target.dataset.disconnect;
  if (!id || !confirm("Отключить этот Яндекс ID? Привязанные к нему кампании перестанут отправляться.")) return;
  try { await api("/api/metrika/connections/" + id, { method: "DELETE" }); await loadAll(); }
  catch (err) { say($("accountsMsg"), err.message, "error"); }
});

// ───── Источники Б24 ─────
const sourceForm = $("sourceForm");
const toggleScore = () => document.querySelectorAll(".score-only").forEach((el) => el.classList.toggle("hidden", sourceForm.mode.value !== "score_field"));
sourceForm.mode.addEventListener("change", toggleScore);

function openSourceForm(src) {
  sourceForm.reset();
  sourceForm.id.value = src ? src.id : "";
  if (src) {
    sourceForm.name.value = src.name;
    sourceForm.lookback_hours.value = src.lookback_hours;
    sourceForm.mode.value = src.mode;
    sourceForm.score_field.value = src.score_field;
    sourceForm.scored_flag_field.value = src.scored_flag_field;
    sourceForm.send_backlog.checked = !!src.send_backlog;
    sourceForm.webhook.placeholder = src.webhook + " (не менять)";
  }
  sourceForm.webhook.required = !src;
  toggleScore();
  say($("sourceMsg"), "");
  sourceForm.classList.remove("hidden");
  sourceForm.name.focus();
}
$("addSourceBtn").addEventListener("click", () => openSourceForm(null));
$("cancelSource").addEventListener("click", () => sourceForm.classList.add("hidden"));

sourceForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = sourceForm;
  const body = {
    name: f.name.value, webhook: f.webhook.value.trim() || null, lookback_hours: Number(f.lookback_hours.value),
    mode: f.mode.value, score_field: f.score_field.value.trim(), scored_flag_field: f.scored_flag_field.value.trim(),
    send_backlog: f.send_backlog.checked, enabled: true,
  };
  say($("sourceMsg"), "Проверяю вебхук…");
  try {
    const id = f.id.value;
    if (id) {
      const prev = state.sources.find((s) => String(s.id) === id);
      body.enabled = prev ? !!prev.enabled : true;
      await api("/api/lk/sources/" + id, { method: "PUT", body });
    } else {
      const made = await api("/api/lk/sources", { method: "POST", body });
      if (made && made.marks && !made.marks.ready) alert("Источник добавлен, но поля для меток в Б24 не настроены.\n" + made.marks.status);
    }
    f.classList.add("hidden");
    await loadAll();
  } catch (err) { say($("sourceMsg"), err.message, "error"); }
});

function renderSources() {
  const rows = state.sources.map((s) => {
    const status = s.last_status || "ещё не запускался";
    const bad = status.startsWith("error");
    return `<tr>
      <td><b>${esc(s.name)}</b><div class="muted small">${esc(s.webhook)} · глубина ${s.lookback_hours} ч${s.enabled ? "" : " · выключен"}</div>
        <div class="muted small">Метки в Б24: ${esc(s.marks_status || "не настроены")}</div></td>
      <td>${s.mode === "score_field" ? "со скорингом" : "каждый лид один раз"}</td>
      <td>${fmtTime(s.last_run_at)}<div class="lk-status" style="${bad ? "color:#bb3d44" : ""}">${esc(status)}</div></td>
      <td><span class="lk-tag ${s.dry_run ? "warn" : "ok"}">${s.dry_run ? "пробный режим" : "боевая"}</span></td>
      <td class="lk-actions">
        <span class="lk-run"><input class="lk-hours" type="number" min="1" max="720" value="24" title="За сколько часов проверить лиды" data-hours-for="${s.id}"><span class="muted small">ч</span>
        <button class="button button-ghost button-small" data-run="${s.id}">Запустить вручную</button></span>
        <button class="button button-ghost button-small" data-marks="${s.id}">${s.marks_ready ? "Проверить поля Б24" : "Настроить поля Б24"}</button>
        <button class="button button-ghost button-small" data-live="${s.id}" data-on="${s.dry_run ? 1 : 0}">${s.dry_run ? "Включить отправку" : "В пробный режим"}</button>
        <button class="button button-ghost button-small" data-edit="${s.id}">Правка</button>
        <button class="button button-ghost button-small" data-del="${s.id}">Удалить</button>
      </td></tr>`;
  });
  $("sourcesRows").innerHTML = rows.join("") || `<tr><td colspan="5" class="empty">Добавьте первый портал Битрикс24</td></tr>`;
}

$("sourcesRows").addEventListener("click", async (e) => {
  const t = e.target.dataset;
  const btn = e.target;
  try {
    if (t.edit) return openSourceForm(state.sources.find((s) => String(s.id) === t.edit));
    if (t.del) {
      if (!confirm("Удалить источник и его историю отправок?")) return;
      await api("/api/lk/sources/" + t.del, { method: "DELETE" });
    } else if (t.run) {
      const input = document.querySelector(`[data-hours-for="${t.run}"]`);
      const hours = Math.min(720, Math.max(1, parseInt(input && input.value, 10) || 24));
      const src = state.sources.find((x) => String(x.id) === t.run);
      if (src && !src.dry_run && !confirm(`Запустить за последние ${hours} ч? Лиды, которые ещё не отправлялись, уйдут в Метрику по-настоящему.`)) return;
      btn.disabled = true; btn.textContent = "Проверяю…";
      await api(`/api/lk/sources/${t.run}/run?hours=${hours}`, { method: "POST" });
    } else if (t.marks) {
      btn.disabled = true; btn.textContent = "Проверяю поля…";
      const res = await api(`/api/lk/sources/${t.marks}/marks/setup`, { method: "POST" });
      alert(res.report.map((r) => `${r.field}: ${r.action === "found" ? "найдено" : r.action === "created" ? "создано" : "ошибка — " + r.detail}`).join("\n"));
    } else if (t.live) {
      const on = t.on === "1";
      if (on && !confirm("Включить реальную отправку в Метрику? Лиды, которые уже видны в пробном режиме, будут считаться отправленными и повторно не уйдут.")) return;
      await api(`/api/lk/sources/${t.live}/live?on=${on}`, { method: "POST" });
    } else return;
    await loadAll();
  } catch (err) { alert(err.message); await loadAll(); }
});

// ───── Кампании → счётчики ─────
$("mapConn").addEventListener("change", async () => {
  const id = $("mapConn").value;
  const sel = $("mapCounter");
  if (!id) { sel.innerHTML = `<option value="">Сначала выберите Яндекс ID</option>`; return; }
  sel.innerHTML = `<option value="">Загружаю счётчики…</option>`;
  try {
    const data = await api("/api/lk/counters?connection_id=" + id);
    sel.innerHTML = `<option value="">Выберите счётчик</option>` + data.items.map((c) =>
      `<option value="${c.id}">${esc(c.name || c.site || "")} · ${c.id}${c.has_lead_goal ? "" : " (цели lead ещё нет)"}</option>`).join("");
  } catch (err) { sel.innerHTML = `<option value="">${esc(err.message)}</option>`; }
});

$("mapForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  say($("mapMsg"), "Привязываю, проверяю цель lead…");
  try {
    const r = await api("/api/lk/mappings", { method: "POST", body: {
      campaign_keys: f.campaign_keys.value, connection_id: Number(f.connection_id.value),
      counter_id: Number(f.counter_id.value), note: f.note.value } });
    say($("mapMsg"), `Готово: привязано кампаний — ${r.saved}. ${r.goal_created ? "Цель lead создана в счётчике." : "Цель lead в счётчике уже была."}`, "success");
    f.campaign_keys.value = ""; f.note.value = "";
    await loadMappings();
  } catch (err) { say($("mapMsg"), err.message, "error"); }
});

async function loadMappings() {
  const [m, u] = await Promise.all([api("/api/lk/mappings"), api("/api/lk/unmapped")]);
  state.mappings = m.items; state.unmapped = u.items;
  const conn = Object.fromEntries(state.connections.map((c) => [c.id, c.label]));
  $("mapRows").innerHTML = state.mappings.map((x) => `<tr>
      <td><code>${esc(x.campaign_key)}</code></td><td>${esc(x.counter_id)}</td><td>${esc(conn[x.connection_id] || "отключён")}</td>
      <td>${esc(x.note)}</td><td class="lk-actions"><button class="button button-ghost button-small" data-delmap="${x.id}">Убрать</button></td></tr>`).join("")
    || `<tr><td colspan="5" class="empty">Привязок пока нет</td></tr>`;
  const box = $("unmappedBox");
  if (state.unmapped.length) {
    box.classList.remove("hidden");
    box.innerHTML = `<b>Нашли кампании без счётчика (${state.unmapped.length}).</b> Лиды из них пока не отправляются. Нажмите на ID, чтобы подставить в форму:<div class="lk-unmapped">` +
      state.unmapped.map((x) => `<button class="button button-ghost button-small" type="button" data-fill="${esc(x.campaign_key)}">${esc(x.campaign_key)} · ${x.leads} лид.</button>`).join("") + `</div>`;
  } else box.classList.add("hidden");
  $("stUnmapped").textContent = state.unmapped.length;
}

$("unmappedBox").addEventListener("click", (e) => {
  const k = e.target.dataset.fill;
  if (!k) return;
  const ta = $("mapKeys");
  ta.value = (ta.value.trim() ? ta.value.trim() + ", " : "") + k;
  $("mapForm").scrollIntoView({ behavior: "smooth", block: "center" });
});
$("mapRows").addEventListener("click", async (e) => {
  const id = e.target.dataset.delmap;
  if (!id || !confirm("Убрать привязку? Лиды этой кампании перестанут отправляться.")) return;
  try { await api("/api/lk/mappings/" + id, { method: "DELETE" }); await loadMappings(); } catch (err) { alert(err.message); }
});

// ───── журнал ─────
const STATUS = { ok: ["ok", "отправлено"], dry: ["warn", "пробно"], failed: ["err", "ошибка"], skipped_no_id: ["warn", "нет идентификатора"] };
async function loadLog() {
  const f = $("logFilter").value;
  const data = await api("/api/lk/log?limit=100" + (f ? "&status=" + f : ""));
  const c = data.counts_24h || {};
  $("stOk").textContent = c.ok || 0; $("stDry").textContent = c.dry || 0; $("stFail").textContent = c.failed || 0;
  $("logRows").innerHTML = data.items.map((l) => {
    const [cls, label] = STATUS[l.status] || ["", l.status];
    return `<tr><td>${fmtTime(l.created_at)}</td><td>${esc(l.source_name || "")}</td><td>${esc(l.lead_id)}</td>
      <td><code>${esc(l.campaign_key)}</code></td><td>${esc(l.counter_id ?? "")}</td><td>${esc(l.ident_type)}</td>
      <td>${l.revenue ?? ""}</td><td><span class="lk-tag ${cls}">${label}</span>${l.error ? `<div class="lk-status">${esc(l.error)}</div>` : ""}</td></tr>`;
  }).join("") || `<tr><td colspan="8" class="empty">Записей пока нет</td></tr>`;
}
$("logFilter").addEventListener("change", loadLog);

// ───── пользователи (админ) ─────
async function loadUsers() {
  if (!window.LK.isAdmin) return;
  const data = await api("/api/lk/users");
  $("userRows").innerHTML = data.items.map((u) => `<tr><td><b>${esc(u.username)}</b></td><td>${fmtTime(u.created_at)}</td>
      <td class="lk-actions"><button class="button button-ghost button-small" data-deluser="${esc(u.username)}">Удалить</button></td></tr>`).join("")
    || `<tr><td colspan="3" class="empty">Пока только администратор</td></tr>`;
}
if (window.LK.isAdmin) {
  $("userForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api("/api/lk/users", { method: "POST", body: { username: f.username.value.trim(), password: f.password.value } });
      say($("userMsg"), "Пользователь создан. Передайте ему логин и пароль.", "success");
      f.reset(); await loadUsers();
    } catch (err) { say($("userMsg"), err.message, "error"); }
  });
  $("userRows").addEventListener("click", async (e) => {
    const u = e.target.dataset.deluser;
    if (!u || !confirm(`Удалить пользователя ${u}? Его источники Б24 будут отключены.`)) return;
    try { await api("/api/lk/users/" + encodeURIComponent(u), { method: "DELETE" }); await loadUsers(); } catch (err) { alert(err.message); }
  });
}

// ───── общая загрузка ─────
async function loadAll() {
  try {
    await loadConnections();
    const s = await api("/api/lk/sources");
    state.sources = s.items;
    $("pollMinutes").textContent = Math.round(s.poll_seconds / 60);
    renderSources();
    await Promise.all([loadMappings(), loadLog(), loadUsers()]);
  } catch (err) { say($("accountsMsg"), err.message, "error"); }
}
$("reloadAll").addEventListener("click", loadAll);

(function oauthReturn() {
  const result = new URLSearchParams(location.search).get("metrika_oauth");
  if (!result) return;
  const msg = {
    connected: ["Яндекс ID подключён.", "success"],
    denied: ["Доступ не был разрешён. Повторите подключение и подтвердите права.", "error"],
    invalid_state: ["Сессия подключения истекла. Запустите подключение ещё раз.", "error"],
    permissions: ["Токен получен, но у аккаунта нет доступа к Метрике.", "error"],
  }[result] || ["Не удалось подключить Яндекс ID.", "error"];
  history.replaceState({}, document.title, location.pathname);
  loadAll().then(() => say($("accountsMsg"), msg[0], msg[1]));
  window.__skipInitialLoad = true;
})();
if (!window.__skipInitialLoad) loadAll();
