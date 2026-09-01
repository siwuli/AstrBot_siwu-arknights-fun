"""抽卡/干员箱图片渲染（复刻 Amiya-Bot amiyabot-arknights-gacha 的素材与绘制逻辑）。

- 抽卡拼图（1~10 抽）：官方组图背景 bg.png + 稀有度边框 + 立绘中央裁剪 + 职业图标，
  最后整体 0.8 倍缩放 —— 与 Amiya create_gacha_image 一致；
- 干员箱：浅灰底 + 头像 + 数量角标（rank/1-6.png），按稀有度分组 —— 与 Amiya box.py 一致；
- PIL 或素材缺失时返回 None，调用方回退纯文本。
"""

import logging
import os
import random
import time
import urllib.request

from .gamedata import AVATAR_DIR, PORTRAIT_DIR, GameData

logger = logging.getLogger("astrbot")

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
GACHA_ASSETS = os.path.join(ASSETS_DIR, "gacha")
CLASSIFY_ASSETS = os.path.join(ASSETS_DIR, "classify")
RANK_ASSETS = os.path.join(ASSETS_DIR, "rank")

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


def _op_art(data: GameData, name: str, cache_dir: str, fetch: bool) -> str | None:
    """返回干员立绘/头像路径：优先共享 portrait，其次 avatar；缺失时按需下载到插件缓存。"""
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


def render_pulls(
    data: GameData,
    results: list[dict],
    pool_name: str,
    cache_dir: str,
    fetch: bool = True,
    custom_font: str = "",
) -> str | None:
    """复刻 Amiya create_gacha_image：背景 + 边框 + 立绘 + 职业图标，0.8 倍缩放。

    不足 10 抽时裁掉背景右侧未使用区域，保留 Amiya 左侧版式。
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    bg_path = os.path.join(GACHA_ASSETS, "bg.png")
    if not os.path.exists(bg_path):
        return None
    n = len(results)
    if n <= 0:
        return None
    try:
        image = Image.open(bg_path)
        draw = ImageDraw.ImageDraw(image)
        x = 78
        used = 0
        for r in results[:10]:
            rarity = int(r["rarity"])
            frame = os.path.join(GACHA_ASSETS, f"{rarity}.png")
            if os.path.exists(frame):
                img = Image.open(frame).convert("RGBA")
                image.paste(img, box=(x, 0), mask=img)
            portrait = _op_art(data, r["name"], cache_dir, fetch)
            if portrait and os.path.exists(portrait):
                img = Image.open(portrait).convert("RGBA")
                radio = 252 / img.size[1]
                width = int(img.size[0] * radio)
                height = int(img.size[1] * radio)
                step = int((width - 82) / 2)
                crop = (step, 0, width - step, height)
                img = img.resize(size=(width, height))
                img = img.crop(crop)
                image.paste(img, box=(x, 112), mask=img)
            draw.rectangle((x + 10, 321, x + 70, 381), fill="white")
            op = data.operators.get(r["name"]) or {}
            prof = str(op.get("prof") or "").lower()
            class_img = os.path.join(CLASSIFY_ASSETS, f"{prof}.png")
            if os.path.exists(class_img):
                img = Image.open(class_img).convert("RGBA").resize((59, 59))
                image.paste(img, box=(x + 11, 322), mask=img)
            x += 82
            used += 1
        if used < 10:
            width = 78 + 82 * used
            image = image.crop((0, 0, width, image.size[1]))
        w, h = image.size
        image = image.resize((int(w * 0.8), int(h * 0.8)), Image.Resampling.LANCZOS)
        os.makedirs(cache_dir, exist_ok=True)
        out = os.path.join(cache_dir, f"pulls_{int(time.time() * 1000)}_{random.randrange(100000)}.png")
        image.save(out, "PNG")
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[arknights_fun] 抽卡拼图渲染失败: {e}")
        return None


def render_box(data: GameData, box: dict[str, int], cache_dir: str, custom_font: str = "") -> str | None:
    """复刻 Amiya box.py：浅灰底 + 60px 头像 + rank 角标，按稀有度 6→5→4→3 分组排列。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    if not box:
        return None
    collect: dict[int, list[tuple[str, int, str | None]]] = {6: [], 5: [], 4: [], 3: []}
    for name, cnt in box.items():
        op = data.operators.get(name) or {}
        r = int(op.get("rarity", 3))
        if r in collect:
            collect[r].append((name, int(cnt), _op_art(data, name, cache_dir, False)))

    size = 60
    rank_size = 20
    padding = 10
    y_pos = 52 - size
    max_length = 10
    placed: list[tuple[str, tuple[int, int], int]] = []
    for rarity in (6, 5, 4, 3):
        x_pos = padding
        y_pos += size + 10
        for index, (_name, cnt, art) in enumerate(collect[rarity]):
            if art and os.path.exists(art):
                placed.append((art, (x_pos, y_pos), size))
                rank_file = os.path.join(RANK_ASSETS, f"{min(cnt, 6)}.png")
                if os.path.exists(rank_file):
                    placed.append(
                        (rank_file, (x_pos + size - rank_size, y_pos + size - rank_size), rank_size),
                    )
            if (index + 1) % max_length == 0:
                x_pos = padding
                y_pos += size
            else:
                x_pos += size

    width = size * max_length + padding * 2
    height = y_pos + padding + size
    img = Image.new("RGB", (width, height), (245, 245, 245))  # '#F5F5F5'
    draw = ImageDraw.Draw(img)
    f_head = _font(16, custom_font)
    draw.text((padding, padding), "博士，这是您的干员列表（按获取顺序）", fill=(0, 0, 0), font=f_head)
    draw.text(
        (padding, padding + 22),
        '请注意，以下为"兔兔抽卡"插件记录的 BOX，并非您的真实 BOX。',
        fill=(0, 0, 0),
        font=f_head,
    )
    for art, pos, sz in placed:
        try:
            src = Image.open(art).convert("RGBA")
            w0, h0 = src.size
            side = min(w0, h0)
            src = src.crop(((w0 - side) // 2, (h0 - side) // 2, (w0 + side) // 2, (h0 + side) // 2))
            src = src.resize((sz, sz), Image.Resampling.LANCZOS)
            img.paste(src, pos, src)
        except Exception as e:  # noqa: BLE001, PERF203
            logger.warning(f"[arknights_fun] 干员箱图片处理失败: {e}")
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, f"box_{int(time.time() * 1000)}_{random.randrange(100000)}.png")
    img.save(out, "PNG")
    return out
