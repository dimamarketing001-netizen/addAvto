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
    if (!projects.length) {
      body.innerHTML = '<tr><td colspan="4" class="empty">Проектов пока нет. Нажмите «+ Новый проект» выше.</td></tr>';
      return;
    }
    body.innerHTML = projects.map((p) => {
      return '<tr>' +
        '<td><div class="project-title">' + esc(p.name) + '</div><div class="project-domain">' + esc(p.domain) + '</div></td>' +
        '<td><span class="mode-badge">' + esc(modeLabel(p.campaign_mode)) + '</span></td>' +
        '<td>' + num(p.target_drr) + '%</td>' +
        '<td><div class="row-actions"><button class="link-button" data-plan="' + p.id + '">План</button><button class="link-button" data-bulk="' + p.id + '">Массовый запуск</button><button class="link-button" data-history="' + p.id + '">История</button><button class="link-button danger" data-delete="' + p.id + '">Удалить</button></div></td>' +
        '</tr>';
    }).join("");
  } catch (error) {
    body.innerHTML = '<tr><td colspan="4" class="empty">Ошибка загрузки: ' + esc(error.message) + '</td></tr>';
  }
}
byId("newProjectButton").addEventListener("click", () => openWizard(null));

let bulkCreatedTotal = 0;

byId("projectsRows").addEventListener("click", async (event) => {
  const planButton = event.target.closest("[data-plan]");
  const bulkButton = event.target.closest("[data-bulk]");
  const historyButton = event.target.closest("[data-history]");
  const deleteButton = event.target.closest("[data-delete]");
  if (bulkButton) {
    openWizard(Number(bulkButton.dataset.bulk));
    return;
  }
  if (historyButton) {
    await showCampaignHistory(Number(historyButton.dataset.history));
    return;
  }
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
      byId("bulkOutput").classList.add("hidden");
    } catch (error) { alert(error.message); }
  }
});
byId("reloadProjects").addEventListener("click", loadProjects);

byId("bulkOutput").addEventListener("click", async (event) => {
  const activateButton = event.target.closest("[data-activate-campaign]");
  const suspendButton = event.target.closest("[data-suspend-campaign]");
  if (activateButton) {
    const accountId = activateButton.dataset.activateAccount;
    const campaignId = activateButton.dataset.activateCampaign;
    const connectionId = activateButton.dataset.activateConnection;
    if (!confirm(
      "Запустить показы по кампании #" + campaignId + "? Это реальный запуск рекламы — " +
      "бюджет начнёт расходоваться по выбранной стратегии ДРР. Отменить этот шаг нельзя, " +
      "только отдельно остановить кампанию после."
    )) return;
    activateButton.disabled = true;
    activateButton.textContent = "Запускаю…";
    try {
      await api(
        "/api/accounts/" + accountId + "/campaigns/" + campaignId + "/activate?connection_id=" + connectionId,
        {method: "POST"}
      );
      activateButton.textContent = "Запущено ✓";
    } catch (error) {
      activateButton.disabled = false;
      activateButton.textContent = "Активировать (запустить показы)";
      alert(error.message);
    }
    return;
  }
  if (suspendButton) {
    const accountId = suspendButton.dataset.suspendAccount;
    const campaignId = suspendButton.dataset.suspendCampaign;
    const connectionId = suspendButton.dataset.suspendConnection;
    if (!confirm("Остановить показы по кампании #" + campaignId + "? Расход бюджета прекратится, кампанию можно будет запустить снова позже.")) return;
    suspendButton.disabled = true;
    suspendButton.textContent = "Останавливаю…";
    try {
      await api(
        "/api/accounts/" + accountId + "/campaigns/" + campaignId + "/suspend?connection_id=" + connectionId,
        {method: "POST"}
      );
      suspendButton.textContent = "Остановлено ✓";
    } catch (error) {
      suspendButton.disabled = false;
      suspendButton.textContent = "Остановить";
      alert(error.message);
    }
  }
});

async function loadClickruConnections() {
  const rows = byId("connRows");
  try {
    const data = await api("/api/clickru-connections");
    const items = data.items || [];
    byId("metricConnection").textContent = num(items.length);
    rows.innerHTML = items.length ? items.map((c) => {
      return '<tr><td>' + esc(c.label) + '</td><td>' + esc(c.token_preview) + '</td><td>' + esc(c.user_id || "—") + '</td>' +
        '<td><button class="link-button danger" data-delete-conn="' + c.id + '">Удалить</button></td></tr>';
    }).join("") : '<tr><td colspan="4" class="empty">Подключений пока нет — добавьте первое выше.</td></tr>';
    return items;
  } catch (error) {
    rows.innerHTML = '<tr><td colspan="4" class="empty">Ошибка загрузки: ' + esc(error.message) + '</td></tr>';
    return [];
  }
}
byId("connAddForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = event.submitter;
  const msg = byId("connAddMessage");
  const f = new FormData(form);
  try {
    button.disabled = true;
    showMessage(msg, "Проверяю токен в Click.ru…");
    await api("/api/clickru-connections", {
      method: "POST",
      body: JSON.stringify({
        label: String(f.get("label") || "").trim(),
        token: String(f.get("token") || "").trim(),
        user_id: String(f.get("user_id") || "").trim()
      })
    });
    form.reset();
    showMessage(msg, "Аккаунт Click.ru добавлен и проверен.", "success");
    await loadClickruConnections();
  } catch (error) {
    showMessage(msg, error.message, "error");
  } finally {
    button.disabled = false;
  }
});
byId("connRows").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete-conn]");
  if (!button || !confirm("Удалить этот аккаунт Click.ru? Проекты, уже запущенные под ним, не затронутся.")) return;
  try {
    await api("/api/clickru-connections/" + button.dataset.deleteConn, {method: "DELETE"});
    await loadClickruConnections();
  } catch (error) { alert(error.message); }
});

