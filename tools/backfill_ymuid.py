#!/usr/bin/env python3
# НЕ РЕДАКТИРОВАТЬ ОТСЮДА — см. предупреждение в app/lk.py. deploy.sh исключает этот файл.
"""Восстановление _ym_uid в уже созданных лидах Б24 из логов сайтов и повторная отправка в Метрику.

Что делает:
  1. Берёт из Б24 лиды за --hours часов, у которых в карточке нет _ym_uid.
  2. Ищет эту заявку в логах сайтов (leads.log: телефон + время) и достаёт оттуда _ym_uid
     (cookie _ym_uid или, если cookie не было, yandex_client_id).
  3. С --apply дописывает строку «_ym_uid: …» в комментарий лида.
  4. С --resend запускает в ЛК ручную проверку источника за тот же период: ЛК увидит новый
     _ym_uid и отправит конверсию (повторы он не шлёт: уже отправленные лиды пропускаются).

Без --apply ничего не меняется, скрипт только показывает, что нашёл. Номера телефонов не выводятся.

Примеры (на сервере, от root):
  python3 tools/backfill_ymuid.py --source-id 2 --hours 504
  python3 tools/backfill_ymuid.py --source-id 2 --hours 504 --apply --resend
Только стандартная библиотека. Вебхук Б24 берётся из ЛК (расшифровывается в контейнере и в вывод не попадает);
можно передать вручную через --webhook или переменную B24_WEBHOOK.
"""
from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

APP_DIR = "/var/www/dima/data/addAvto"
DEFAULT_LOGS = [
    "/var/www/dima/data/storage*/*leads*.log",
    "/var/www/dima/data/*/api/storage/*leads*.log",
]
MSK = timezone(timedelta(hours=3))
MAX_GAP = timedelta(hours=12)  # заявка из лога и лид в Б24 могут разойтись, если лид добирал retry.php

UID_PATTERNS = [
    r"(?<![A-Za-z])_?ym_uid\s*[:=]\s*(\d+)",
    r"yandex[_\s]*client[_\s]*id\s*[:=]\s*(\d+)",
    r"client[_\s]*id\s*[:=]\s*(\d+)",
]


def digits10(value) -> str:
    return re.sub(r"\D", "", str(value or ""))[-10:]


def has_uid(lead: dict) -> bool:
    for text in (lead.get("COMMENTS") or "", lead.get("UF_CRM_COOKIES") or "", lead.get("UF_CRM_YM_UID") or ""):
        if any(re.search(p, str(text), re.I) for p in UID_PATTERNS):
            return True
    return False


def lead_phone10(lead: dict) -> str:
    phones = lead.get("PHONE")
    if isinstance(phones, list) and phones and isinstance(phones[0], dict):
        return digits10(phones[0].get("VALUE"))
    return ""


def read_logs(patterns: list[str]) -> dict[str, list[tuple[datetime, str]]]:
    """phone10 → [(время заявки, _ym_uid)] только для записей, где идентификатор есть."""
    out: dict[str, list[tuple[datetime, str]]] = {}
    files: list[str] = []
    for pattern in patterns:
        files.extend(glob.glob(pattern))
    for path in sorted(set(files)):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.match(r"\[([^\]]+)\]\s*(\{.*)", line)
                if not m:
                    continue
                try:
                    rec = json.loads(m.group(2))
                    ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=MSK)
                except (ValueError, json.JSONDecodeError):
                    continue
                uid = ((rec.get("cookies") or {}).get("_ym_uid") or rec.get("ym_uid")
                       or rec.get("yandex_client_id") or "")
                uid = re.sub(r"\D", "", str(uid))
                phone = digits10(rec.get("phone"))
                if uid and phone:
                    out.setdefault(phone, []).append((ts, uid))
    return out


def match_uid(lead: dict, logs: dict[str, list[tuple[datetime, str]]]) -> str | None:
    entries = logs.get(lead_phone10(lead))
    if not entries:
        return None
    try:
        created = datetime.fromisoformat(lead["DATE_CREATE"])
    except (KeyError, ValueError):
        return None
    ts, uid = min(entries, key=lambda e: abs(e[0] - created))
    return uid if abs(ts - created) <= MAX_GAP else None


