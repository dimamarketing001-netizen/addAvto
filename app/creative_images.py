"""Картинки креативов, которые лежат у НАС на диске (2026-11-10).

Директ (AdImages.add) принимает только байты картинки — ссылок он не
знает. Раньше addAvto скачивал картинку по ссылке в момент запуска, но CDN
Ozon отдаёт серверу 403, и фото товара в объявления не попадали. Теперь:
  * фото Ozon скачивает Chrome в WSL (ozon_fetch.py) при генерации;
  * пользователь загружает файлы с компьютера (визард, шаг 5);
и всё это сохраняется здесь — в data/creative-images/ рядом с БД (том
./data в compose.yaml переживает пересборку). В проекте (image_urls)
хранится ссылка вида "local:<sha256>.jpg"; при запуске main.py читает
файл и отправляет байты в Директ.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
from pathlib import Path

# Форматы, которые принимает AdImages.add Директа (WebP — нет, а
# конвертировать нечем: Pillow в образе нет).
MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif"}
MAX_BYTES = 10 * 1024 * 1024
REF_PREFIX = "local:"
_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png|gif)$")


class CreativeImageError(ValueError):
    pass


def images_dir() -> Path:
    base = Path(os.getenv("DATABASE_PATH", "./data/app.db")).parent
    path = base / "creative-images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sniff_mime(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def save_image(data: bytes) -> str:
    """Сохраняет картинку (по содержимому, без дублей) → "local:<имя>"."""
    if not data:
        raise CreativeImageError("Пустой файл")
    if len(data) > MAX_BYTES:
        raise CreativeImageError("Картинка больше 10 МБ — Директ такую не примет")
    mime = _sniff_mime(data)
    if mime is None:
        raise CreativeImageError("Нужен JPG, PNG или GIF — Директ принимает только эти форматы")
    name = hashlib.sha256(data).hexdigest()[:32] + MIME_EXT[mime]
    path = images_dir() / name
    if not path.exists():
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    return REF_PREFIX + name


def save_data_url(data_url: str) -> str:
    head, sep, b64 = (data_url or "").partition(",")
    if not sep or not head.startswith("data:image/") or ";base64" not in head:
        raise CreativeImageError("Ожидается data:image/...;base64,...")
    try:
        data = base64.b64decode(b64, validate=False)
    except ValueError as exc:
        raise CreativeImageError("Битый base64") from exc
    return save_image(data)


def is_ref(value: str) -> bool:
    return isinstance(value, str) and value.startswith(REF_PREFIX) and bool(_NAME_RE.match(value[len(REF_PREFIX):]))


def path_of(ref_or_name: str) -> Path:
    name = ref_or_name[len(REF_PREFIX):] if ref_or_name.startswith(REF_PREFIX) else ref_or_name
    if not _NAME_RE.match(name):
        raise CreativeImageError("Некорректное имя картинки")
    path = images_dir() / name
    if not path.exists():
        raise CreativeImageError("Картинка не найдена на сервере")
    return path


def image_size(data: bytes) -> tuple[int, int] | None:
    """Ширина и высота из заголовка JPEG/PNG/GIF (без Pillow — его нет в образе)."""
    import struct
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", data[6:10])
        if data[:3] == b"\xff\xd8\xff":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + length
    except (struct.error, IndexError):
        return None
    return None


def direct_image_ok(data: bytes) -> bool:
    """Требования Директа к картинке текстово-графического объявления:
    стороны 450–5000 px, пропорции от 3:4 до 4:3 (живая ошибка 2026-11-11:
    фото 896×1200 = 0,747 → «Image size is invalid»). Неизвестный размер —
    пускаем, пусть решает Директ."""
    size = image_size(data)
    if not size:
        return True
    w, h = size
    if min(w, h) < 450 or max(w, h) > 5000:
        return False
    return 0.75 <= w / h <= 4 / 3


def read_b64(ref: str) -> str:
    return base64.b64encode(path_of(ref).read_bytes()).decode("ascii")


def data_url(ref: str) -> str:
    path = path_of(ref)
    mime = next(m for m, ext in MIME_EXT.items() if path.suffix == ext)
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


# --- Импорт картинок из подборок парсера (2026-11-13) -----------------------
# Креативы парсера — ссылки avatars.mds.yandex.net/get-…/<id>/<размер>
# (y300, wy150, huge…): маленькие и часто 3:2 / 16:9. Директ (ТГО) берёт
# 450–5000 px и 3:4…4:3 — берём оригинал и обрезаем по центру.
_AVATARS_RE = re.compile(r"^(https?://avatars\.mds\.yandex\.net/get-[^/]+/\d+/[^/?#]+)/[^/?#]+$")
MIN_SIDE = 450
UPSCALE_MAX = 1.6  # не растягиваем мелочь больше чем в 1,6 раза — будет мыло


def avatars_original(url: str) -> str | None:
    """Ссылка на оригинал для аватарницы Яндекса, иначе None."""
    m = _AVATARS_RE.match((url or "").strip())
    return m.group(1) + "/orig" if m else None


def fit_for_direct(data: bytes) -> bytes:
    """Обрезка по центру до 4:3 / 3:4, ресайз в 450–5000 px, JPEG.
    CreativeImageError — если картинка слишком мелкая или битая."""
    import io

    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise CreativeImageError("На сервере нет Pillow — обрезка картинок недоступна") from exc
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise CreativeImageError("Не удалось прочитать картинку") from exc
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    elif img.mode == "L":
        img = img.convert("RGB")
    w, h = img.size
    ratio = w / h
    if ratio > 4 / 3:
        nw = int(h * 4 / 3)
        left = (w - nw) // 2
        img = img.crop((left, 0, left + nw, h))
    elif ratio < 0.75:
        nh = int(w / 0.75)
        top = (h - nh) // 2
        img = img.crop((0, top, w, top + nh))
    w, h = img.size
    short = min(w, h)
    if short < MIN_SIDE:
        k = MIN_SIDE / short
        if k > UPSCALE_MAX:
            raise CreativeImageError(f"Картинка слишком маленькая ({w}×{h}) — Директу нужно от 450 px")
        img = img.resize((max(MIN_SIDE, round(w * k)), max(MIN_SIDE, round(h * k))), Image.LANCZOS)
    w, h = img.size
    if max(w, h) > 5000:
        k = 5000 / max(w, h)
        img = img.resize((round(w * k), round(h * k)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()
