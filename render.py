"""抽卡结果 / 干员箱图片渲染（PIL 可选依赖，缺失或失败时调用方回退纯文本）。"""

import logging
import os
import random
import time
import urllib.request

from .gamedata import AVATAR_DIR, PORTRAIT_DIR, GameData

logger = logging.getLogger("astrbot")

RARITY_RGB = {6: (255, 67, 67), 5: (254, 166, 58), 4: (162, 136, 181), 3: (136, 136, 136)}
PROF_CN = {
    "PIONEER": "先锋",
    "WARRIOR": "近卫",
    "TANK": "重装",
    "SNIPER": "狙击",
    "CASTER": "术师",
    "MEDIC": "医疗",
    "SUPPORT": "辅助",
    "SPECIAL": "特种",
    "TOKEN": "召唤物",
}

_AVATAR_URL = (
    "https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/"
    "avatar/{oid}%231.png"
)

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _find_font(custom: str = "") -> str | None:
    """返回可用中文字体路径；找不到返回 None（用默认字体）。"""
    for p in ([custom] if custom else []) + _FONT_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def _font(size: int, custom: str = ""):
    from PIL import ImageFont

    path = _find_font(custom)
    try:
        return ImageFont.truetype(path, size) if path else ImageFont.load_default(size=size)
    except (OSError, TypeError):
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _rgb(rarity: int) -> tuple[int, int, int]:
    return RARITY_RGB.get(rarity, (136, 136, 136))


