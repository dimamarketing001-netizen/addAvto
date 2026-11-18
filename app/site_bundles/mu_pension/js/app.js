/* Квиз «Проверка пенсии» — ООО «Мой юрист» */
(function () {
  'use strict';

  var YM_ID = 0;                      // ID счётчика Яндекс.Метрики (поставим позже)
  var ENDPOINT = 'api/submit.php';
  var CHECKLIST = 'checklist.html';
  var PHONE_SHOW = '8 (800) 505-67-97';
  var PHONE_HREF = 'tel:88005056797';
  var STORE = 'pension_quiz_v1';
  var PENDING = 'pension_quiz_pending';
  var TTL = 3 * 24 * 3600 * 1000;

  var REGIONS = ['Москва', 'Санкт-Петербург', 'Московская область', 'Ленинградская область', 'Свердловская область', 'Челябинская область', 'Республика Адыгея', 'Республика Алтай', 'Республика Башкортостан', 'Республика Бурятия', 'Республика Дагестан', 'Республика Ингушетия', 'Кабардино-Балкарская Республика', 'Республика Калмыкия', 'Карачаево-Черкесская Республика', 'Республика Карелия', 'Республика Коми', 'Республика Крым', 'Республика Марий Эл', 'Республика Мордовия', 'Республика Саха (Якутия)', 'Республика Северная Осетия — Алания', 'Республика Татарстан', 'Республика Тыва', 'Удмуртская Республика', 'Республика Хакасия', 'Чеченская Республика', 'Чувашская Республика', 'Алтайский край', 'Забайкальский край', 'Камчатский край', 'Краснодарский край', 'Красноярский край', 'Пермский край', 'Приморский край', 'Ставропольский край', 'Хабаровский край', 'Амурская область', 'Архангельская область', 'Астраханская область', 'Белгородская область', 'Брянская область', 'Владимирская область', 'Волгоградская область', 'Вологодская область', 'Воронежская область', 'Ивановская область', 'Иркутская область', 'Калининградская область', 'Калужская область', 'Кемеровская область — Кузбасс', 'Кировская область', 'Костромская область', 'Курганская область', 'Курская область', 'Липецкая область', 'Магаданская область', 'Мурманская область', 'Нижегородская область', 'Новгородская область', 'Новосибирская область', 'Омская область', 'Оренбургская область', 'Орловская область', 'Пензенская область', 'Псковская область', 'Ростовская область', 'Рязанская область', 'Самарская область', 'Саратовская область', 'Сахалинская область', 'Смоленская область', 'Тамбовская область', 'Тверская область', 'Томская область', 'Тульская область', 'Тюменская область', 'Ульяновская область', 'Ярославская область', 'Севастополь', 'Еврейская автономная область', 'Ненецкий автономный округ', 'Ханты-Мансийский автономный округ — Югра', 'Чукотский автономный округ', 'Ямало-Ненецкий автономный округ', 'Донецкая Народная Республика', 'Луганская Народная Республика', 'Запорожская область', 'Херсонская область'];

  /* ---------- описание шагов ---------- */
  var DEFS = {
    who: {
      type: 'single', label: 'Проверка для',
      title: function () { return 'Для кого проверяем пенсию?'; },
      hint: function () { return 'Выберите вариант — формулировки подстроятся.'; },
      opts: [
        { v: 'self', t: 'Для себя', i: '🙋' },
        { v: 'parent', t: 'Для мамы или папы', i: '👨‍👩‍👦' },
        { v: 'relative', t: 'Для другого родственника', i: '🤝' }
      ]
    },
    status: {
      type: 'single', label: 'Статус пенсии',
      title: function () { return isSelf() ? 'Вы уже получаете пенсию?' : 'Пенсия уже оформлена?'; },
      hint: function () { return 'От этого зависит, что именно мы проверим.'; },
      opts: [
        { v: 'age', t: 'Да, по возрасту' },
        { v: 'disab', t: 'Да, по инвалидности' },
        { v: 'surv', t: 'Да, по потере кормильца' },
        { v: 'soon', t: 'Пока нет — до пенсии меньше 5 лет' },
        { v: 'far', t: 'Пока нет — до пенсии больше 5 лет' }
      ]
    },
    age: {
      type: 'single', label: 'Возраст',
      title: function () { return isSelf() ? 'Сколько вам лет?' : 'Сколько лет человеку?'; },
      hint: function () { return ''; },
      opts: [
        { v: 'u55', t: 'Младше 55' }, { v: '55', t: '55–59' }, { v: '60', t: '60–64' },
        { v: '65', t: '65–69' }, { v: '70', t: '70–79' }, { v: '80', t: '80 и старше' }
      ]
    },
    stazh: {
      type: 'single', label: 'Трудовой стаж',
      title: function () { return 'Какой примерно трудовой стаж?'; },
      hint: function () { return 'Приблизительно — точные цифры не нужны.'; },
      opts: [
        { v: 'lt15', t: 'Меньше 15 лет' }, { v: '15-25', t: '15–25 лет' }, { v: '25-35', t: '25–35 лет' },
        { v: '35p', t: 'Больше 35 лет' }, { v: 'unk', t: 'Не знаю' }
      ]
    },
    pension: {
      type: 'single', label: 'Размер пенсии',
      title: function () {
        if (A.status === 'soon' || A.status === 'far') return isSelf() ? 'Какую пенсию вы рассчитываете получать?' : 'Какую пенсию рассчитывают получать?';
        return 'Какого размера пенсия сейчас?';
      },
      hint: function () { return 'Примерно — по сумме, которая приходит на карту.'; },
      opts: [
        { v: 'lt15', t: 'До 15 000 ₽' }, { v: '15-20', t: '15 000 – 20 000 ₽' }, { v: '20-30', t: '20 000 – 30 000 ₽' },
        { v: '30-40', t: '30 000 – 40 000 ₽' }, { v: '40p', t: 'Больше 40 000 ₽' }, { v: 'unk', t: 'Не знаю / ещё не оформлена' }
      ]
    },
    periods: {
      type: 'multi', label: 'Периоды в жизни',
      title: function () { return isSelf() ? 'Были ли в вашей жизни такие периоды?' : 'Были ли в жизни такие периоды?'; },
      hint: function () { return 'Отметьте всё, что подходит. Многие из них дают баллы или льготы.'; },
      opts: [
        { v: 'army', t: 'Служба в армии', i: '🎖️' },
        { v: 'kids', t: 'Отпуск по уходу за ребёнком (декрет)', i: '👶' },
        { v: 'care', t: 'Уход за инвалидом или человеком 80+', i: '🧓' },
        { v: 'north', t: 'Работа на Севере', s: 'Крайний Север и приравненные районы', i: '❄️' },
        { v: 'hazard', t: 'Тяжёлые или вредные условия труда', s: 'шахта, горячий цех, химия, медицина, школа', i: '🏭' },
        { v: 'village', t: 'Работа в сельском хозяйстве', s: 'колхоз, совхоз, ферма', i: '🌾' },
        { v: 'other', t: 'Была безработица или работа за границей', i: '🌍' },
        { v: 'none', t: 'Ничего из перечисленного', exclusive: true }
      ]
    },
    kids: {
      type: 'single', label: 'Детей в отпуске по уходу',
      title: function () { return 'Сколько детей воспитывали в отпуске по уходу?'; },
      hint: function () { return 'За детей начисляются баллы — размер зависит от очерёдности.'; },
      opts: [{ v: '1', t: 'Одного' }, { v: '2', t: 'Двоих' }, { v: '3', t: 'Троих' }, { v: '4', t: 'Четверых и более' }]
    },
    work: {
      type: 'single', label: 'Работа сейчас',
      title: function () { return isSelf() ? 'Вы сейчас работаете?' : 'Человек сейчас работает?'; },
      hint: function () { return 'Работающим пенсионерам пенсия пересчитывается раз в год.'; },
      opts: [
        { v: 'work', t: 'Да, официально' }, { v: 'informal', t: 'Подрабатываю неофициально' },
        { v: 'no', t: 'Нет, не работаю' }
      ]
    },
    docs: {
      type: 'single', label: 'Документы',
      title: function () { return 'Какие документы сохранились?'; },
      hint: function () { return 'Не страшно, если почти ничего нет — сведения можно запросить в архивах.'; },
      opts: [
        { v: 'all', t: 'Трудовая книжка и выписка ИЛС' },
        { v: 'book', t: 'Только трудовая книжка' },
        { v: 'part', t: 'Часть документов утеряна' },
        { v: 'none', t: 'Почти ничего не сохранилось' }
      ]
    },
    region: { type: 'region', label: 'Регион' },
    contact: { type: 'contact', label: 'Контакт' }
  };

  var A = {};            // ответы
  var cur = 0;           // текущий шаг
  var locked = false;
  var finished = false;
  var body, bar, stepEl, leftEl, backBtn, quizEl;

  function isSelf() { return !A.who || A.who === 'self'; }
  function has(arr, v) { return Array.isArray(arr) && arr.indexOf(v) !== -1; }
  function $(s, r) { return (r || document).querySelector(s); }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }

  function steps() {
    var l = ['who', 'status', 'age', 'stazh', 'pension', 'periods'];
    if (has(A.periods, 'kids')) l.push('kids');
    l.push('work', 'docs', 'region', 'contact');
    return l;
  }

  function optLabel(id, v) {
    var d = DEFS[id]; if (!d || !d.opts) return v;
    for (var i = 0; i < d.opts.length; i++) if (d.opts[i].v === v) return d.opts[i].t;
    return v;
  }

  /* ---------- аналитика ---------- */
  function track(goal, params) {
    if (YM_ID && window.ym) { try { window.ym(YM_ID, 'reachGoal', goal, params); } catch (e) {} }
  }

  /* ---------- состояние ---------- */
  function save() {
    try { localStorage.setItem(STORE, JSON.stringify({ t: Date.now(), A: A, cur: cur, done: finished })); } catch (e) {}
  }
  function restore() {
    try {
      var s = JSON.parse(localStorage.getItem(STORE) || 'null');
      if (!s || Date.now() - s.t > TTL || s.done) return;
      A = s.A || {};
      var l = steps();
      cur = Math.min(s.cur || 0, l.length - 1);
      if (l[cur] === 'contact') cur = l.length - 1;
    } catch (e) {}
  }

  /* ---------- UTM ---------- */
  function utm() {
    var keys = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term', 'yclid', 'gclid'];
    var out = {}, q = new URLSearchParams(location.search), got = false;
    keys.forEach(function (k) { var v = q.get(k); if (v) { out[k] = v; got = true; } });
    try {
      if (got) sessionStorage.setItem('pension_utm', JSON.stringify(out));
      else out = JSON.parse(sessionStorage.getItem('pension_utm') || '{}');
    } catch (e) {}
    return out;
  }

  /* ---------- отрисовка ---------- */
  function render() {
    var list = steps();
    var id = list[cur];
    var d = DEFS[id];
    var total = list.length;
    var pct = id === 'contact' ? 94 : Math.round(8 + (cur / (total - 1)) * 82);
    bar.style.width = pct + '%';
    stepEl.textContent = id === 'contact' ? 'Последний шаг' : 'Шаг ' + (cur + 1) + ' из ' + total;
    var sec = (total - cur - 1) * 9;
    leftEl.textContent = id === 'contact' ? 'почти готово!' : (sec >= 60 ? '≈ ' + Math.round(sec / 60) + ' мин' : '≈ ' + Math.max(sec, 10) + ' сек');
    backBtn.disabled = cur === 0;
    locked = false;

    var h = '';
    if (d.type === 'single' || d.type === 'multi') {
      h += '<h2 class="q__title">' + esc(d.title()) + '</h2>';
      if (d.hint()) h += '<p class="q__hint">' + esc(d.hint()) + '</p>';
      h += '<div class="opts" role="' + (d.type === 'multi' ? 'group' : 'radiogroup') + '">';
      d.opts.forEach(function (o) {
        var on = d.type === 'multi' ? has(A[id], o.v) : A[id] === o.v;
        h += '<button type="button" class="opt' + (d.type === 'multi' ? ' opt--multi' : '') + (on ? ' is-on' : '') + '" data-v="' + o.v + '"' + (o.exclusive ? ' data-ex="1"' : '') + '>' +
          (o.i ? '<span class="opt__ico">' + o.i + '</span>' : '') +
          '<span class="opt__txt">' + esc(o.t) + (o.s ? '<small>' + esc(o.s) + '</small>' : '') + '</span>' +
          '<span class="opt__dot"></span></button>';
      });
      h += '</div>';
      if (d.type === 'multi') h += '<div class="q__actions"><button type="button" class="btn btn--block" data-next>Далее →</button></div>';
    } else if (d.type === 'region') {
      h += '<h2 class="q__title">' + (isSelf() ? 'В каком регионе вы живёте?' : 'В каком регионе живёт человек?') + '</h2>' +
        '<p class="q__hint">Подберём юриста, который знает местную специфику.</p>' +
        '<div class="field"><label for="fRegion">Регион</label><select class="input" id="fRegion"><option value="">Выберите регион…</option>' +
        REGIONS.map(function (r) { return '<option' + (A.region === r ? ' selected' : '') + '>' + esc(r) + '</option>'; }).join('') + '</select><div class="err" id="eRegion"></div></div>' +
        '<div class="field"><label for="fCity">Город или населённый пункт <span style="font-weight:500">(по желанию)</span></label><input class="input" id="fCity" type="text" autocomplete="address-level2" value="' + esc(A.city || '') + '"></div>' +
        '<div class="q__actions"><button type="button" class="btn btn--block" data-region-next>Далее →</button></div>';
    } else if (d.type === 'contact') {
      h += '<h2 class="q__title">Куда отправить результат проверки?</h2>' +
        '<p class="q__hint">Юрист по пенсиям разберёт ваши ответы и свяжется, чтобы рассказать, что можно пересчитать.</p>' +
        '<div class="field"><label for="fName">Как к вам обращаться?</label><input class="input" id="fName" type="text" autocomplete="given-name" placeholder="Имя" value="' + esc(A.name || '') + '"><div class="err" id="eName"></div></div>' +
        '<div class="field"><label for="fPhone">Телефон для связи</label><input class="input" id="fPhone" type="tel" inputmode="tel" autocomplete="tel" placeholder="+7 (___) ___-__-__" value="' + esc(A.phone || '') + '"><div class="err" id="ePhone"></div></div>' +
        '<div class="field"><label>Как удобнее связаться?</label><div class="chips" id="fMethod">' +
        ['Позвонить', 'WhatsApp', 'Telegram'].map(function (m) { return '<button type="button" class="chip' + ((A.method || 'Позвонить') === m ? ' is-on' : '') + '" data-m="' + m + '">' + m + '</button>'; }).join('') + '</div></div>' +
        '<input class="hp" type="text" id="fHp" name="website" tabindex="-1" autocomplete="off" aria-hidden="true">' +
        '<div class="reward"><span>🎁</span><div><b>В подарок:</b> памятка «Как проверить пенсию самостоятельно» — откроется сразу после результата.</div></div>' +
        '<div class="q__actions"><button type="button" class="btn btn--block btn--pulse" id="fGo">Получить результат проверки</button></div>' +
        '<p class="legal">Нажимая кнопку, вы соглашаетесь на обработку персональных данных в соответствии с <a href="privacy.html" target="_blank" rel="noopener">политикой конфиденциальности</a>. Звонок юриста бесплатный.</p>';
      track('quiz_contact_view');
    }
    body.innerHTML = h;
    bindStep(id, d);
    save();
  }

  function bindStep(id, d) {
    if (id === 'contact') {
      var ph = $('#fPhone'), nm = $('#fName');
      ph.addEventListener('input', function (e) {
        var del = e.inputType && e.inputType.indexOf('delete') === 0;
        var f = fmtPhone(ph.value);
        if (del) f = f.replace(/[^\d]+$/, '');
        ph.value = f; ph.classList.remove('is-bad'); $('#ePhone').textContent = '';
      });
      ph.addEventListener('focus', function () { if (!ph.value) ph.value = '+7 ('; });
      ph.addEventListener('blur', function () { if (ph.value === '+7 (' || ph.value === '+7') ph.value = ''; });
      nm.addEventListener('input', function () { nm.classList.remove('is-bad'); $('#eName').textContent = ''; });
      $('#fMethod').addEventListener('click', function (e) {
        var b = e.target.closest('.chip'); if (!b) return;
        A.method = b.getAttribute('data-m');
        [].forEach.call(this.children, function (c) { c.classList.toggle('is-on', c === b); });
      });
      $('#fGo').addEventListener('click', submit);
      [nm, ph].forEach(function (el) { el.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); }); });
    }
  }

  function fmtPhone(v) {
    var dgt = String(v).replace(/\D/g, '');
    if (dgt[0] === '7' || dgt[0] === '8') dgt = dgt.slice(1);
    dgt = dgt.slice(0, 10);
    if (!dgt.length) return '';
    var o = '+7 (' + dgt.slice(0, 3);
    if (dgt.length >= 3) o += ')';
    if (dgt.length > 3) o += ' ' + dgt.slice(3, 6);
    if (dgt.length > 6) o += '-' + dgt.slice(6, 8);
    if (dgt.length > 8) o += '-' + dgt.slice(8, 10);
    return o;
  }

  /* ---------- навигация ---------- */
  function go(n, push) {
    cur = n;
    if (push !== false) { try { history.pushState({ i: n }, '', '#q' + (n + 1)); } catch (e) {} }
    render();
    var r = quizEl.getBoundingClientRect();
    if (r.top < 60 || r.top > window.innerHeight * 0.6) quizEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function next() {
    var l = steps();
    if (cur < l.length - 1) go(cur + 1);
  }

  /* ---------- события ---------- */
  function onBodyClick(e) {
    var list = steps(), id = list[cur], d = DEFS[id];
    var opt = e.target.closest('.opt');
    if (opt && !locked) {
      var v = opt.getAttribute('data-v');
      if (!A.__started) { A.__started = 1; track('quiz_start'); }
      if (d.type === 'single') {
        locked = true;
        A[id] = v;
        [].forEach.call(body.querySelectorAll('.opt'), function (b) { b.classList.toggle('is-on', b === opt); });
        track('quiz_step_' + (cur + 1), { answer: v });
        setTimeout(next, 230);
      } else {
        var arr = Array.isArray(A[id]) ? A[id].slice() : [];
        var ex = opt.getAttribute('data-ex');
        if (ex) arr = has(arr, v) ? [] : [v];
        else {
          arr = arr.filter(function (x) { return x !== 'none'; });
          var k = arr.indexOf(v);
          if (k === -1) arr.push(v); else arr.splice(k, 1);
        }
        A[id] = arr;
        [].forEach.call(body.querySelectorAll('.opt'), function (b) { b.classList.toggle('is-on', has(arr, b.getAttribute('data-v'))); });
        save();
      }
      return;
    }
    if (e.target.closest('[data-next]') && !locked) {
      locked = true;
      track('quiz_step_' + (cur + 1), { answer: (A[id] || []).join(',') });
      if (!A[id] || !A[id].length) A[id] = ['none'];
      next();
      return;
    }
    if (e.target.closest('[data-region-next]')) {
      var sel = $('#fRegion');
      if (!sel.value) { sel.classList.add('is-bad'); $('#eRegion').textContent = 'Выберите регион из списка'; return; }
      A.region = sel.value; A.city = ($('#fCity').value || '').trim();
      track('quiz_step_' + (cur + 1));
      next();
    }
  }

  /* ---------- расчёт находок ---------- */
  function findings() {
    var f = [], P = A.periods || [];
    var receiving = A.status === 'age' || A.status === 'disab' || A.status === 'surv';
    if (has(P, 'army')) f.push({ i: '🎖️', t: 'Служба в армии', d: 'За каждый год службы по призыву начисляется 1,8 балла. Проверим, засчитан ли этот период.' });
    if (has(P, 'kids')) {
      var n = parseInt(A.kids, 10) || 1;
      f.push({ i: '👶', t: 'Уход за детьми до 1,5 лет', d: 'Баллы за год ухода: за первого ребёнка — 1,8, за второго — 3,6, за третьего и четвёртого — 5,4 (всего не более 6 лет). Детей в отпуске по уходу: ' + (n >= 4 ? '4 и более' : n) + '.' });
    }
    if (has(P, 'care')) f.push({ i: '🧓', t: 'Уход за инвалидом или человеком 80+', d: 'Каждый год подтверждённого ухода даёт 1,8 балла. Подскажем, как это оформить.' });
    if (has(P, 'north')) f.push({ i: '❄️', t: 'Работа на Севере', d: 'Возможны особые условия: более ранний выход на пенсию и повышенная фиксированная выплата.' });
    if (has(P, 'hazard')) f.push({ i: '🏭', t: 'Тяжёлые и вредные условия труда', d: 'Возможна досрочная (льготная) пенсия. Проверим, учтены ли «особые условия» в сведениях о стаже.' });
    if (has(P, 'village') && (A.stazh === '25-35' || A.stazh === '35p' || A.stazh === 'unk')) f.push({ i: '🌾', t: 'Стаж в сельском хозяйстве', d: 'При 30 и более годах сельского стажа фиксированная выплата повышается на 25%.' });
    if (has(P, 'other')) f.push({ i: '🌍', t: 'Безработица и работа за границей', d: 'Периоды получения пособия по безработице и ряд других периодов могут давать баллы — проверим, учтены ли они.' });
    if (A.age === '80' && receiving) f.push({ i: '🎂', t: 'Возраст 80 лет и старше', d: 'С 80 лет фиксированная выплата увеличивается в 2 раза. Убедимся, что повышение применено.' });
    if (A.status === 'disab') f.push({ i: '♿', t: 'Пенсия по инвалидности', d: 'Для инвалидов I группы фиксированная выплата повышена в 2 раза. Проверим, что надбавки начислены полностью.' });
    if (A.status === 'surv') f.push({ i: '🕊️', t: 'Пенсия по потере кормильца', d: 'Проверим, нет ли права на более выгодный вариант пенсии или сочетание выплат.' });
    if (A.work === 'work' && receiving) f.push({ i: '💼', t: 'Вы продолжаете работать', d: 'Пенсия пересчитывается раз в год (с 1 августа), максимум на 3 балла. Проверим, всё ли начислено за ваши рабочие годы.' });
    if (A.work === 'informal') f.push({ i: '💼', t: 'Неофициальные подработки', d: 'Страховые взносы за такие периоды не платились — подскажем, как это влияет на пенсию и что можно сделать.' });
    if (A.status === 'soon' || A.status === 'far') f.push({ i: '🗓️', t: 'Подготовка к выходу на пенсию', d: 'Заранее проверим стаж и баллы — чтобы выйти вовремя и с максимальным размером, а также выяснить, нет ли права на досрочную пенсию.' });
    if (A.pension === 'lt15' || A.pension === '15-20') f.push({ i: '🔎', t: 'Размер пенсии вызывает вопросы', d: 'Сверим расчёт: стаж, баллы, коэффициенты. Именно в таких случаях ошибки заметнее всего.' });
    if (A.docs === 'part' || A.docs === 'none') f.push({ i: '🗄️', t: 'Часть документов утеряна', d: 'Это не препятствие: запросим сведения в архивах и восстановим стаж по справкам и приказам.' });
    f.push({ i: '📄', t: 'Выписка из индивидуального лицевого счёта', d: 'Сверим данные ИЛС с трудовой книжкой: потерянные записи и неверные коды — самая частая причина заниженной пенсии.' });
    f.push({ i: '🧮', t: 'Проверка самого расчёта', d: 'Правильно ли применены баллы, коэффициенты и фиксированная выплата при назначении пенсии.' });
    return f;
  }
  function potential(n) { return n >= 6 ? { l: 'Высокий', p: 92 } : n >= 4 ? { l: 'Выше среднего', p: 74 } : { l: 'Средний', p: 52 }; }

  /* ---------- отправка ---------- */
  function validate() {
    var nm = $('#fName'), ph = $('#fPhone'), ok = true;
    var name = (nm.value || '').trim();
    var digits = (ph.value || '').replace(/\D/g, '');
    if (digits[0] === '7' || digits[0] === '8') digits = digits.slice(1);
    if (name.length < 2) { nm.classList.add('is-bad'); $('#eName').textContent = 'Укажите имя'; ok = false; }
    if (digits.length !== 10) { ph.classList.add('is-bad'); $('#ePhone').textContent = 'Введите номер полностью: 10 цифр после +7'; ok = false; }
    if (!ok) { (nm.classList.contains('is-bad') ? nm : ph).focus(); return null; }
    return { name: name, phone: '+7' + digits };
  }

  function summary() {
    var out = [], l = steps();
    l.forEach(function (id) {
      var d = DEFS[id];
      if (d.type === 'single') { if (A[id]) out.push(d.label + ': ' + optLabel(id, A[id]) + (id === 'kids' && A.kids === '4' ? '' : '')); }
      else if (d.type === 'multi') { if (A[id] && A[id].length) out.push(d.label + ': ' + A[id].map(function (v) { return optLabel(id, v); }).join('; ')); }
    });
    return out;
  }

  function getCid(cb) {
    var done = false;
    function fin(v) { if (!done) { done = true; cb(v || ''); } }
    if (YM_ID && window.ym) { try { setTimeout(fin, 900); window.ym(YM_ID, 'getClientID', fin); return; } catch (e) {} }
    fin('');
  }

  function buildPayload(c, cid) {
    var f = findings();
    var specific = f.length - 2;
    var receiving = A.status === 'age' || A.status === 'disab' || A.status === 'surv';
    return {
      name: c.name, phone: c.phone,
      region: A.region || '', city: A.city || '',
      method: A.method || 'Позвонить',
      who: A.who || 'self',
      summary: summary(),
      findings: f.map(function (x) { return x.t; }),
      score: f.length,
      quality: (specific >= 3 || (receiving && specific >= 2)) ? 'good' : (specific >= 1 ? 'medium' : 'normal'),
      utm: utm(),
      referrer: document.referrer || '',
      page: location.href.split('#')[0],
      yandex_client_id: '',
      website: ($('#fHp') || {}).value || ''
    };
  }

  function post(payload) {
    return fetch(ENDPOINT, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), keepalive: true })
      .then(function (r) { if (!r.ok && r.status !== 202) throw new Error('http ' + r.status); return r.json().catch(function () { return {}; }); });
  }
  function stash(payload) {
    try { var s = JSON.parse(localStorage.getItem(PENDING) || '[]'); s.push(payload); localStorage.setItem(PENDING, JSON.stringify(s.slice(-5))); } catch (e) {}
  }
  function flushPending() {
    var s; try { s = JSON.parse(localStorage.getItem(PENDING) || '[]'); } catch (e) { return; }
    if (!s || !s.length) return;
    try { localStorage.removeItem(PENDING); } catch (e) {}
    s.forEach(function (p) { post(p).catch(function () { stash(p); }); });
  }

  var sending = false;
  function submit() {
    if (sending) return;
    var c = validate(); if (!c) return;
    sending = true;
    A.name = c.name; A.phone = $('#fPhone').value;
    var btn = $('#fGo'); btn.disabled = true; btn.textContent = 'Отправляем…';
    var hp = ($('#fHp') || {}).value || '';
    getCid(function (cid) {
      var payload = buildPayload(c, cid);
      payload.yandex_client_id = cid;
      payload.website = hp;
      post(payload).catch(function () { stash(payload); });
      track('lead_submit');
      finished = true; save();
      showAnalysis();
    });
  }

  /* ---------- анализ и результат ---------- */
  function showAnalysis() {
    bar.style.width = '100%';
    stepEl.textContent = 'Анализ ответов'; leftEl.textContent = '';
    backBtn.disabled = true;
    var items = ['Сверяем стаж и периоды работы', 'Ищем периоды, которые дают баллы и надбавки', 'Оцениваем возможность перерасчёта'];
    body.innerHTML = '<div class="analysis"><h3>Анализируем ваши ответы…</h3><ul class="an__list">' +
      items.map(function (t) { return '<li><span class="an__ic"></span>' + t + '</li>'; }).join('') + '</ul></div>';
    var lis = body.querySelectorAll('.an__list li');
    [].forEach.call(lis, function (li, i) {
      setTimeout(function () { li.classList.add('is-run'); }, i * 1100);
      setTimeout(function () { li.classList.remove('is-run'); li.classList.add('is-done'); }, i * 1100 + 1000);
    });
    setTimeout(renderResult, lis.length * 1100 + 200);
  }

  function renderResult() {
    var f = findings(), pot = potential(f.length);
    var show = f.slice(0, 5);
    var h = '<div class="res__head"><span>✅</span><div><b>' + esc(A.name || 'Проверка') + ', готово!</b>Мы нашли возможных оснований для проверки: ' + f.length + '</div></div>' +
      '<div class="meter"><div class="meter__row"><span>Потенциал для перерасчёта</span><span>' + pot.l + '</span></div><div class="meter__bar"><i id="mFill"></i></div></div>' +
      '<ul class="finds">' + show.map(function (x) { return '<li><span class="fi">' + x.i + '</span><div><b>' + esc(x.t) + '</b><span>' + esc(x.d) + '</span></div></li>'; }).join('') + '</ul>' +
      (f.length > show.length ? '<p class="q__hint" style="margin-top:-4px">…и ещё ' + (f.length - show.length) + ' — юрист разберёт всё подробно.</p>' : '') +
      '<div class="next"><b>Что дальше</b><ol><li>Юрист по пенсиям изучит ваши ответы и свяжется по номеру <b>' + esc(A.phone || '') + '</b> (' + esc(A.method || 'звонок') + ').</li><li>Расскажет, что реально пересчитать, и какие документы понадобятся.</li><li>Поможет подать заявление в СФР.</li></ol></div>' +
      '<div class="res__btns"><a class="btn btn--block" href="' + CHECKLIST + '" target="_blank" rel="noopener" data-checklist>📋 Открыть памятку «Как проверить пенсию»</a>' +
      '<a class="btn btn--block btn--ghost" href="' + PHONE_HREF + '">📞 Позвонить самим: ' + PHONE_SHOW + '</a></div>' +
      '<p class="legal">Оценка предварительная и основана на ваших ответах. Точный результат возможен только после анализа документов. Гарантий конкретного решения СФР или суда мы не даём.</p>';
    body.innerHTML = h;
    quizEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setTimeout(function () { var m = $('#mFill'); if (m) m.style.width = pot.p + '%'; }, 80);
    var ck = $('[data-checklist]'); if (ck) ck.addEventListener('click', function () { track('checklist_open'); });
    track('quiz_result_view');
    try { localStorage.removeItem(STORE); } catch (e) {}
    sending = false;
  }

  /* ---------- старт ---------- */
  function init() {
    quizEl = $('#quiz'); body = $('#qBody'); bar = $('#qBar'); stepEl = $('#qStep'); leftEl = $('#qLeft'); backBtn = $('#qBack');
    if (!quizEl) return;
    restore();
    try { history.replaceState({ i: cur }, '', cur ? '#q' + (cur + 1) : location.pathname + location.search); } catch (e) {}
    body.addEventListener('click', onBodyClick);
    backBtn.addEventListener('click', function () { if (cur > 0 && !finished) go(cur - 1, false); });
    window.addEventListener('popstate', function (e) {
      if (finished) return;
      var i = e.state && typeof e.state.i === 'number' ? e.state.i : 0;
      cur = Math.min(i, steps().length - 1);
      render();
    });
    render();
    flushPending();

    var sticky = $('#sticky');
    if (sticky && 'IntersectionObserver' in window) {
      var vis = true;
      new IntersectionObserver(function (en) {
        vis = en[0].isIntersecting;
        sticky.classList.toggle('is-on', !vis && window.scrollY > 300);
      }, { threshold: 0.12 }).observe(quizEl);
      window.addEventListener('scroll', function () { sticky.classList.toggle('is-on', !vis && window.scrollY > 300); }, { passive: true });
    }
    [].forEach.call(document.querySelectorAll('[data-go-quiz]'), function (a) {
      a.addEventListener('click', function () { track('cta_click'); });
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
