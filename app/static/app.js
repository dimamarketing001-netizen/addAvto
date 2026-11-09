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
function pluralizeRu(n, one, few, many) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}
function accountsWord(n) {
  return pluralizeRu(n, "аккаунт", "аккаунта", "аккаунтов");
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
const IMAGE_UPLOAD_MAX_MB = 4;
function readImageFilesAsDataUris(fileList) {
  const files = Array.from(fileList || []);
  const tooBig = files.find((f) => f.size > IMAGE_UPLOAD_MAX_MB * 1024 * 1024);
  if (tooBig) {
    alert('Файл "' + tooBig.name + '" больше ' + IMAGE_UPLOAD_MAX_MB + ' МБ — выберите файл меньшего размера.');
    return Promise.resolve([]);
  }
  return Promise.all(files.map((file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("Не удалось прочитать файл " + file.name));
    reader.readAsDataURL(file);
  })));
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
      const editButton = p.has_launched
        ? '<button class="link-button" disabled title="Проект уже запущен — изменить настройки нельзя">Редактировать</button>'
        : '<button class="link-button" data-edit="' + p.id + '">Редактировать</button>';
      const bulkLabel = p.has_launched ? "Запустить ещё" : "Массовый запуск";
      return '<tr>' +
        '<td><div class="project-title">' + esc(p.name) + '</div><div class="project-domain">' + esc(p.domain) + '</div></td>' +
        '<td><span class="mode-badge">' + esc(modeLabel(p.campaign_mode)) + '</span></td>' +
        '<td>' + num(p.target_drr) + '%</td>' +
        '<td><div class="row-actions">' + editButton + '<button class="link-button" data-plan="' + p.id + '">План</button><button class="link-button" data-bulk="' + p.id + '">' + bulkLabel + '</button><button class="link-button" data-duplicate="' + p.id + '" title="Создать копию с теми же настройками, которую можно запустить как есть или сначала отредактировать">Дублировать</button><button class="link-button" data-history="' + p.id + '">История</button><button class="link-button danger" data-delete="' + p.id + '">Удалить</button></div></td>' +
        '</tr>';
    }).join("");
  } catch (error) {
    body.innerHTML = '<tr><td colspan="4" class="empty">Ошибка загрузки: ' + esc(error.message) + '</td></tr>';
  }
}
byId("newProjectButton").addEventListener("click", () => openWizard(null));

let bulkCreatedTotal = 0;