const wizard = {
  projectId: null, project: null, step: 1,
  connections: [], connectionId: null,
  payers: [], payerId: null,
  freeCount: null, count: 1,
  creativeMode: "shuffle",
  adsPerGroup: 1,
  pool: {headlines: "", ad_texts: "", image_urls: "", callouts: "", sitelinks: ""},
  manualSets: [],
  regionSelections: null,
};
const WIZARD_STEP_LABELS = ["1. Проект", "2. Аккаунт Click.ru", "3. Юрлицо", "4. Количество", "5. Креативы"];
function wizardStepsNav(activeStep) {
  return '<div class="wizard-steps">' + WIZARD_STEP_LABELS.map((label, i) =>
    '<span' + (i + 1 === activeStep ? ' class="active"' : '') + '>' + esc(label) + '</span>'
  ).join("") + '</div>';
}
function wizardClose() { byId("wizardOverlay").classList.add("hidden"); }
byId("wizardClose").addEventListener("click", wizardClose);
byId("wizardOverlay").addEventListener("click", (event) => { if (event.target.id === "wizardOverlay") wizardClose(); });
byId("wizardBody").addEventListener("change", (event) => {
  if (event.target.name === "wizConn") wizard.connectionId = Number(event.target.value);
  if (event.target.name === "wizPayer") wizard.payerId = Number(event.target.value);
});
document.addEventListener("click", (event) => {
  const suggestionsBox = byId("wizPRegionSuggestions");
  const searchInput = byId("wizPRegionSearch");
  if (suggestionsBox && !suggestionsBox.classList.contains("hidden") &&
      !suggestionsBox.contains(event.target) && event.target !== searchInput) {
    suggestionsBox.classList.add("hidden");
  }
});

function seedCreativePools(project) {
  const p = project || {};
  const seedHeadlines = (p.headlines && p.headlines.length) ? p.headlines : [];
  const seedAdTexts = (p.ad_texts && p.ad_texts.length) ? p.ad_texts : [];
  const seedSitelinks = (p.sitelinks_pool || []).map((sl) => [sl.title, sl.href, sl.description || ""].join("|"));
  wizard.pool = {
    headlines: seedHeadlines.join("\n"),
    ad_texts: seedAdTexts.join("\n"),
    image_urls: (p.image_urls || []).join("\n"),
    callouts: (p.callouts_pool || []).join("\n"),
    sitelinks: seedSitelinks.join("\n"),
  };
}

async function openWizard(projectId) {
  let project = null;
  if (projectId) {
    try {
      const data = await api("/api/projects");
      project = (data.items || []).find((p) => p.id === projectId);
    } catch (error) { alert(error.message); return; }
    if (!project) { alert("Проект не найден, обновите список."); return; }
  }
  wizard.projectId = projectId || null;
  wizard.project = project;
  wizard.step = 1;
  wizard.connectionId = null;
  wizard.payerId = null;
  wizard.count = 1;
  wizard.creativeMode = "shuffle";
  wizard.adsPerGroup = 1;
  seedCreativePools(project);
  wizard.manualSets = [];
  wizard.regionSelections = null;
  byId("wizardOverlay").classList.remove("hidden");
  renderWizardStep1();
}

let geoRegionsCache = null;
async function loadGeoRegions() {
  if (geoRegionsCache) return geoRegionsCache;
  try {
    const data = await api("/api/geo-regions");
    geoRegionsCache = data.items || [];
  } catch (error) {
    geoRegionsCache = [];
  }
  return geoRegionsCache;
}
function geoRegionLabel(region) {
  const parent = region.parents && region.parents.length ? region.parents[region.parents.length - 1] : "";
  return parent ? region.name + " (" + parent + ")" : region.name;
}

