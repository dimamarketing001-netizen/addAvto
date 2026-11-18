(function(){
  'use strict';

  // Читаем данные из localStorage (переданы с квиза)
  let result = null;
  try {
    const raw = localStorage.getItem('ca_last_result');
    if(raw) result = JSON.parse(raw);
  } catch(e){}

  const nameEl = document.getElementById('userName');
  const verdictBlock = document.getElementById('verdictBlock');
  const analysisBlock = document.getElementById('analysisBlock');
  const timerText = document.getElementById('timerText');
  const saveCard = document.getElementById('saveContactCard');
  const savePhoneNumEl = document.getElementById('saveContactPhone');
  const saveBtn = document.getElementById('saveContactBtn');

  if(!result){
    verdictBlock.innerHTML = `
      <div class="sps__verdict-status">✅ Ваша заявка принята в работу</div>
      <div style="margin-top:16px;font-size:16px;color:#4B5563;line-height:1.55">
        Наш юрист изучит вашу ситуацию и позвонит в течение 15 минут для консультации.
      </div>
    `;
    analysisBlock.innerHTML = `
      <div class="sps__analysis-item"><span class="dot"></span><span>Расскажем о всех возможных вариантах списания долгов</span></div>
      <div class="sps__analysis-item"><span class="dot"></span><span>Оценим шансы на успех и подберём оптимальную стратегию</span></div>
      <div class="sps__analysis-item"><span class="dot"></span><span>Ответим на все вопросы про сроки, стоимость и процедуру</span></div>
    `;
    return;
  }

  // Имя
  if(nameEl && result.name) nameEl.textContent = result.name;

  // Регион и город
  const heroTextEl = document.querySelector('.sps__hero-text');
  if(heroTextEl && (result.region || result.city)){
    const location = [result.city, result.region].filter(Boolean).join(', ');
    if(location){
      heroTextEl.innerHTML = `Мы получили ваши ответы. <strong>Юрист по вашему региону (${escapeHtml(location)})</strong> позвонит в течение 15 минут с персональным планом списания долгов`;
    }
  }

  // Вердикт
  const verdictTitles = {
    strong: { icon: '✅', text: 'Отличные шансы на списание' },
    good:   { icon: '✅', text: 'Высокая вероятность успеха' },
    medium: { icon: '✅', text: 'Есть хорошие перспективы' },
    'need-consult': { icon: '📋', text: 'Требуется консультация юриста' }
  };
  const vt = verdictTitles[result.verdict] || verdictTitles.medium;

  verdictBlock.innerHTML = `
    <div class="sps__verdict-status">${vt.icon} ${vt.text}</div>
    <div style="margin-top:14px">
      <div style="font-size:12px;color:#6B7280;font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">Возможная сумма списания:</div>
      <div class="sps__verdict-sum">${formatMoney(result.minSum)} – ${formatMoney(result.maxSum)} ₽</div>
      <div class="sps__verdict-chance">Вероятность одобрения: <b>${result.chance}%</b></div>
    </div>
  `;

  // Анализ
  if(result.analysis && result.analysis.length){
    analysisBlock.innerHTML = result.analysis.map(item => `
      <div class="sps__analysis-item">
        <span class="dot"></span>
        <span>${item}</span>
      </div>
    `).join('');
  }

  // ===== ПЕРСОНАЛЬНЫЙ НОМЕР ДЛЯ СОХРАНЕНИЯ =====
  if(result.phoneCall && saveCard){
    const rawPhone = String(result.phoneCall).replace(/\D/g, '');
    let displayPhone = rawPhone;
    let telHref = rawPhone;

    // Форматируем русский номер: 79254294268 -> +7 (925) 429-42-68
    if(rawPhone.length === 11 && (rawPhone.startsWith('7') || rawPhone.startsWith('8'))){
      const p = rawPhone.length === 10 ? '7' + rawPhone : rawPhone;
      const clean = p[0] === '8' ? '7' + p.slice(1) : p;
      displayPhone = `+7 (${clean.slice(1,4)}) ${clean.slice(4,7)}-${clean.slice(7,9)}-${clean.slice(9,11)}`;
      telHref = '+' + clean;
    } else if(rawPhone.length === 10){
      // 10 цифр (без 7 впереди)
      const clean = '7' + rawPhone;
      displayPhone = `+7 (${clean.slice(1,4)}) ${clean.slice(4,7)}-${clean.slice(7,9)}-${clean.slice(9,11)}`;
      telHref = '+' + clean;
    } else {
      // Как есть
      telHref = '+' + rawPhone;
      displayPhone = '+' + rawPhone;
    }

    // Показываем номер
    savePhoneNumEl.textContent = displayPhone;
    savePhoneNumEl.href = 'tel:' + telHref;

    // Кнопка сохранить — vCard blob
    if(saveBtn){
      const vcardText =
        'BEGIN:VCARD\n' +
        'VERSION:3.0\n' +
        'FN:Юрист (Списание долгов)\n' +
        'N:Юрист;Мой Юрист;;;\n' +
        'ORG:ООО «Мой юрист»\n' +
        'TITLE:Персональный юрист\n' +
        'TEL;TYPE=CELL:' + telHref + '\n' +
        'NOTE:Юрист по вашей заявке. Не блокируйте — важный звонок!\n' +
        'END:VCARD';

      const blob = new Blob([vcardText], { type: 'text/vcard;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      saveBtn.href = url;
      saveBtn.setAttribute('download', 'Yurist_Moy_Yurist.vcf');

      // Метрика
      saveBtn.addEventListener('click', () => {
        if(window.ym){ try { window.ym(111540304, 'reachGoal', 'save_contact_click'); } catch(e){} }
      });
    }

    // Показываем блок
    saveCard.hidden = false;
  }

  // Таймер обратного отсчёта
  let seconds = 15 * 60;
  function updateTimer(){
    if(seconds <= 0){
      timerText.textContent = 'Скоро позвоним!';
      return;
    }
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    timerText.textContent = `Звонок через ~${m}:${s.toString().padStart(2, '0')}`;
    seconds--;
  }
  updateTimer();
  setInterval(updateTimer, 1000);

  // Событие в метрику
  if(window.ym){ try { window.ym(111540304, 'reachGoal', 'quiz_result_view'); } catch(e){} }
  if(window.dataLayer){ window.dataLayer.push({ event: 'quiz_result_view' }); }

  function formatMoney(n){
    return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  }
  function escapeHtml(str){
    return String(str).replace(/[&<>"']/g, m => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[m]));
  }
})();