byId("projectsRows").addEventListener("click", async (event) => {
  const editButton = event.target.closest("[data-edit]");
  const planButton = event.target.closest("[data-plan]");
  const bulkButton = event.target.closest("[data-bulk]");
  const duplicateButton = event.target.closest("[data-duplicate]");
  const historyButton = event.target.closest("[data-history]");
  const deleteButton = event.target.closest("[data-delete]");
  if (editButton) {
    openWizard(Number(editButton.dataset.edit));
    return;
  }
  if (bulkButton) {
    openWizard(Number(bulkButton.dataset.bulk));
    return;
  }
  if (duplicateButton) {
    duplicateButton.disabled = true;
    try {
      const copy = await api("/api/projects/" + duplicateButton.dataset.duplicate + "/duplicate", {method: "POST", body: "{}"});
      await loadProjects();
      openWizard(copy.id);
    } catch (error) {
      alert(error.message);
    } finally {
      duplicateButton.disabled = false;
    }
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
  projectId: null, project: null, step: 1, locked: false,
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
  wizard.locked = !!(project && project.has_launched);
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
  if (wizard.locked) {
    const regionsLocked = await loadGeoRegions();
    const byIdLocked = new Map(regionsLocked.map((r) => [r.id, r]));
    const regionLabels = (p.region_ids || []).map((id) => {
      const r = byIdLocked.get(id);
      return r ? geoRegionLabel(r) : ("ID " + id);
    });
    body.innerHTML = wizardStepsNav(1) +
      '<div class="notice notice-info">Проект уже запущен — название, домен, ключевые фразы и регион показа больше нельзя менять, ' +
      'чтобы они не расходились с уже созданными кампаниями. Дальше можно запустить ещё аккаунты с теми же настройками.</div>' +
      '<div class="form-grid">' +
        '<div class="field field-wide field-readonly"><span>Название проекта</span><strong>' + esc(p.name || "") + '</strong></div>' +
        '<div class="field field-wide field-readonly"><span>Домен / посадочная страница</span><strong>' + esc(p.domain || "") + '</strong></div>' +
        (p.launch_mode === "ozon" ? '<div class="field field-wide field-readonly"><span>Источник креативов</span><strong>Директ Озон — ' + esc(p.ozon_url || "") + '</strong></div>' : "") +
        '<div class="field field-readonly"><span>Структура кампаний</span><strong>' + (p.campaign_mode === "single_epk" ? "Одна ЕПК" : "Отдельные РК") + '</strong></div>' +
        '<div class="field field-readonly"><span>Целевой ДРР</span><strong>' + esc(p.target_drr ?? "") + '%</strong></div>' +
        '<div class="field field-wide field-readonly"><span>Регион показа</span><strong>' + esc(regionLabels.join(", ") || "—") + '</strong></div>' +
      '</div>' +
      '<div class="wizard-actions"><span></span><button class="button button-primary" id="wizNext1" type="button">Далее →</button></div>';
    body.querySelector("#wizNext1").addEventListener("click", () => { wizard.step = 2; renderWizardStep2(); });
    return;
  }
  if (!wizard.regionSelections) {
    const regions = await loadGeoRegions();
    const byId_ = new Map(regions.map((r) => [r.id, r]));
    wizard.regionSelections = (p.region_ids || []).map((id) => {
      const r = byId_.get(id);
      return {id, label: r ? geoRegionLabel(r) : ("ID " + id)};
    });
  }
  const launchMode = p.launch_mode || "direct";
  body.innerHTML = wizardStepsNav(1) +
    '<div class="form-grid">' +
      '<label class="field field-wide">Название проекта<input id="wizPName" value="' + esc(p.name || "") + '" maxlength="120" placeholder="Например, БФЛ — Казань (можно оставить пустым для режима Озон — подставится название товара)"></label>' +
      '<fieldset class="field field-wide mode-picker"><legend>Источник креативов</legend>' +
        '<label class="mode-option"><input type="radio" name="wizLaunchMode" value="direct"' + (launchMode === "direct" ? " checked" : "") + '><span><strong>Обычный Директ</strong><small>Заголовки, тексты, картинки и ключевые фразы вводите сами (шаг 5).</small></span></label>' +
        '<label class="mode-option"><input type="radio" name="wizLaunchMode" value="ozon"' + (launchMode === "ozon" ? " checked" : "") + '><span><strong>Директ Озон</strong><small>Даёте ссылку на карточку товара Ozon — скрипт сам парсит её и просит Gemini написать заголовки, тексты, ключевые фразы и подобрать картинки.</small></span></label>' +
      '</fieldset>' +
      '<div id="wizDomainBlock"' + (launchMode === "ozon" ? " hidden" : "") + '><label class="field field-wide">Домен / посадочная страница<input id="wizPDomain" value="' + esc(launchMode === "ozon" ? "" : (p.domain || "")) + '" placeholder="https://example.ru/"></label></div>' +
      '<div id="wizOzonBlock"' + (launchMode === "ozon" ? "" : " hidden") + '>' +
        '<label class="field field-wide">Ссылка на карточку товара Ozon<input id="wizPOzonUrl" value="' + esc(p.ozon_url || "") + '" placeholder="https://www.ozon.ru/product/..."></label>' +
        '<div class="notice notice-info">Домен/посадочная, заголовки, тексты объявлений, ключевые и минус-фразы, картинки — заполнятся автоматически после нажатия «Далее». Их можно будет поправить вручную на шаге 5 или перегенерировать позже.</div>' +
      '</div>' +
      '<fieldset class="field field-wide mode-picker"><legend>Структура рекламных кампаний</legend>' +
        '<label class="mode-option"><input type="radio" name="wizPMode" value="separate"' + ((p.campaign_mode || "separate") === "separate" ? " checked" : "") + '><span><strong>Отдельные РК</strong><small>Одна кампания Поиск + одна кампания РСЯ.</small></span></label>' +
        '<label class="mode-option"><input type="radio" name="wizPMode" value="single_epk"' + (p.campaign_mode === "single_epk" ? " checked" : "") + '><span><strong>Одна ЕПК</strong><small>Единая перфоманс-кампания, Поиск+РСЯ вместе.</small></span></label>' +
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

  // Переключение режима не должно перерисовывать весь шаг (потерялись бы
  // уже введённые название/ДРР/регионы) — просто прячем/показываем два
  // готовых блока (домен vs ссылка Ozon).
  body.querySelectorAll('input[name="wizLaunchMode"]').forEach((input) => {
    input.addEventListener("change", (event) => {
      const isOzon = event.target.value === "ozon";
      byId("wizDomainBlock").hidden = isOzon;
      byId("wizOzonBlock").hidden = !isOzon;
    });
  });

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
  const launchMode = document.querySelector('input[name="wizLaunchMode"]:checked').value;
  const name = byId("wizPName").value.trim();
  const campaignMode = document.querySelector('input[name="wizPMode"]:checked').value;
  const targetDrr = Number(byId("wizPDrr").value);
  const mobileRaw = byId("wizPMobile").value.trim();
  const regionIds = (wizard.regionSelections || []).map((r) => r.id);
  if (!regionIds.length) { showMessage(msg, "Выберите хотя бы один город или регион.", "error"); return; }

  let payload;
  if (launchMode === "ozon") {
    const ozonUrl = byId("wizPOzonUrl").value.trim();
    if (!ozonUrl) { showMessage(msg, "Укажите ссылку на карточку товара Ozon.", "error"); return; }
    if (!/ozon\./i.test(ozonUrl)) { showMessage(msg, "Это не похоже на ссылку Ozon.", "error"); return; }
    payload = {
      ...(wizard.project || {}),
      name: name || "Товар Ozon", domain: ozonUrl, campaign_mode: campaignMode, target_drr: targetDrr,
      launch_mode: "ozon", ozon_url: ozonUrl,
      keywords: "", negative_keywords: "", region_ids: regionIds,
      mobile_bid_adjustment_percent: mobileRaw ? Number(mobileRaw) : null,
    };
  } else {
    const domain = byId("wizPDomain").value.trim();
    const keywords = byId("wizPKeywords").value.trim();
    const negativeKeywords = byId("wizPNegative").value.trim();
    if (!name || !domain) { showMessage(msg, "Укажите название и домен.", "error"); return; }
    if (!keywords) { showMessage(msg, "Нужна хотя бы одна ключевая фраза.", "error"); return; }
    payload = {
      ...(wizard.project || {}),
      name, domain, campaign_mode: campaignMode, target_drr: targetDrr,
      launch_mode: "direct", ozon_url: "",
      keywords, negative_keywords: negativeKeywords, region_ids: regionIds,
      mobile_bid_adjustment_percent: mobileRaw ? Number(mobileRaw) : null,
    };
  }

  button.disabled = true;
  showMessage(msg, wizard.projectId ? "Сохраняю проект…" : "Создаю проект…");
  try {
    let saved = wizard.projectId
      ? await api("/api/projects/" + wizard.projectId, {method: "PUT", body: JSON.stringify(payload)})
      : await api("/api/projects", {method: "POST", body: JSON.stringify(payload)});
    wizard.project = saved;
    wizard.projectId = saved.id;

    if (launchMode === "ozon") {
      showMessage(msg, "Озон парсит карточку, Gemini пишет тексты… это может занять 30–90 секунд.");
      try {
        const genResult = await api("/api/projects/" + saved.id + "/ozon-generate", {
          method: "POST", body: JSON.stringify({url: payload.ozon_url}),
        });
        saved = genResult.project;
        wizard.project = saved;
        showMessage(msg, "Готово: " + saved.headlines.length + " заголовков, " + saved.ad_texts.length +
          " текстов, " + saved.image_urls.length + " картинок, " +
          saved.keywords.split("\n").filter(Boolean).length + " ключевых фраз.", "success");
      } catch (genError) {
        showMessage(msg, "Проект создан, но генерация не удалась: " + genError.message +
          " Можно нажать «Далее» ещё раз (попробовать снова) или заполнить шаг 5 вручную.", "error");
        loadProjects();
        return;
      }
    }

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
  )).join("") : '<div class="notice notice-warning">Юрлица для маркировки не найдены — добавьте первое ниже или настройте в кабинете Click.ru. Можно продолжить без выбора, но активировать показы без маркировки не получится.</div>';
  body.innerHTML = wizardStepsNav(3) +
    '<div class="notice notice-info">Юрлицо используется для маркировки рекламы (ОРД/ERID) — обязательно перед реальным запуском показов.</div>' +
    '<div class="wizard-list">' + list + '</div>' +
    '<button class="button button-ghost button-small wizard-add" id="wizAddPayerToggle" type="button">+ Добавить юрлицо</button>' +
    '<form id="wizAddPayerForm" class="hidden">' +
      '<fieldset class="field field-wide mode-picker payer-type-picker">' +
        '<label class="mode-option"><input type="radio" name="payerType" value="BUSINESS" checked><span><strong>ООО</strong></span></label>' +
        '<label class="mode-option"><input type="radio" name="payerType" value="INDIVIDUAL"><span><strong>ИП</strong></span></label>' +
        '<label class="mode-option"><input type="radio" name="payerType" value="PERSON"><span><strong>Физлицо</strong></span></label>' +
      '</fieldset>' +
      '<div id="wizPayerFields" class="form-grid"></div>' +
      '<div class="wizard-actions"><span></span><button class="button button-primary" type="submit">Добавить юрлицо</button></div>' +
    '</form>' +
    '<div id="wizAddPayerMessage" class="form-message"></div>' +
    '<div class="wizard-actions"><button class="button button-ghost" id="wizBack3" type="button">← Назад</button><button class="button button-primary" id="wizNext3" type="button">Далее →</button></div>';
  body.querySelector("#wizBack3").addEventListener("click", () => { wizard.step = 2; renderWizardStep2(); });
  body.querySelector("#wizNext3").addEventListener("click", () => {
    if (wizard.payers.length && !wizard.payerId) { alert("Выберите юрлицо для маркировки — без него нельзя будет запустить показы."); return; }
    wizard.step = 4;
    renderWizardStep4();
  });

  const payerForm = body.querySelector("#wizAddPayerForm");
  const payerFieldsBox = body.querySelector("#wizPayerFields");
  function refreshPayerFields() {
    const type = body.querySelector('input[name="payerType"]:checked').value;
    payerFieldsBox.innerHTML = renderPayerTypeFields(type);
  }
  refreshPayerFields();
  body.querySelector("#wizAddPayerToggle").addEventListener("click", () => {
    payerForm.classList.toggle("hidden");
  });
  body.querySelectorAll('input[name="payerType"]').forEach((input) => {
    input.addEventListener("change", refreshPayerFields);
  });
  payerForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const msg = body.querySelector("#wizAddPayerMessage");
    const button = event.submitter;
    try {
      button.disabled = true;
      showMessage(msg, "Создаю юрлицо в Click.ru…");
      const payload = buildPayerPayload(new FormData(payerForm));
      const created = await api("/api/payers?connection_id=" + wizard.connectionId, {
        method: "POST", body: JSON.stringify(payload),
      });
      wizard.payerId = created.payer_id;
      showMessage(msg, "Юрлицо добавлено.", "success");
      await renderWizardStep3();
    } catch (error) {
      showMessage(msg, error.message, "error");
    } finally {
      button.disabled = false;
    }
  });
}