async function renderWizardStep1() {
  const body = byId("wizardBody");
  const p = wizard.project || {};
  if (!wizard.regionSelections) {
    const regions = await loadGeoRegions();
    const byId_ = new Map(regions.map((r) => [r.id, r]));
    wizard.regionSelections = (p.region_ids || []).map((id) => {
      const r = byId_.get(id);
      return {id, label: r ? geoRegionLabel(r) : ("ID " + id)};
    });
  }
  body.innerHTML = wizardStepsNav(1) +
    '<div class="form-grid">' +
      '<label class="field field-wide">Название проекта<input id="wizPName" value="' + esc(p.name || "") + '" required maxlength="120" placeholder="Например, БФЛ — Казань"></label>' +
      '<label class="field field-wide">Домен / посадочная страница<input id="wizPDomain" value="' + esc(p.domain || "") + '" required placeholder="https://example.ru/"></label>' +
      '<fieldset class="field field-wide mode-picker"><legend>Структура рекламных кампаний</legend>' +
        '<label class="mode-option"><input type="radio" name="wizPMode" value="separate"' + ((p.campaign_mode || "separate") === "separate" ? " checked" : "") + '><span class="mode-icon">Ⅱ</span><span><strong>Отдельные РК</strong><small>Одна кампания Поиск + одна кампания РСЯ.</small></span></label>' +
        '<label class="mode-option"><input type="radio" name="wizPMode" value="single_epk"' + (p.campaign_mode === "single_epk" ? " checked" : "") + '><span class="mode-icon">▣</span><span><strong>Одна ЕПК</strong><small>Единая перфоманс-кампания, Поиск+РСЯ вместе.</small></span></label>' +
      '</fieldset>' +
      '<label class="field">Целевой ДРР, %<input id="wizPDrr" type="number" min="0.1" max="1000" step="0.1" value="' + esc(p.target_drr ?? 30) + '" required></label>' +
      '<label class="field">Корректировка ставки для мобильных, % (необязательно)<input id="wizPMobile" type="number" min="-100" max="1200" value="' + esc(p.mobile_bid_adjustment_percent ?? "") + '" placeholder="например, -50"></label>' +
      '<label class="field field-wide">Ключевые фразы — по одной на строку<textarea id="wizPKeywords" rows="3" placeholder="юрист по банкротству&#10;списание долгов">' + esc(p.keywords || "") + '</textarea></label>' +
      '<label class="field field-wide">Минус-фразы — по одной на строку (общие для группы, у Яндекса лимит 3 фразы)<textarea id="wizPNegative" rows="2" placeholder="бесплатно&#10;вакансии">' + esc(p.negative_keywords || "") + '</textarea></label>' +
      '<label class="field field-wide">Город или регион показа<input id="wizPRegionSearch" autocomplete="off" placeholder="Начните вводить название города или региона…">' +
        '<div id="wizPRegionSuggestions" class="geo-suggestions hidden"></div>' +
        '<div id="wizPRegionChips" class="geo-chips"></div>' +
      '</label>' +
    '</div>' +
    '<div class="wizard-actions"><span></span><button class="button button-primary" id="wizNext1" type="button">Далее →</button></div>' +
    '<div id="wizPMessage" class="form-message" role="status"></div>';

  renderGeoChips();
  const searchInput = body.querySelector("#wizPRegionSearch");
  let searchDebounce = null;
  searchInput.addEventListener("input", () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => renderGeoSuggestions(searchInput.value), 150);
  });
  searchInput.addEventListener("focus", () => renderGeoSuggestions(searchInput.value));

  body.querySelector("#wizNext1").addEventListener("click", wizardSaveProjectStep);
}

function renderGeoSuggestions(query) {
  const suggestionsBox = byId("wizPRegionSuggestions");
  if (!suggestionsBox) return;
  const term = query.trim().toLowerCase().replace(/ё/g, "е");
  if (!term) { suggestionsBox.classList.add("hidden"); suggestionsBox.innerHTML = ""; return; }
  const selectedIds = new Set(wizard.regionSelections.map((r) => r.id));
  const matches = (geoRegionsCache || [])
    .filter((r) => !selectedIds.has(r.id) && r.name.toLowerCase().replace(/ё/g, "е").includes(term))
    .slice(0, 20);
  if (!matches.length) {
    suggestionsBox.innerHTML = '<div class="geo-suggestion" style="cursor:default;color:#8494a8">Ничего не найдено</div>';
    suggestionsBox.classList.remove("hidden");
    return;
  }
  suggestionsBox.innerHTML = matches.map((r) =>
    '<button type="button" class="geo-suggestion" data-region-id="' + r.id + '">' + esc(r.name) +
    (r.parents && r.parents.length ? '<small>' + esc(r.parents[r.parents.length - 1]) + '</small>' : '') +
    '</button>'
  ).join("");
  suggestionsBox.classList.remove("hidden");
  suggestionsBox.querySelectorAll("[data-region-id]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = Number(btn.dataset.regionId);
      const region = (geoRegionsCache || []).find((r) => r.id === id);
      wizard.regionSelections.push({id, label: region ? geoRegionLabel(region) : ("ID " + id)});
      byId("wizPRegionSearch").value = "";
      suggestionsBox.classList.add("hidden");
      renderGeoChips();
    });
  });
}

