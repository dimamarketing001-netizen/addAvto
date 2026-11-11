FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Ozon-режим массового запуска (app/ozon_ad_creator.py) резервным путём
# открывает настоящий Chromium через Playwright, когда обычный HTTP-запрос
# блокируется антиботом Ozon — ставим браузер и его системные зависимости
# прямо в образ (--with-deps сам делает apt-get, поэтому делаем это ДО
# переключения на appuser), плюс Xvfb: Ozon блокирует headless Chrome (см.
# докстринг ozon_ad_creator.py), а у контейнера нет своего дисплея — CMD
# ниже запускает uvicorn под xvfb-run, чтобы "видимому" браузеру было что
# открыть.
# Плюс настоящий Google Chrome (OZON_BROWSER_CHANNEL=chrome): на проде
# 2026-11-10 Ozon отдавал встроенному Chromium 403 даже в видимом окне, а
# Google Chrome он пропускает. Chromium остаётся запасным — OzonBrowser
# откатывается на него, если Chrome не найден.
# libatomic1: node внутри Playwright ≥1.65 без неё не запускается
# («libatomic.so.1: cannot open shared object file», сборка 2026-11-11).
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && \
    playwright install --with-deps chromium && \
    playwright install --with-deps chrome && \
    apt-get update && apt-get install -y --no-install-recommends xvfb xauth x11-utils && \
    rm -rf /var/lib/apt/lists/* && \
    chmod -R a+rX /opt/pw-browsers

COPY app ./app
RUN mkdir -p /app/data && useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
# With network_mode: host (compose.yaml), 0.0.0.0 here would mean uvicorn
# listens on ALL of the host's interfaces, not just loopback — a real
# exposure regression from the previous bridge+port-mapping setup, which
# only published 127.0.0.1:8000. Bind to loopback explicitly to keep the
# same "nginx-only, nothing else reaches it directly" posture.
CMD ["xvfb-run", "-a", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"]