function renderPayerTypeFields(type) {
  const common =
    '<label class="field">ИНН<input name="inn" required minlength="10" maxlength="12"></label>' +
    '<label class="field">Телефон (необязательно)<input name="phone"></label>';
  if (type === "PERSON") {
    return common +
      '<label class="field">Фамилия<input name="last_name" required></label>' +
      '<label class="field">Имя<input name="first_name" required></label>' +
      '<label class="field">Отчество<input name="middle_name" required></label>';
  }
  const typeSpecific = type === "BUSINESS"
    ? '<label class="field field-wide">Полное название (например, Общество с ограниченной ответственностью «Ромашка»)<input name="full_name" required></label>' +
      '<label class="field">КПП<input name="kpp" required minlength="9" maxlength="9"></label>' +
      '<label class="field">ОГРН<input name="ogrn" required inputmode="numeric" pattern="[0-9]+"></label>'
    : '<label class="field">ОГРНИП<input name="ogrnip" required inputmode="numeric" pattern="[0-9]+"></label>';
  return common +
    '<label class="field field-wide">' + (type === "BUSINESS" ? "Краткое название (например, ООО «Ромашка»)" : "Полное название ИП") + '<input name="name" required></label>' +
    typeSpecific +
    '<label class="field">Форма налогообложения<select name="tax_form" required><option value="OSN">ОСН</option><option value="USN">УСН</option></select></label>' +
    '<label class="field field-wide">Юридический адрес<input name="business_address_address" required placeholder="Россия, Москва, улица Новая, дом 1, офис 1"></label>' +
    '<label class="field">Индекс (юр. адрес)<input name="business_address_postal_code" required maxlength="20"></label>' +
    '<label class="field field-wide">Фактический адрес<input name="actual_address_address" required></label>' +
    '<label class="field">Индекс (факт. адрес)<input name="actual_address_postal_code" required maxlength="20"></label>' +
    '<label class="field field-wide">Банк<input name="bank_name" required placeholder="Например, Альфа-банк"></label>' +
    '<label class="field">БИК банка<input name="bank_rcbic" required minlength="9" maxlength="9"></label>' +
    '<label class="field">Расчётный счёт<input name="bank_account" required></label>' +
    '<label class="field">Корреспондентский счёт<input name="bank_correspondent_account" required></label>';
}

