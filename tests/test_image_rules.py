"""Картинки для Директа (2026-11-11): «Image size is invalid» на фото 896×1200
(уже 3:4) — такие отсеиваем; неудачная картинка не валит кампанию."""
from __future__ import annotations

import struct

from app import creative_images as ci


def _png(w, h):
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"


def _jpeg(w, h):
    sof = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", h, w) + b"\x03" + b"\x00" * 9
    return b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9 + sof + b"\xff\xd9"


def test_image_size_and_direct_rules():
    assert ci.image_size(_png(1000, 800)) == (1000, 800)
    assert ci.image_size(_jpeg(896, 1200)) == (896, 1200)
    assert ci.direct_image_ok(_jpeg(896, 1200)) is False      # 0,747 < 3:4
    assert ci.direct_image_ok(_jpeg(1920, 2560)) is True      # ровно 3:4
    assert ci.direct_image_ok(_jpeg(1000, 1000)) is True
    assert ci.direct_image_ok(_png(400, 400)) is False        # меньше 450
    assert ci.direct_image_ok(_png(6000, 5000)) is False      # больше 5000
    assert ci.direct_image_ok(_png(1600, 900)) is False       # 16:9 шире 4:3
    assert ci.direct_image_ok(b"\xff\xd8\xff\xd9") is True    # размер не прочитан — решает Директ
