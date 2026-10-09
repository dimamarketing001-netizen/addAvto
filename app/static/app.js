const $ = (selector) => document.querySelector(selector);
const byId = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, function (c) {
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];
  });
}
function money(value) {
  if (value === null || value === undefined || value === "") return "—";
  return new Intl.NumberFormat("ru-RU", {style:"currency",currency:"RUB",maximumFractionDigits:2}).format(Number(value)||0);
}
function num(value) {
  return new Intl.NumberFormat("ru-RU", {maximumFractionDigits:0}).format(Number(value)||0);
}
function modeLabel(mode) {
  return mode === "single_epk" ? "Одна ЕПК" : "Поиск + РСЯ";
}
function strategyLabel(strategy) {
  return strategy === "PAY_FOR_CONVERSION_CRR" ? "ДРР · оплата за конверсии" : "ДРР · оплата за клики";
}
async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: {"Content-Type":"application/json", ...(options.headers || {})},
    ...options
  });
  const type = response.headers.get("content-type") || "";
  const body = type.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(body?.detail || body?.message || ("HTTP " + response.status));
  return body;
}
function showMessage(el, message, kind = "") {
  el.textContent = message;
  el.className = ("form-message " + kind).trim();
}
function parseIds(value) {
  if (!value.trim()) return [];
  const raw = value.split(",").filter((x) => x.trim());
  const ids = raw.map((x) => Number(x.trim()));
  if (ids.some((x) => !Number.isInteger(x) || x <= 0)) throw new Error("ID вводите положительными числами через запятую.");
  return [...new Set(ids)];
}

async function loadProjects() {
  const body = byId("projectsRows");
  try {
    const data = await api("/api/projects");
    const projects = data.items || [];
    byId("metricProjects").textContent = num(projects.length);
    byId("metricPlanned").textContent = num(projects.reduce((sum,p) => sum + (p.campaign_mode === "single_epk" ? 1 : 2),0));
    if (!projects.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty">Проектов пока нет. Создайте первый шаблон выше.</td></tr>';
      return;
    }
    body.innerHTML = projects.map((p) => {
      return '<tr>' +
        '<td><div class="project-title">' + esc(p.name) + '</div><div class="project-domain">' + esc(p.domain) + '</div></td>' +
        '<td><span class="mode-badge">' + esc(modeLabel(p.campaign_mode)) + '</span></td>' +
        '<td>' + esc(strategyLabel(p.strategy)) + '</td>' +
        '<td>' + num(p.target_drr) + '%</td>' +
        '<td>' + money(p.budget) + '</td>' +
        '<td>' + ((p.account_ids || []).length ? esc((p.account_ids || []).join(", ")) : "—") + '</td>' +
        '<td><div class="row-actions"><button class="link-button" data-plan="' + p.id + '">План</button><button class="link-button danger" data-delete="' + p.id + '">Удалить</button></div></td>' +
        '</tr>';
    }).join("");
  } catch (error) {
    body.innerHTML = '<tr><td colspan="7" class="empty">Ошибка загрузки: ' + esc(error.message) + '</td></tr>';
  }
}
function getProjectPayload(form) {
  const f = new FormData(form);
  const imageText = String(f.get("image_urls") || "");
  return {
    name: String(f.get("name") || "").trim(),
    domain: String(f.get("domain") || "").trim(),
    campaign_mode: String(f.get("campaign_mode") || "separate"),
    strategy: String(f.get("strategy") || "AVERAGE_CRR"),
    target_drr: Number(f.get("target_drr")),
    budget: Number(f.get("budget")),
    lead_value: Number(f.get("lead_value") || 0),
    headline: String(f.get("headline") || "").trim(),
    ad_text: String(f.get("ad_text") || "").trim(),
    keywords: String(f.get("keywords") || "").trim(),
    negative_keywords: String(f.get("negative_keywords") || "").trim(),
    image_urls: imageText.split(String.fromCharCode(10)).map(x => x.trim()).filter(Boolean),
    account_ids: parseIds(String(f.get("account_ids") || "")),
    counter_id: f.get("counter_id") ? Number(f.get("counter_id")) : null,
    goal_id: f.get("goal_id") ? Number(f.get("goal_id")) : null,
    region_ids: parseIds(String(f.get("region_ids") || ""))
  };
}
byId("projectForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  const msg = byId("formMessage");
  try {
    const payload = getProjectPayload(event.currentTarget);
    button.disabled = true;
    await api("/api/projects", {method:"POST", body:JSON.stringify(payload)});
    event.currentTarget.reset();
    showMessage(msg, "Проект сохранён. Откройте «План», чтобы проверить выбранную структуру кампаний.", "success");
    await loadProjects();
  } catch (error) {
    showMessage(msg, error.message, "error");
  } finally {
    button.disabled = false;
  }
});