def _op_art(data: GameData, name: str, cache_dir: str, fetch: bool) -> str | None:
    """返回干员立绘/头像路径：共享 portrait 或 avatar 目录，缺失时按需下载到插件缓存。"""
    op = data.operators.get(name)
    oid = str((op or {}).get("id") or "")
    if not oid:
        return None
    for p in (
        os.path.join(PORTRAIT_DIR, f"{oid}#1.png"),
        os.path.join(AVATAR_DIR, f"{oid}#1.png"),
        os.path.join(cache_dir, f"{oid}.png") if cache_dir else "",
    ):
        if p and os.path.exists(p):
            return p
    if fetch and cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        target = os.path.join(cache_dir, f"{oid}.png")
        try:
            req = urllib.request.Request(
                _AVATAR_URL.format(oid=oid),
                headers={"User-Agent": "Mozilla/5.0 (AstrBot arknights_fun)"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
            with open(target, "wb") as f:
                f.write(raw)
            return target
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[arknights_fun] 头像下载失败 {name}: {e}")
    return None


def _draw_card(img, draw, name: str, rarity: int, prof: str, art: str | None, x: int, y: int, w: int, h: int, font_path: str) -> None:
    """绘制单张干员卡片（稀有度描边 + 立绘/头像 + 名字 + 星级 + 职业）。"""
    rgb = _rgb(rarity)
    draw.rounded_rectangle([x, y, x + w - 1, y + h - 1], radius=12, outline=rgb, width=4)
    art_top = y + 6
    art_bottom = y + int(h * 0.62)
    inner_w = w - 12
    inner_h = art_bottom - art_top
    if art and inner_w > 0 and inner_h > 0:
        try:
            from PIL import Image

            src = Image.open(art).convert("RGBA")
            scale = inner_h / src.height
            nw = max(1, int(src.width * scale))
            src = src.resize((nw, inner_h), Image.Resampling.LANCZOS)
            if nw > inner_w:
                left = (nw - inner_w) // 2
                src = src.crop((left, 0, left + inner_w, inner_h))
            img.paste(src, (x + 6, art_top), src)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[arknights_fun] 立绘处理失败 {name}: {e}")

    f_name = _font(20, font_path)
    f_star = _font(18, font_path)
    f_cls = _font(14, font_path)
    text_y = art_bottom + 6
    draw.text(
        (x + (w - draw.textlength(name, font=f_name)) / 2, text_y),
        name,
        fill=(40, 40, 40),
        font=f_name,
    )
    stars = "★" * rarity
    text_y += 26
    draw.text(
        (x + (w - draw.textlength(stars, font=f_star)) / 2, text_y),
        stars,
        fill=rgb,
        font=f_star,
    )
    prof_cn = PROF_CN.get(prof, prof or "")
    if prof_cn:
        text_y += 24
        draw.text(
            (x + (w - draw.textlength(prof_cn, font=f_cls)) / 2, text_y),
            prof_cn,
            fill=(120, 120, 120),
            font=f_cls,
        )


def render_pulls(
    data: GameData,
    results: list[dict],
    pool_name: str,
    cache_dir: str,
    fetch: bool = True,
    custom_font: str = "",
) -> str | None:
    """渲染抽卡结果拼图（≤10 抽时调用）。返回图片路径；失败返回 None。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    n = len(results)
    if n <= 0:
        return None
    per_row = 5
    rows = (n + per_row - 1) // per_row
    card_w, card_h = 172, 236
    pad = 10
    header_h = 44
    cols = min(n, per_row)
    img_w = cols * card_w + (cols + 1) * pad
    img_h = header_h + rows * card_h + (rows + 1) * pad
    img = Image.new("RGB", (img_w, img_h), (248, 249, 252))
    draw = ImageDraw.Draw(img)
    draw.text((pad, 12), f"【{pool_name}】", fill=(60, 60, 60), font=_font(22, custom_font))
    for i, r in enumerate(results):
        row, col = divmod(i, per_row)
        x = pad + col * (card_w + pad)
        y = header_h + pad + row * (card_h + pad)
        art = _op_art(data, r["name"], cache_dir, fetch)
        prof = (data.operators.get(r["name"]) or {}).get("prof", "")
        _draw_card(img, draw, r["name"], r["rarity"], prof, art, x, y, card_w, card_h, custom_font)
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, f"pulls_{int(time.time() * 1000)}_{random.randrange(100000)}.png")
    img.save(out, "PNG")
    return out


def render_box(data: GameData, box: dict[str, int], cache_dir: str, custom_font: str = "") -> str | None:
    """渲染干员箱拼图（最多展示 30 位；其余见文本列表）。返回图片路径；失败返回 None。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    items: list[tuple[dict | None, str, int, str | None]] = []
    for name, cnt in box.items():
        op = data.operators.get(name)
        oid = str((op or {}).get("id") or "")
        art = None
        if oid:
            for p in (
                os.path.join(PORTRAIT_DIR, f"{oid}#1.png"),
                os.path.join(AVATAR_DIR, f"{oid}#1.png"),
            ):
                if os.path.exists(p):
                    art = p
                    break
        items.append((op, name, int(cnt), art))
    items.sort(key=lambda it: (-(it[0] or {}).get("rarity", 3), it[1]))
    items = items[:30]
    if not items:
        return None

    size = 78
    per_row = 6
    pad = 12
    header_h = 44
    rows = (len(items) + per_row - 1) // per_row
    img_w = per_row * size + (per_row + 1) * pad
    img_h = header_h + rows * (size + 12) + pad
    img = Image.new("RGB", (img_w, img_h), (248, 249, 252))
    draw = ImageDraw.Draw(img)
    draw.text(
        (pad, 10),
        f"博士的干员箱（{sum(box.values())} 位，图片展示前 {len(items)}）",
        fill=(60, 60, 60),
        font=_font(20, custom_font),
    )
    for i, (op, name, cnt, art) in enumerate(items):
        row, col = divmod(i, per_row)
        x = pad + col * (size + pad)
        y = header_h + pad + row * (size + 12)
        rgb = _rgb(int((op or {}).get("rarity", 3)))
        if art:
            try:
                src = Image.open(art).convert("RGBA")
                w0, h0 = src.size
                side = min(w0, h0)
                src = src.crop(((w0 - side) // 2, (h0 - side) // 2, (w0 + side) // 2, (h0 + side) // 2))
                src = src.resize((size, size), Image.Resampling.LANCZOS)
                img.paste(src, (x, y), src)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[arknights_fun] 干员箱图片处理失败 {name}: {e}")
                draw.rounded_rectangle([x, y, x + size - 1, y + size - 1], radius=8, outline=rgb, width=3)
        else:
            draw.rounded_rectangle([x, y, x + size - 1, y + size - 1], radius=8, outline=rgb, width=3)
            f_cls = _font(13, custom_font)
            draw.text(
                (x + (size - draw.textlength(name, font=f_cls)) / 2, y + size // 2 - 8),
                name[:4],
                fill=(100, 100, 100),
                font=f_cls,
            )
        draw.rounded_rectangle([x + 4, y + size - 24, x + 4 + draw.textlength(f"x{cnt}", font=_font(16, custom_font)) + 6, y + size - 4], radius=6, fill=(70, 70, 70))
        draw.text((x + 7, y + size - 22), f"x{cnt}", fill=(255, 255, 255), font=_font(16, custom_font))
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, f"box_{int(time.time() * 1000)}_{random.randrange(100000)}.png")
    img.save(out, "PNG")
    return out
