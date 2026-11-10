"""Обновляет app/geo_regions_ru.json — справочник регионов Яндекса по России
на РУССКОМ (2026-11-10).

Зачем: через Click.ru (Dictionaries.get) названия приходят только
по-английски — прокси Click.ru не передаёт Accept-Language. Напрямую Direct
API отдаёт русские. Справочник почти не меняется, поэтому он хранится в
репозитории файлом, и на сервере токен Директа не нужен.

Запуск (токен — OAuth Direct API, НЕ коммитить):
    YANDEX_DIRECT_TOKEN=... python tools/refresh_geo_regions.py
Формат файла: {"source", "fetched_at", "regions": [[id, parent_id, type, name], ...]}
— только Россия: субъекты, города и их предки (сёла/поселения отброшены).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RUSSIA = 225
KEEP_TYPES = {"Country", "Federal district", "Federation subject", "Administrative area", "City"}
OUT = Path(__file__).resolve().parent.parent / "app" / "geo_regions_ru.json"


def fetch(token: str) -> list[dict]:
    req = urllib.request.Request(
        "https://api.direct.yandex.com/json/v5/dictionaries",
        data=json.dumps({"method": "get", "params": {"DictionaryNames": ["GeoRegions"]}}).encode(),
        headers={"Authorization": f"Bearer {token}", "Accept-Language": "ru",
                 "Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        raise SystemExit(f"Direct API: {data['error']}")
    return data["result"]["GeoRegions"]


def russia_subset(raw: list[dict]) -> list[list]:
    nodes = {int(r["GeoRegionId"]): r for r in raw}

    def chain(rid: int) -> list[int]:
        out, cur = [], nodes[rid].get("ParentId")
        while cur is not None and cur in nodes and cur not in out and len(out) < 32:
            out.append(cur)
            cur = nodes[cur].get("ParentId")
        return out

    keep: set[int] = {RUSSIA}
    for rid, r in nodes.items():
        c = chain(rid)
        if RUSSIA in c and r.get("GeoRegionType") in KEEP_TYPES:
            keep.add(rid)
            keep.update(a for a in c[: c.index(RUSSIA)])  # промежуточные предки до России
    return [[rid, nodes[rid].get("ParentId"), nodes[rid].get("GeoRegionType"), nodes[rid].get("GeoRegionName")]
            for rid in sorted(keep)]


def main() -> None:
    token = os.getenv("YANDEX_DIRECT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Задайте YANDEX_DIRECT_TOKEN (OAuth-токен Direct API)")
    regions = russia_subset(fetch(token))
    OUT.write_text(json.dumps({
        "source": "Yandex Direct API v5 Dictionaries.get GeoRegions, Accept-Language: ru",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "regions": regions,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT}: {len(regions)} регионов", file=sys.stderr)


if __name__ == "__main__":
    main()