byId("projectsRows").addEventListener("click", async (event) => {
  const planButton = event.target.closest("[data-plan]");
  const deleteButton = event.target.closest("[data-delete]");
  if (planButton) {
    const panel = byId("planOutput");
    panel.classList.remove("hidden");
    panel.innerHTML = '<div class="muted small">Формирую план…</div>';
    try {
      const plan = await api("/api/projects/" + planButton.dataset.plan + "/plan", {method:"POST", body:"{}"});
      const items = plan.items.map((item) => {
        return '<div class="plan-item"><strong>' + esc(item.name) + ' · ' + esc(item.placement) + '</strong><p>' + esc(item.note) + '</p></div>';
      }).join("");
      panel.innerHTML = '<h3>План кампаний · проект #' + plan.project_id + '</h3>' +
        '<div class="muted small">Режим: ' + esc(modeLabel(plan.campaign_mode)) + ' · ' + esc(strategyLabel(plan.strategy)) + '</div>' +
        items + '<div class="notice notice-warning" style="margin:12px 0 0">' + esc(plan.warning) + '</div>';
      panel.scrollIntoView({behavior:"smooth",block:"nearest"});
    } catch (error) {
      panel.textContent = error.message;
    }
  }
  if (deleteButton && confirm("Удалить сохранённый проект?")) {
    try {
      await api("/api/projects/" + deleteButton.dataset.delete, {method:"DELETE"});
      await loadProjects();
      byId("planOutput").classList.add("hidden");
    } catch (error) { alert(error.message); }
  }
});
byId("reloadProjects").addEventListener("click", loadProjects);

async function checkConnection() {
  const button = byId("checkConnection");
  const msg = byId("connectionMessage");
  button.disabled = true;
  msg.className = "connection-message";
  msg.textContent = "Запрашиваю профиль, интеграции и кабинеты Click.ru…";
  try {
    const data = await api("/api/connection");
    if (!data.connected) throw new Error(data.message || "Не удалось подключиться");
    msg.className = "connection-message ok";
    msg.textContent = "Подключение работает. Пользователь: " + (data.user?.email || data.user?.login || data.user?.id || "доступ подтверждён") + ".";
    byId("metricConnection").textContent = "Подключено";
    byId("metricAccounts").textContent = num(data.accounts.length);
    byId("accountsFoot").textContent = data.integrations.length + " интеграций найдено";
    byId("integrationsList").innerHTML = data.integrations.length ? data.integrations.map((i) => {
      return '<div class="list-item"><strong>' + esc(i.name || i.title || i.service || "Интеграция") + ' · #' + esc(i.id) + '</strong><small>' + esc(i.service || "") + ' · ' + esc(i.state || i.status || "состояние не указано") + '</small></div>';
    }).join("") : '<div class="empty">Интеграции Yandex Direct не найдены. Проверьте подключение Яндекс ID в Click.ru.</div>';
    byId("accountsList").innerHTML = data.accounts.length ? data.accounts.map((a) => {
      return '<div class="list-item"><strong>' + esc(a.name || "Рекламный аккаунт") + ' · #' + esc(a.id) + '</strong><small>' + esc(a.service || "SERVICE не указан") + ' · ' + esc(a.status || a.state || "") + '</small></div>';
    }).join("") : '<div class="empty">Рекламные аккаунты не найдены.</div>';
  } catch (error) {
    msg.className = "connection-message error";
    msg.textContent = error.message;
    byId("metricConnection").textContent = "Ошибка";
    byId("metricAccounts").textContent = "—";
    byId("accountsFoot").textContent = "Проверьте .env и права доступа";
  } finally {
    button.disabled = false;
  }
}
byId("checkConnection").addEventListener("click", checkConnection);
byId("refreshAll").addEventListener("click", async () => { await loadProjects(); await checkConnection(); });
function isoDate(daysOffset = 0) {
  const d = new Date();
  d.setDate(d.getDate() + daysOffset);
  return d.toISOString().slice(0,10);
}
byId("reportForm").elements.date_from.value = isoDate(-7);
byId("reportForm").elements.date_to.value = isoDate(-1);
byId("reportForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const f = new FormData(event.currentTarget);
  const from = String(f.get("date_from"));
  const to = String(f.get("date_to"));
  const msg = byId("reportMessage");
  const rows = byId("reportRows");
  const submit = event.submitter;
  if (to < from) { showMessage(msg, "Дата окончания раньше даты начала.", "error"); return; }
  try {
    submit.disabled = true;
    showMessage(msg, "Загружаю статистику Click.ru…");
    const ids = String(f.get("account_ids") || "").trim();
    const qs = new URLSearchParams({date_from:from,date_to:to});
    if (ids) { parseIds(ids); qs.set("account_ids",ids); }
    const report = await api("/api/report?" + qs.toString());
    rows.innerHTML = report.items.length ? report.items.map((r) => {
      return '<tr><td>' + esc(r.date) + '</td><td>' + esc(r.account_id || "—") + '</td><td>' + esc(r.campaign_id || "—") + '</td>' +
        '<td>' + num(r.impressions) + '</td><td>' + num(r.clicks) + '</td><td>' + money(r.spend) + '</td>' +
        '<td>' + (r.clicks ? money(r.spend/r.clicks) : "—") + '</td><td class="lead-pending">Не подключено</td></tr>';
    }).join("") : '<tr><td colspan="8" class="empty">Нет данных за выбранный период.</td></tr>';
    const t = report.totals;
    byId("reportTotals").innerHTML = [
      ["Показы",num(t.impressions)],["Клики",num(t.clicks)],["Расход без НДС",money(t.spend)],["Средняя цена клика",t.cpc === null ? "—" : money(t.cpc)]
    ].map(([label,value]) => '<div class="total-tile"><span>' + esc(label) + '</span><strong>' + esc(value) + '</strong></div>').join("");
    byId("reportNote").textContent = report.conversions_note;
    byId("reportNote").classList.remove("hidden");
    showMessage(msg, "Получено строк: " + report.items.length + ". Конверсии lead пока не выгружаются.", "success");
  } catch (error) {
    showMessage(msg, error.message, "error");
    rows.innerHTML = '<tr><td colspan="8" class="empty">' + esc(error.message) + '</td></tr>';
  } finally {
    submit.disabled = false;
  }
});
loadProjects();