function buildPayerPayload(f) {
  const type = f.get("payerType");
  const payload = {
    type,
    inn: String(f.get("inn") || "").trim(),
    phone: String(f.get("phone") || "").trim() || null,
  };
  if (type === "PERSON") {
    payload.last_name = String(f.get("last_name") || "").trim();
    payload.first_name = String(f.get("first_name") || "").trim();
    payload.middle_name = String(f.get("middle_name") || "").trim();
    return payload;
  }
  payload.name = String(f.get("name") || "").trim();
  payload.tax_form = f.get("tax_form");
  payload.business_address = {
    address: String(f.get("business_address_address") || "").trim(),
    postal_code: String(f.get("business_address_postal_code") || "").trim(),
  };
  payload.actual_address = {
    address: String(f.get("actual_address_address") || "").trim(),
    postal_code: String(f.get("actual_address_postal_code") || "").trim(),
  };
  payload.bank = {
    name: String(f.get("bank_name") || "").trim(),
    rcbic: String(f.get("bank_rcbic") || "").trim(),
    account: String(f.get("bank_account") || "").trim(),
    correspondent_account: String(f.get("bank_correspondent_account") || "").trim(),
  };
  if (type === "BUSINESS") {
    payload.full_name = String(f.get("full_name") || "").trim();
    payload.kpp = String(f.get("kpp") || "").trim();
    payload.ogrn = Number(f.get("ogrn"));
  } else {
    payload.ogrnip = Number(f.get("ogrnip"));
  }
  return payload;
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
  return '<label class="mode-option"><input type="radio" name="wizMode" value="' + value + '"' + (wizard.creativeMode === value ? " checked" : "") + '><span><strong>' + esc(title) + '</strong><small>' + esc(note) + '</small></span></label>';
}