function renderGeoChips() {
  const box = byId("wizPRegionChips");
  if (!box) return;
  box.innerHTML = wizard.regionSelections.map((r) =>
    '<span class="geo-chip">' + esc(r.label) + '<button type="button" data-remove-region="' + r.id + '" aria-label="Убрать">×</button></span>'
  ).join("");
  box.querySelectorAll("[data-remove-region]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = Number(btn.dataset.removeRegion);
      wizard.regionSelections = wizard.regionSelections.filter((r) => r.id !== id);
      renderGeoChips();
    });
  });
}

async function wizardSaveProjectStep() {
  const msg = byId("wizPMessage");
  const button = byId("wizNext1");
  const name = byId("wizPName").value.trim();
  const domain = byId("wizPDomain").value.trim();
  const campaignMode = document.querySelector('input[name="wizPMode"]:checked').value;
  const targetDrr = Number(byId("wizPDrr").value);
  const mobileRaw = byId("wizPMobile").value.trim();
  const keywords = byId("wizPKeywords").value.trim();
  const negativeKeywords = byId("wizPNegative").value.trim();
  if (!name || !domain) { showMessage(msg, "Укажите название и домен.", "error"); return; }
  if (!keywords) { showMessage(msg, "Нужна хотя бы одна ключевая фраза.", "error"); return; }
  const regionIds = (wizard.regionSelections || []).map((r) => r.id);
  if (!regionIds.length) { showMessage(msg, "Выберите хотя бы один город или регион.", "error"); return; }

  const payload = {
    ...(wizard.project || {}),
    name, domain, campaign_mode: campaignMode, target_drr: targetDrr,
    keywords, negative_keywords: negativeKeywords, region_ids: regionIds,
    mobile_bid_adjustment_percent: mobileRaw ? Number(mobileRaw) : null,
  };
  button.disabled = true;
  showMessage(msg, wizard.projectId ? "Сохраняю проект…" : "Создаю проект…");
  try {
    const saved = wizard.projectId
      ? await api("/api/projects/" + wizard.projectId, {method: "PUT", body: JSON.stringify(payload)})
      : await api("/api/projects", {method: "POST", body: JSON.stringify(payload)});
    wizard.project = saved;
    wizard.projectId = saved.id;
    seedCreativePools(saved);
    loadProjects();
    wizard.step = 2;
    renderWizardStep2();
  } catch (error) {
    showMessage(msg, error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function renderWizardStep2() {
  const body = byId("wizardBody");
  body.innerHTML = wizardStepsNav(2) + '<div class="muted small">Загружаю подключения…</div>';
  const connections = await loadClickruConnections();
  wizard.connections = connections;
  const list = connections.length ? connections.map((c) => (
    '<label class="wizard-radio"><input type="radio" name="wizConn" value="' + c.id + '"' + (wizard.connectionId === c.id ? " checked" : "") + '><span><strong>' + esc(c.label) + '</strong><small>' + esc(c.token_preview) + (c.user_id ? " · User ID " + esc(c.user_id) : "") + '</small></span></label>'
  )).join("") : '<div class="empty">Подключений пока нет — добавьте первое ниже.</div>';
  body.innerHTML = wizardStepsNav(2) +
    '<div class="wizard-list">' + list + '</div>' +
    '<button class="button button-ghost button-small wizard-add" id="wizAddConnToggle" type="button">+ Добавить ещё аккаунт Click.ru</button>' +
    '<form id="wizAddConnForm" class="account-create-form hidden">' +
      '<label class="field">Название<input name="label" required minlength="2" maxlength="120"></label>' +
      '<label class="field">API-токен<input name="token" type="password" required minlength="10"></label>' +
      '<label class="field">User ID (необязательно)<input name="user_id"></label>' +
      '<button class="button button-primary" type="submit">Добавить и проверить</button>' +
    '</form>' +
    '<div id="wizAddConnMessage" class="form-message"></div>' +
    '<div class="wizard-actions"><button class="button button-ghost" id="wizBack2" type="button">← Назад</button><button class="button button-primary" id="wizNext2" type="button">Далее →</button></div>';
  body.querySelector("#wizAddConnToggle").addEventListener("click", () => {
    body.querySelector("#wizAddConnForm").classList.toggle("hidden");
  });
  body.querySelector("#wizAddConnForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = new FormData(event.currentTarget);
    const msg = body.querySelector("#wizAddConnMessage");
    try {
      showMessage(msg, "Проверяю токен…");
      const created = await api("/api/clickru-connections", {method: "POST", body: JSON.stringify({
        label: String(f.get("label") || "").trim(),
        token: String(f.get("token") || "").trim(),
        user_id: String(f.get("user_id") || "").trim(),
      })});
      wizard.connectionId = created.id;
      await renderWizardStep2();
    } catch (error) { showMessage(msg, error.message, "error"); }
  });
  body.querySelector("#wizBack2").addEventListener("click", () => { wizard.step = 1; renderWizardStep1(); });
  body.querySelector("#wizNext2").addEventListener("click", () => {
    if (!wizard.connectionId) { alert("Выберите аккаунт Click.ru."); return; }
    wizard.step = 3;
    renderWizardStep3();
  });
}

async function renderWizardStep3() {
  const body = byId("wizardBody");
  body.innerHTML = wizardStepsNav(3) + '<div class="muted small">Загружаю юрлица для маркировки…</div>';
  try {
    const data = await api("/api/payers?connection_id=" + wizard.connectionId);
    wizard.payers = data.items || [];
  } catch (error) {
    body.innerHTML = wizardStepsNav(3) +
      '<div class="notice notice-warning">Не удалось получить юрлица: ' + esc(error.message) + '</div>' +
      '<div class="wizard-actions"><button class="button button-ghost" id="wizBack3" type="button">← Назад</button></div>';
    body.querySelector("#wizBack3").addEventListener("click", () => { wizard.step = 2; renderWizardStep2(); });
    return;
  }
  const list = wizard.payers.length ? wizard.payers.map((p) => (
    '<label class="wizard-radio"><input type="radio" name="wizPayer" value="' + p.id + '"' + (wizard.payerId === p.id ? " checked" : "") + '><span><strong>' + esc(p.name || ("Юрлицо #" + p.id)) + '</strong><small>Баланс: ' + money(p.balance) + '</small></span></label>'
  )).join("") : '<div class="notice notice-warning">Юрлица для маркировки не найдены — настройте их в кабинете Click.ru. Можно продолжить без выбора, но активировать показы без маркировки не получится.</div>';
  body.innerHTML = wizardStepsNav(3) +
    '<div class="notice notice-info">Юрлицо используется для маркировки рекламы (ОРД/ERID) — обязательно перед реальным запуском показов.</div>' +
    '<div class="wizard-list">' + list + '</div>' +
    '<div class="wizard-actions"><button class="button button-ghost" id="wizBack3" type="button">← Назад</button><button class="button button-primary" id="wizNext3" type="button">Далее →</button></div>';
  body.querySelector("#wizBack3").addEventListener("click", () => { wizard.step = 2; renderWizardStep2(); });
  body.querySelector("#wizNext3").addEventListener("click", () => {
    if (wizard.payers.length && !wizard.payerId) { alert("Выберите юрлицо для маркировки — без него нельзя будет запустить показы."); return; }
    wizard.step = 4;
    renderWizardStep4();
  });
}

async function renderWizardStep4() {
  const body = byId("wizardBody");
  body.innerHTML = wizardStepsNav(4) + '<div class="muted small">Проверяю свободные аккаунты в пуле…</div>';
  let free = null, total = null;
  try {
    const data = await api("/api/parsya-pool/accounts");
    if (data.configured) {
      const items = data.items || [];
      free = items.filter((a) => a.is_free).length;
      total = items.length;
    }
  } catch (error) { /* показываем как "не удалось получить статус" ниже */ }
  wizard.freeCount = free;
  const hint = free === null ? "Не удалось получить статус пула ParsYa." : ("Свободно в пуле: " + free + " из " + total + ".");
  const maxAllowed = free && free > 0 ? Math.min(free, 20) : 20;
  body.innerHTML = wizardStepsNav(4) +
    '<div class="notice notice-info">' + esc(hint) + ' Каждый аккаунт — это НОВЫЙ Direct-аккаунт, созданный из пула, со своей кампанией.</div>' +
    '<label class="field">Сколько аккаунтов запустить<input id="wizCount" type="number" min="1" max="' + maxAllowed + '" value="' + wizard.count + '"></label>' +
    '<div class="wizard-actions"><button class="button button-ghost" id="wizBack4" type="button">← Назад</button><button class="button button-primary" id="wizNext4" type="button">Далее →</button></div>';
  body.querySelector("#wizBack4").addEventListener("click", () => { wizard.step = 3; renderWizardStep3(); });
  body.querySelector("#wizNext4").addEventListener("click", () => {
    const value = Number(body.querySelector("#wizCount").value);
    if (!Number.isInteger(value) || value < 1 || value > 20) { alert("Укажите количество от 1 до 20."); return; }
    if (wizard.freeCount !== null && value > wizard.freeCount) {
      if (!confirm("В пуле сейчас свободно только " + wizard.freeCount + ". Всё равно продолжить с " + value + "?")) return;
    }
    wizard.count = value;
    wizard.manualSets = Array.from({length: value}, (_, i) => wizard.manualSets[i] || {headline: "", ad_text: "", image_url: ""});
    wizard.step = 5;
    renderWizardStep5();
  });
}

function wizardModeOption(value, title, note) {
  return '<label class="mode-option"><input type="radio" name="wizMode" value="' + value + '"' + (wizard.creativeMode === value ? " checked" : "") + '><span class="mode-icon">●</span><span><strong>' + esc(title) + '</strong><small>' + esc(note) + '</small></span></label>';
}

function renderWizardStep5() {
  const body = byId("wizardBody");
  const structureBlock = '<fieldset class="field field-wide mode-picker"><legend>Структура объявлений в группе каждого аккаунта</legend>' +
    '<label class="mode-option"><input type="radio" name="wizStructure" value="single"' + (wizard.adsPerGroup <= 1 ? " checked" : "") + '><span class="mode-icon">1</span><span><strong>Одно объявление на аккаунт</strong><small>Один фиксированный креатив на кампанию.</small></span></label>' +
    '<label class="mode-option"><input type="radio" name="wizStructure" value="multi"' + (wizard.adsPerGroup > 1 ? " checked" : "") + '><span class="mode-icon">N</span><span><strong>Несколько объявлений в группе</strong><small>До 50 в одной группе — Директ сам подберёт лучшую связку показа. Только с режимом «Скрипт перемешает» ниже.</small></span></label>' +
    '</fieldset>' +
    (wizard.adsPerGroup > 1
      ? '<label class="field">Сколько объявлений в группе (2–50)<input id="wizAdsPerGroup" type="number" min="2" max="50" value="' + wizard.adsPerGroup + '"></label>'
      : "");
  const modeOptions = '<fieldset class="field field-wide mode-picker"><legend>Как собрать креативы на ' + wizard.count + ' аккаунт(ов)</legend>' +
    wizardModeOption("shuffle", "Скрипт перемешает", "Один общий набор заголовков/текстов/картинок — на каждый аккаунт (и объявление, если их несколько в группе) берётся случайный.") +
    wizardModeOption("duplicate_single", "Продублировать один набор", "Один заголовок/текст/картинка — одинаковые на всех аккаунтах.") +
    wizardModeOption("manual_per_account", "Свой набор на каждый аккаунт", "Вручную заполняете " + wizard.count + " разных наборов — по одному на аккаунт.") +
    '</fieldset>';
  const poolForm =
    '<label class="field field-wide">Заголовки — по одному на строку (до 56 символов)<textarea id="wizHeadlines" rows="3">' + esc(wizard.pool.headlines) + '</textarea></label>' +
    '<label class="field field-wide">Тексты объявлений — по одному на строку (до 81 символа)<textarea id="wizAdTexts" rows="3">' + esc(wizard.pool.ad_texts) + '</textarea></label>' +
    '<label class="field field-wide">Ссылки на изображения — по одной на строку (необязательно)<textarea id="wizImages" rows="2">' + esc(wizard.pool.image_urls) + '</textarea></label>' +
    '<label class="field field-wide">Уточнения (callouts) — по одному на строку, до 25 символов (необязательно, до 5 на объявление)<textarea id="wizCallouts" rows="2">' + esc(wizard.pool.callouts) + '</textarea></label>' +
    '<label class="field field-wide">Быстрые ссылки (sitelinks) — по одной на строку: «Заголовок|Ссылка|Описание» (описание необязательно, до 4 на объявление)<textarea id="wizSitelinks" rows="3" placeholder="Доставка|https://example.ru/delivery|Условия доставки">' + esc(wizard.pool.sitelinks) + '</textarea></label>';
  const manualForm = wizard.manualSets.map((set, i) => (
    '<div class="wizard-creative-set"><strong>Аккаунт ' + (i + 1) + '</strong><div class="wizard-creative-grid">' +
      '<label class="field">Заголовок<input data-manual-headline="' + i + '" maxlength="56" value="' + esc(set.headline) + '"></label>' +
      '<label class="field field-wide">Текст объявления<input data-manual-adtext="' + i + '" maxlength="81" value="' + esc(set.ad_text) + '"></label>' +
      '<label class="field field-wide">Ссылка на изображение (необязательно)<input data-manual-image="' + i + '" value="' + esc(set.image_url || "") + '"></label>' +
    '</div></div>'
  )).join("");
  body.innerHTML = wizardStepsNav(5) + structureBlock + modeOptions +
    '<div id="wizCreativeArea">' + (wizard.creativeMode === "manual_per_account" ? manualForm : poolForm) + '</div>' +
    '<div class="wizard-actions"><button class="button button-ghost" id="wizBack5" type="button">← Назад</button><button class="button button-primary" id="wizLaunch" type="button">Запустить массовый запуск</button></div>' +
    '<div id="wizLaunchMessage" class="form-message"></div>';
  body.querySelectorAll('input[name="wizStructure"]').forEach((input) => {
    input.addEventListener("change", (event) => {
      if (event.target.value === "multi") {
        wizard.creativeMode = "shuffle";
        wizard.adsPerGroup = wizard.adsPerGroup > 1 ? wizard.adsPerGroup : 5;
      } else {
        wizard.adsPerGroup = 1;
      }
      renderWizardStep5();
    });
  });
  if (byId("wizAdsPerGroup")) {
    byId("wizAdsPerGroup").addEventListener("input", (event) => {
      const value = Number(event.target.value);
      if (Number.isInteger(value) && value >= 2) wizard.adsPerGroup = Math.min(value, 50);
    });
  }
  body.querySelectorAll('input[name="wizMode"]').forEach((input) => {
    input.addEventListener("change", (event) => {
      if (byId("wizHeadlines")) {
        wizard.pool.headlines = byId("wizHeadlines").value;
        wizard.pool.ad_texts = byId("wizAdTexts").value;
        wizard.pool.image_urls = byId("wizImages").value;
        wizard.pool.callouts = byId("wizCallouts").value;
        wizard.pool.sitelinks = byId("wizSitelinks").value;
      }
      wizard.creativeMode = event.target.value;
      if (wizard.creativeMode !== "shuffle") wizard.adsPerGroup = 1;
      renderWizardStep5();
    });
  });
  body.querySelectorAll("[data-manual-headline]").forEach((input) => {
    input.addEventListener("input", (event) => { wizard.manualSets[Number(event.target.dataset.manualHeadline)].headline = event.target.value; });
  });
  body.querySelectorAll("[data-manual-adtext]").forEach((input) => {
    input.addEventListener("input", (event) => { wizard.manualSets[Number(event.target.dataset.manualAdtext)].ad_text = event.target.value; });
  });
  body.querySelectorAll("[data-manual-image]").forEach((input) => {
    input.addEventListener("input", (event) => { wizard.manualSets[Number(event.target.dataset.manualImage)].image_url = event.target.value; });
  });
  body.querySelector("#wizBack5").addEventListener("click", () => { wizard.step = 4; renderWizardStep4(); });
  body.querySelector("#wizLaunch").addEventListener("click", wizardLaunch);
}

function renderCampaignItems(items, connectionId) {
  return items.map((item) => {
    if (item.status === "failed") {
      return '<div class="plan-item error"><strong>Ошибка</strong><p>' + esc(item.error || "Неизвестная ошибка") + '</p></div>';
    }
    const activateButton = '<button class="button button-ghost button-small" data-activate-account="' + item.account_id +
      '" data-activate-campaign="' + item.campaign_id + '" data-activate-connection="' + connectionId +
      '" type="button">Активировать (запустить показы)</button>';
    const suspendButton = '<button class="button button-ghost button-small" data-suspend-account="' + item.account_id +
      '" data-suspend-campaign="' + item.campaign_id + '" data-suspend-connection="' + connectionId +
      '" type="button">Остановить</button>';
    const adsNote = item.ads_created > 1 ? (" · " + item.ads_created + " объявлений в группе") : "";
    const softNote = item.note ? '<div class="notice notice-warning" style="margin-top:6px">' + esc(item.note) + '</div>' : "";
    return '<div class="plan-item"><strong>Аккаунт ' + esc(item.pool_login) + ' · кампания #' + item.campaign_id + '</strong>' +
      '<p>' + esc(item.headline) + ' — ' + esc(item.ad_text) + '</p>' +
      '<small>Счётчик #' + item.counter_id + ' · цель #' + item.goal_id + (item.image_used ? " · с картинкой" : "") + esc(adsNote) + '</small>' +
      softNote +
      '<div style="margin-top:8px">' + activateButton + ' ' + suspendButton + '</div></div>';
  }).join("");
}

function renderBulkJob(job, connectionId) {
  const panel = byId("bulkOutput");
  panel.classList.remove("hidden");
  const statusLabel = job.status === "running" ? "Выполняется…" : (job.status === "done" ? "Завершено" : "Ошибка выполнения");
  const errorNote = job.status === "failed" && job.error ? '<div class="notice notice-warning">' + esc(job.error) + '</div>' : "";
  const warningNote = job.status !== "running" ? '<div class="notice notice-warning" style="margin:12px 0 0">' + esc(job.warning) + '</div>' : "";
  panel.innerHTML = '<h3>Массовый запуск · проект #' + job.project_id + '</h3>' +
    '<div class="muted small">' + statusLabel + ' — создано ' + job.created + ' из ' + job.requested + '</div>' +
    renderCampaignItems(job.items, connectionId) + errorNote + warningNote;
  panel.scrollIntoView({behavior: "smooth", block: "nearest"});
}

async function pollBulkJob(projectId, jobId, onTick) {
  while (true) {
    const job = await api("/api/projects/" + projectId + "/campaigns/bulk-jobs/" + jobId);
    onTick(job);
    if (job.status !== "running") return job;
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
}

async function wizardLaunch() {
  const msg = byId("wizLaunchMessage");
  const button = byId("wizLaunch");
  button.disabled = true;
  showMessage(msg, "Запускаю массовый запуск…");
  try {
    const payload = {
      count: wizard.count,
      connection_id: wizard.connectionId,
      payer_id: wizard.payerId,
      creative_mode: wizard.creativeMode,
      creative_sets: [],
      ads_per_group: wizard.adsPerGroup,
    };
    if (wizard.creativeMode === "manual_per_account") {
      payload.creative_sets = wizard.manualSets.map((set) => ({
        headline: set.headline.trim(),
        ad_text: set.ad_text.trim(),
        image_url: set.image_url.trim() || null,
        sitelinks: [],
        callouts: [],
      }));
      if (payload.creative_sets.some((c) => !c.headline || !c.ad_text)) {
        throw new Error("Заполните заголовок и текст объявления для каждого аккаунта.");
      }
    } else {
      const headlines = byId("wizHeadlines").value.split("\n").map((x) => x.trim()).filter(Boolean);
      const adTexts = byId("wizAdTexts").value.split("\n").map((x) => x.trim()).filter(Boolean);
      const imageUrls = byId("wizImages").value.split("\n").map((x) => x.trim()).filter(Boolean);
      const calloutsPool = byId("wizCallouts").value.split("\n").map((x) => x.trim()).filter(Boolean);
      const sitelinksPool = byId("wizSitelinks").value.split("\n").map((x) => x.trim()).filter(Boolean).map((line) => {
        const [title, href, description] = line.split("|").map((part) => (part || "").trim());
        return {title: title || "", href: href || "", description: description || ""};
      });
      if (!headlines.length || !adTexts.length) throw new Error("Нужен хотя бы один заголовок и один текст объявления.");
      if (sitelinksPool.some((sl) => !sl.title || !sl.href)) throw new Error("В каждой быстрой ссылке нужны заголовок и ссылка (формат «Заголовок|Ссылка|Описание»).");
      const projectPayload = {
        ...wizard.project, headlines, ad_texts: adTexts, image_urls: imageUrls,
        callouts_pool: calloutsPool, sitelinks_pool: sitelinksPool,
      };
      await api("/api/projects/" + wizard.projectId, {method: "PUT", body: JSON.stringify(projectPayload)});
    }
    const start = await api("/api/projects/" + wizard.projectId + "/campaigns/bulk-create", {
      method: "POST", body: JSON.stringify(payload),
    });
    const connectionId = wizard.connectionId;
    const job = await pollBulkJob(wizard.projectId, start.job_id, (tick) => {
      showMessage(msg, "Выполняется: создано " + tick.created + " из " + tick.requested + "…");
      renderBulkJob(tick, connectionId);
    });
    bulkCreatedTotal += job.created;
    byId("metricBulkCreated").textContent = num(bulkCreatedTotal);
    wizardClose();
    await loadProjects();
  } catch (error) {
    showMessage(msg, error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function showCampaignHistory(projectId) {
  const panel = byId("bulkOutput");
  panel.classList.remove("hidden");
  panel.innerHTML = '<div class="muted small">Загружаю историю запусков…</div>';
  try {
    const data = await api("/api/projects/" + projectId + "/campaigns");
    const items = data.items || [];
    if (!items.length) {
      panel.innerHTML = '<h3>История запусков · проект #' + projectId + '</h3><div class="empty">Запусков пока не было.</div>';
      panel.scrollIntoView({behavior: "smooth", block: "nearest"});
      return;
    }
    const rows = items.map((c) => {
      const connectionId = c.connection_id ?? "";
      const activateButton = '<button class="button button-ghost button-small" data-activate-account="' + c.account_id +
        '" data-activate-campaign="' + c.campaign_id + '" data-activate-connection="' + connectionId +
        '" type="button">Активировать</button>';
      const suspendButton = '<button class="button button-ghost button-small" data-suspend-account="' + c.account_id +
        '" data-suspend-campaign="' + c.campaign_id + '" data-suspend-connection="' + connectionId +
        '" type="button">Остановить</button>';
      return '<div class="plan-item"><strong>' + esc(c.pool_login || ("Аккаунт #" + c.account_id)) + ' · кампания #' + c.campaign_id + '</strong>' +
        '<small>' + esc(c.created_at) + (c.payer_id ? " · юрлицо #" + c.payer_id : "") + '</small>' +
        '<div style="margin-top:8px">' + activateButton + ' ' + suspendButton + '</div></div>';
    }).join("");
    panel.innerHTML = '<h3>История запусков · проект #' + projectId + '</h3>' + rows;
    panel.scrollIntoView({behavior: "smooth", block: "nearest"});
  } catch (error) {
    panel.innerHTML = '<div class="notice notice-warning">' + esc(error.message) + '</div>';
  }
}

async function checkPoolStatus() {
  try {
    const data = await api("/api/parsya-pool/accounts");
    if (!data.configured) {
      byId("metricPoolStatus").textContent = "Не настроен";
      byId("poolFoot").textContent = "PARSYA_POOL_API_TOKEN не задан";
      return;
    }
    const items = data.items || [];
    const free = items.filter((a) => a.is_free).length;
    byId("metricPoolStatus").textContent = "Доступен";
    byId("poolFoot").textContent = free + " свободных из " + items.length;
  } catch (error) {
    byId("metricPoolStatus").textContent = "Ошибка";
    byId("poolFoot").textContent = error.message;
  }
}

byId("refreshAll").addEventListener("click", async () => { await loadProjects(); await loadClickruConnections(); await checkPoolStatus(); });
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
loadClickruConnections();
checkPoolStatus();