def b24(webhook: str, method: str, params: dict) -> dict:
    url = webhook.rstrip("/") + "/" + method + ".json"
    req = urllib.request.Request(url, data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    last = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(attempt * 2)
    raise RuntimeError(f"Б24 не отвечает: {last}")


def fetch_leads(webhook: str, hours: int) -> list[dict]:
    date_from = (datetime.now().astimezone() - timedelta(hours=hours)).isoformat(timespec="seconds")
    leads, start = [], 0
    while True:
        data = b24(webhook, "crm.lead.list", {
            "filter": {">=DATE_CREATE": date_from},
            "select": ["*", "UF_*", "PHONE"],
            "order": {"DATE_CREATE": "DESC"},
            "start": start,
        })
        leads.extend(data.get("result") or [])
        if data.get("next") is None or not data.get("result"):
            return leads
        start = int(data["next"])
        time.sleep(0.25)


def webhook_from_lk(source_id: int) -> str:
    code = (
        "import sys; sys.path.insert(0,'/app')\n"
        "from app import db\n"
        "from app.token_crypto import decrypt_secret\n"
        f"r = db.connect().execute('SELECT webhook_enc FROM b24_sources WHERE id={int(source_id)}').fetchone()\n"
        "print(decrypt_secret(r[0]) if r else '')\n"
    )
    res = subprocess.run(["docker", "compose", "exec", "-T", "app", "python", "-c", code],
                         cwd=APP_DIR, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    hook = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
    if not hook.startswith("https://"):
        sys.exit("Не удалось получить вебхук источника из ЛК (проверьте --source-id)")
    return hook


def lk_run(source_id: int, hours: int) -> None:
    env = {}
    with open(os.path.join(APP_DIR, ".env"), encoding="utf-8") as fh:
        for line in fh:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                env[k.strip()] = v.strip().strip("\"'")
    token = base64.b64encode(f"{env['APP_USERNAME']}:{env['APP_PASSWORD']}".encode()).decode()
    req = urllib.request.Request(f"http://127.0.0.1:8000/api/lk/sources/{source_id}/run?hours={hours}",
                                 method="POST", headers={"Authorization": "Basic " + token})
    with urllib.request.urlopen(req, timeout=600) as resp:
        print("ЛК:", json.loads(resp.read()).get("stats"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source-id", type=int, help="номер источника Б24 в ЛК (оттуда берётся вебхук)")
    ap.add_argument("--webhook", default=os.getenv("B24_WEBHOOK", ""))
    ap.add_argument("--hours", type=int, default=504, help="за сколько часов смотреть лиды (по умолчанию 21 день)")
    ap.add_argument("--logs", nargs="*", default=DEFAULT_LOGS, help="маски файлов leads.log сайтов")
    ap.add_argument("--apply", action="store_true", help="записать _ym_uid в комментарии лидов")
    ap.add_argument("--resend", action="store_true", help="после записи запустить проверку источника в ЛК")
    args = ap.parse_args()

    webhook = args.webhook or (webhook_from_lk(args.source_id) if args.source_id else "")
    if not webhook:
        sys.exit("Нужен --source-id или --webhook")

    logs = read_logs(args.logs)
    print(f"В логах сайтов заявок с идентификатором: {sum(len(v) for v in logs.values())}")
    leads = fetch_leads(webhook, args.hours)
    missing = [l for l in leads if not has_uid(l)]
    print(f"Лидов за {args.hours} ч: {len(leads)}, без _ym_uid: {len(missing)}")

    found: list[tuple[dict, str]] = []
    no_phone = no_log = 0
    for lead in missing:
        if not lead_phone10(lead):
            no_phone += 1
            continue
        uid = match_uid(lead, logs)
        if uid:
            found.append((lead, uid))
        else:
            no_log += 1
    print(f"Найден _ym_uid в логах: {len(found)} | нет телефона в лиде: {no_phone} | "
          f"в логах заявки нет или в ней нет _ym_uid: {no_log}")

    if not found:
        print("Восстанавливать нечего.")
        return
    for lead, uid in found:
        print(f"  лид {lead['ID']} ({lead['DATE_CREATE'][:16]}) ← {uid}")
    if not args.apply:
        print("Это пробный просмотр. Для записи в Б24 добавьте --apply.")
        return

    done = 0
    for lead, uid in found:
        comments = (lead.get("COMMENTS") or "").rstrip()
        b24(webhook, "crm.lead.update", {
            "id": lead["ID"],
            "fields": {"COMMENTS": (comments + "\n" if comments else "") + f"_ym_uid: {uid}"},
        })
        done += 1
        time.sleep(0.3)
    print(f"Обновлено лидов: {done}")
    if args.resend:
        if not args.source_id:
            sys.exit("Для --resend нужен --source-id")
        lk_run(args.source_id, args.hours)
    else:
        print("ЛК отправит их сам при ближайшем опросе (до 10 минут), если они укладываются в его глубину проверки; "
              "иначе добавьте --resend.")


if __name__ == "__main__":
    main()