function renderWizardStep5() {
  const body = byId("wizardBody");
  const accountsN = wizard.count;
  const structureBlock = '<fieldset class="field field-wide mode-picker"><legend>Сколько объявлений создавать в группе каждого аккаунта</legend>' +
    '<label class="mode-option"><input type="radio" name="wizStructure" value="single"' + (wizard.adsPerGroup <= 1 ? " checked" : "") + '><span><strong>Одно объявление</strong><small>Простой и предсказуемый вариант: у каждого аккаунта один фиксированный заголовок, текст и картинка.</small></span></label>' +
    '<label class="mode-option"><input type="radio" name="wizStructure" value="multi"' + (wizard.adsPerGroup > 1 ? " checked" : "") + '><span><strong>Несколько объявлений (A/B)</strong><small>До 50 вариантов в одной группе — Директ сам покажет чаще тот, что лучше работает. Доступно только вместе с режимом «Скрипт перемешает» ниже.</small></span></label>' +
    '</fieldset>' +
    (wizard.adsPerGroup > 1
      ? '<label class="field">Сколько вариантов объявления в группе (от 2 до 50)<input id="wizAdsPerGroup" type="number" min="2" max="50" value="' + wizard.adsPerGroup + '"></label>'
      : "");
  const modeOptions = '<fieldset class="field field-wide mode-picker"><legend>Откуда брать заголовки, тексты и картинки для ' + accountsN + ' ' + accountsWord(accountsN) + '</legend>' +
    wizardModeOption("shuffle", "Скрипт перемешает сам",
      "Вы один раз загружаете список заголовков, текстов и картинок ниже — для каждого аккаунта (и каждого объявления, если их несколько) скрипт случайно собирает из них свою связку.") +
    wizardModeOption("duplicate_single", "Один набор на все аккаунты",
      "Вы вводите ОДИН заголовок, текст и картинку — они без изменений попадут во все " + accountsN + " " + accountsWord(accountsN) + ".") +
    wizardModeOption("manual_per_account", "Свой набор на каждый аккаунт",
      "Вы вручную заполняете " + accountsN + " " + (accountsN === 1 ? "форму" : "форм") + " — по одной на каждый аккаунт, с собственным заголовком, текстом и картинкой.") +
    '</fieldset>';
  const poolForm =
    '<label class="field field-wide">Заголовки — по одному на строку (до 56 символов)<textarea id="wizHeadlines" rows="3">' + esc(wizard.pool.headlines) + '</textarea></label>' +
    '<label class="field field-wide">Тексты объявлений — по одному на строку (до 81 символа)<textarea id="wizAdTexts" rows="3">' + esc(wizard.pool.ad_texts) + '</textarea></label>' +
    '<label class="field field-wide">Изображения — вставьте ссылки (по одной на строку) или выберите файлы ниже (необязательно)<textarea id="wizImages" rows="2">' + esc(wizard.pool.image_urls) + '</textarea>' +
      '<input type="file" id="wizImagesFile" accept="image/jpeg,image/png,image/gif" multiple>' +
      '<small>JPG/PNG/GIF, до ' + IMAGE_UPLOAD_MAX_MB + ' МБ на файл — выбранные файлы сами добавятся строками в поле выше.</small>' +
    '</label>' +
    '<label class="field field-wide">Уточнения (callouts) — по одному на строку, до 25 символов (необязательно, до 5 на объявление)<textarea id="wizCallouts" rows="2">' + esc(wizard.pool.callouts) + '</textarea></label>' +
    '<label class="field field-wide">Быстрые ссылки (sitelinks) — по одной на строку: «Заголовок|Ссылка|Описание» (описание необязательно, до 4 на объявление)<textarea id="wizSitelinks" rows="3" placeholder="Доставка|https://example.ru/delivery|Условия доставки">' + esc(wizard.pool.sitelinks) + '</textarea></label>';
  const manualForm = wizard.manualSets.map((set, i) => (
    '<div class="wizard-creative-set"><strong>Аккаунт ' + (i + 1) + '</strong><div class="wizard-creative-grid">' +
      '<label class="field">Заголовок<input data-manual-headline="' + i + '" maxlength="56" value="' + esc(set.headline) + '"></label>' +
      '<label class="field field-wide">Текст объявления<input data-manual-adtext="' + i + '" maxlength="81" value="' + esc(set.ad_text) + '"></label>' +
      '<label class="field field-wide">Ссылка на изображение или файл (необязательно)<input data-manual-image="' + i + '" value="' + esc((set.image_url || "").startsWith("data:") ? "" : (set.image_url || "")) + '" placeholder="' + (((set.image_url || "").startsWith("data:")) ? "Файл загружен ✓ (выберите другой, чтобы заменить)" : "https://example.ru/banner.jpg") + '">' +
      '<input type="file" data-manual-image-file="' + i + '" accept="image/jpeg,image/png,image/gif" style="margin-top:6px"></label>' +
    '</div></div>'
  )).join("");
  const isOzonProject = (wizard.project || {}).launch_mode === "ozon";
  const ozonRegenBlock = isOzonProject ?
    '<div class="notice notice-info">Эти заголовки/тексты/ключевые фразы/картинки сгенерировал Gemini по карточке ' +
      '<a href="' + esc((wizard.project || {}).ozon_url || "") + '" target="_blank" rel="noopener">' + esc((wizard.project || {}).ozon_url || "") + '</a>. ' +
      'Можно поправить вручную ниже или перегенерировать заново (старые значения заменятся).' +
      '<div style="margin-top:10px"><button class="button button-ghost button-small" id="wizOzonRegen" type="button">🔄 Пересоздать через Gemini</button></div>' +
      '<div id="wizOzonRegenMessage" class="form-message"></div>' +
    '</div>' : "";
  body.innerHTML = wizardStepsNav(5) + ozonRegenBlock + structureBlock + modeOptions +
    '<div id="wizCreativeArea">' + (wizard.creativeMode === "manual_per_account" ? manualForm : poolForm) + '</div>' +
    '<div class="wizard-actions"><button class="button button-ghost" id="wizBack5" type="button">← Назад</button><button class="button button-primary" id="wizLaunch" type="button">Запустить массовый запуск</button></div>' +
    '<div id="wizLaunchMessage" class="form-message"></div>';
  if (isOzonProject) {
    byId("wizOzonRegen").addEventListener("click", async () => {
      const regenMsg = byId("wizOzonRegenMessage");
      const regenButton = byId("wizOzonRegen");
      regenButton.disabled = true;
      showMessage(regenMsg, "Озон парсит карточку, Gemini пишет тексты… это может занять 30–90 секунд.");
      try {
        const genResult = await api("/api/projects/" + wizard.projectId + "/ozon-generate", {
          method: "POST", body: JSON.stringify({url: wizard.project.ozon_url}),
        });
        wizard.project = genResult.project;
        seedCreativePools(genResult.project);
        renderWizardStep5();
      } catch (error) {
        showMessage(regenMsg, error.message, "error");
        regenButton.disabled = false;
      }
    });
  }
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
  // Живая привязка к wizard.pool на каждый ввод (не только при смене
  // режима) — иначе набранные заголовки/тексты терялись при переходе
  // "← Назад" на шаг 4 и обратно, см. жалобу пользователя 2026-11-02.
  const poolFieldMap = {wizHeadlines: "headlines", wizAdTexts: "ad_texts", wizImages: "image_urls", wizCallouts: "callouts", wizSitelinks: "sitelinks"};
  Object.keys(poolFieldMap).forEach((fieldId) => {
    const el = byId(fieldId);
    if (el) el.addEventListener("input", () => { wizard.pool[poolFieldMap[fieldId]] = el.value; });
  });
  body.querySelectorAll('input[name="wizMode"]').forEach((input) => {
    input.addEventListener("change", (event) => {
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
  if (byId("wizImagesFile")) {
    byId("wizImagesFile").addEventListener("change", async (event) => {
      const uris = await readImageFilesAsDataUris(event.target.files);
      event.target.value = "";
      if (!uris.length) return;
      const textarea = byId("wizImages");
      const existing = textarea.value.trim();
      textarea.value = (existing ? existing + "\n" : "") + uris.join("\n");
      wizard.pool.image_urls = textarea.value;
    });
  }
  body.querySelectorAll("[data-manual-image-file]").forEach((fileInput) => {
    fileInput.addEventListener("change", async (event) => {
      const i = Number(event.target.dataset.manualImageFile);
      const [uri] = await readImageFilesAsDataUris(event.target.files);
      event.target.value = "";
      if (!uri) return;
      wizard.manualSets[i].image_url = uri;
      const textInput = body.querySelector('[data-manual-image="' + i + '"]');
      textInput.value = "";
      textInput.placeholder = "Файл загружен ✓ (выберите другой, чтобы заменить)";
    });
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
