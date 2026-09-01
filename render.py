"""抽卡/干员箱图片渲染（复刻 Amiya-Bot amiyabot-arknights-gacha 的素材与绘制逻辑）。

- 抽卡拼图（1~10 抽）：官方组图背景 bg.png + 稀有度边框 + 立绘中央裁剪 + 职业图标，
  最后整体 0.8 倍缩放 —— 与 Amiya create_gacha_image 一致；
- 干员箱：浅灰底 + 头像 + 数量角标（rank/1-6.png），按稀有度分组 —— 与 Amiya box.py 一致；
- PIL 或素材缺失时返回 None，调用方回退纯文本。
"""

import asyncio
import base64
import contextlib
import json
import logging
import mimetypes
import os
import random
import re
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
    os.path.join(ASSETS_DIR, "font", "HarmonyOS_Sans_SC.ttf"),
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

    注意：仅十连调用本函数；1~9 抽走 render_detailed（text_image 样式）。
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

_RARITY_COLOR = {6: "FF4343", 5: "FEA63A", 4: "A288B5", 3: "7F7F7F", 2: "7F7F7F", 1: "7F7F7F"}
_TI_FONT_SIZE = 15
_TI_LINE_HEIGHT = 16
_TI_PADDING = 10
_TI_BGCOLOR = "#F5F5F5"


def _insert_empty(text, max_num: int, half: bool = False) -> str:
    """复刻 Amiya core.util.insert_empty：右补空格到指定宽度。"""
    return f"{text}{('　' if half else ' ') * (max_num - len(str(text)))}"


def _char_width(draw, char, font) -> int:
    bbox = draw.multiline_textbbox((0, 0), char, font=font)
    return bbox[2] - bbox[0]


def _parse_text_rows(text: str, font, fill_color: str = "#000000"):
    """复刻 Amiya TextParser：解析 [cl xxx@#RRGGBB cle] 彩色标记并分行。

    返回 (rows, line, width_seat)：rows 为 {text,color,width,enter} 字典列表。
    """
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    search = re.findall(r"\[cl\s(.*?)@#(.*?)\scle]", text)
    color_pos = {0: fill_color}
    for item in search:
        temp = f"[cl {item[0]}@#{item[1]} cle]"
        index = text.index(temp)
        color_pos[index] = f"#{item[1]}"
        color_pos[index + len(item[0])] = fill_color
        text = text.replace(temp, item[0], 1)
    rows = []
    line = 0
    width_seat = 0
    length = 0
    sub_text = ""
    cur_color = fill_color
    for idx, char in enumerate(text):
        if idx in color_pos:
            if cur_color != color_pos[idx] and sub_text:
                rows.append({"text": sub_text, "color": cur_color, "width": length, "enter": False})
                sub_text = ""
                length = 0
            cur_color = color_pos[idx]
        length += _char_width(draw, char, font)
        sub_text += char
        width_seat = max(width_seat, length)
        is_end = idx == len(text) - 1
        if length >= float("inf") or char == "\n" or is_end:
            enter = True
            if not is_end and text[idx + 1] == "\n" and char != "\n":
                enter = False
            if enter:
                line += 1
            rows.append({"text": sub_text, "color": cur_color, "width": length, "enter": enter})
            sub_text = ""
            length = 0
    return rows, line, width_seat


def _create_text_image(text: str, icons, custom_font: str = ""):
    """复刻 amiyabot imageCreator.create_image（Chain.text_image 的默认参数）。"""
    from PIL import Image, ImageDraw

    font = _font(_TI_FONT_SIZE, custom_font)
    rows, line, width_seat = _parse_text_rows(text, font)
    width = width_seat + _TI_PADDING * 2 + 50
    height = (line + 2) * _TI_LINE_HEIGHT
    image = Image.new("RGB", (width, height), _TI_BGCOLOR)
    draw = ImageDraw.Draw(image)
    row = 0
    col = _TI_PADDING
    for item in rows:
        draw.text(
            (col, _TI_PADDING + row * _TI_LINE_HEIGHT),
            item["text"],
            font=font,
            fill=item["color"],
        )
        col += item["width"]
        if item["enter"]:
            row += 1
            col = _TI_PADDING
    for path, size, pos in icons:
        if not path or not os.path.exists(path):
            continue
        img = Image.open(path).convert("RGBA")
        pos = [int(n if n >= 0 else width + n) for n in pos]
        item_width = int(size * (img.width / img.height))
        item_height = size
        offset_x = (item_height - item_width) / 2
        if offset_x:
            pos[0] += int(offset_x)
        img = img.resize(size=(item_width, item_height))
        image.paste(img, box=(pos[0], pos[1]), mask=img)
    return image


def build_detailed_text(
    results: list[dict],
    times: int,
    pool_name: str,
    check_break_even: str,
    colored: bool = False,
) -> str:
    """复刻 Amiya GachaBuilder.detailed_mode 的简历文本（colored=True 含 [cl] 彩色标记）。"""
    result = f"阿米娅给博士扔来了{times}张简历，博士细细地检阅着...\n\n【{pool_name}】\n\n"
    for item in results:
        name = str(item["name"])
        rarity = int(item["rarity"])
        if colored:
            star = f"[cl {'★' * rarity}@#{_RARITY_COLOR.get(rarity, '7F7F7F')} cle]"
        else:
            star = "★" * rarity
        result += f"{' ' * 15}{_insert_empty(name, 6, True)}{star}\n\n"
    result += f"\n{check_break_even}"
    return result


def _op_icon(data: GameData, name: str, cache_dir: str, fetch: bool) -> str | None:
    """返回干员头像路径：与 Amiya detailed_mode 一致（头像优先，其次立绘/按需下载）。"""
    op = data.operators.get(name)
    oid = str((op or {}).get("id") or "")
    if not oid:
        return None
    for p in (
        os.path.join(AVATAR_DIR, f"{oid}#1.png"),
        os.path.join(PORTRAIT_DIR, f"{oid}#1.png"),
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


def render_detailed(
    data: GameData,
    results: list[dict],
    times: int,
    pool_name: str,
    check_break_even: str,
    cache_dir: str,
    fetch: bool = True,
    custom_font: str = "",
) -> str | None:
    """复刻 Amiya detailed_mode：1~9 抽的 text_image（浅灰底文字列表 + 左侧头像图标）。

    图片宽度自适应文本，高度 = (文本行数 + 2) * 16；星级按稀有度着色。
    """
    try:
        from PIL import Image, ImageDraw  # noqa: F401
    except ImportError:
        return None
    text = build_detailed_text(results, times, pool_name, check_break_even, colored=True)
    icons = []
    icon_size = 32
    offset = int((_TI_LINE_HEIGHT * 3 - icon_size) / 2)
    top = _TI_PADDING + _TI_LINE_HEIGHT * 2 + offset + 5
    for index, r in enumerate(results):
        icon = _op_icon(data, str(r["name"]), cache_dir, fetch)
        icons.append((icon, icon_size, (_TI_PADDING, top + offset + icon_size * index)))
    try:
        image = _create_text_image(text, icons, custom_font)
        os.makedirs(cache_dir, exist_ok=True)
        out = os.path.join(cache_dir, f"pulls_{int(time.time() * 1000)}_{random.randrange(100000)}.png")
        image.save(out, "PNG")
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[arknights_fun] 抽卡文字图渲染失败: {e}")
        return None

RARITY_COLOR = _RARITY_COLOR


def render_text_image(text: str, cache_dir: str, custom_font: str = "") -> str | None:
    """复刻 Chain.text_image：纯文字转图（用于 >10 抽的 continuous_mode 统计图）。

    浅灰底 F5F5F5、HarmonyOS 15px、宽度自适应、行高 16、彩色 [cl] 星级标记。
    """
    try:
        from PIL import Image, ImageDraw  # noqa: F401
    except ImportError:
        return None
    try:
        image = _create_text_image(text, [], custom_font)
        os.makedirs(cache_dir, exist_ok=True)
        out = os.path.join(cache_dir, f"pulls_{int(time.time() * 1000)}_{random.randrange(100000)}.png")
        image.save(out, "PNG")
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[arknights_fun] 统计文字图渲染失败: {e}")
        return None


_USER_CARD_ASSETS = os.path.join(ASSETS_DIR, "user_card")
_USER_AVATAR_URL = "https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640"


def _user_card_asset(name: str) -> str:
    return os.path.join(_USER_CARD_ASSETS, name)


def _load_user_avatar(uid: str, cache_dir: str, fetch: bool = True) -> str | None:
    """QQ 头像：本地缓存优先，其次按需下载 qlogo；失败回退默认 avatar.webp。"""
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        target = os.path.join(cache_dir, f"user_{uid}.png")
        if os.path.exists(target):
            return target
        if fetch:
            try:
                req = urllib.request.Request(
                    _USER_AVATAR_URL.format(qq=uid),
                    headers={"User-Agent": "Mozilla/5.0 (AstrBot arknights_fun)"},
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    raw = resp.read()
                if raw:
                    with open(target, "wb") as f:
                        f.write(raw)
                    return target
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[arknights_fun] QQ 头像下载失败 {uid}: {e}")
    fallback = _user_card_asset("avatar.webp")
    return fallback if os.path.exists(fallback) else None


def _card_text(draw, pos, text, font, fill="#000000", shadow=True):
    """带 1px 浅灰阴影的卡片文字（复刻 CSS text-shadow: 1px 1px 2px #dcdcdc）。"""
    x, y = pos
    if shadow:
        draw.text((x + 1, y + 1), text, font=font, fill="#dcdcdc")
    draw.text((x, y), text, font=font, fill=fill)


def _circle_avatar(size: int) -> object:
    """返回 size×size 圆形蒙版。"""
    from PIL import Image, ImageDraw

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    return mask


def _barcode_stripes(seed_text: str, count: int = 34, bar: int = 2) -> list[bool]:
    rnd = random.Random(seed_text)
    return [rnd.random() < 0.55 for _ in range(count)]


def _rounded_progress(draw, box, ratio: float, color: str = "#e91e63"):
    """粉色圆角进度条（复刻 .jade-point/.block）。"""
    x0, y0, x1, y1 = box
    radius = (y1 - y0) // 2
    draw.rounded_rectangle(box, radius=radius, outline=color, width=1)
    fill_w = max(0, int((x1 - x0 - 2) * min(1.0, max(0.0, ratio))))
    if fill_w > 0:
        draw.rounded_rectangle((x0 + 1, y0 + 1, x0 + 1 + fill_w, y1 - 1), radius=radius, fill=color)


def _vtext(draw, pos, text, font, fill="#000000"):
    """竖排（逐字）绘制文字，取字符中心对齐。"""
    x, y = pos
    for _ch in text:
        draw.text((x, y), _ch, font=font, fill=fill)
        bbox = draw.textbbox((0, 0), _ch, font=font)
        y += bbox[3] - bbox[1] + 2


def _fit_text(draw, text: str, font, max_w: int) -> str:
    """按像素宽度截断文本，超长补省略号。"""
    if draw.textlength(text, font=font) <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(text[:mid], font=font) + draw.textlength("…", font=font) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"

_USER_CARD_TEMPLATE = os.path.join(_USER_CARD_ASSETS, "userInfo.html")
_PLAYWRIGHT = None
_BROWSER = None


async def _ensure_browser():
    """复用进程内的 Playwright Chromium（与查询插件一致的浏览器渲染方案）。"""
    global _PLAYWRIGHT, _BROWSER
    if _BROWSER is not None:
        return _BROWSER
    from playwright.async_api import async_playwright

    _PLAYWRIGHT = await async_playwright().start()
    _BROWSER = await _PLAYWRIGHT.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
    return _BROWSER


def _build_card_data(uid, nickname, user, avatar_cache, fetch_avatar, operators):
    """（同步）组装 Amiya userInfo 模板数据；模板缺失返回 (None, None)。"""
    if not os.path.exists(_USER_CARD_TEMPLATE):
        return None, None
    avatar_url = ""
    try:
        av_path = _load_user_avatar(uid, avatar_cache, fetch_avatar)
        if av_path and os.path.exists(av_path):
            with open(av_path, "rb") as f:
                raw = f.read()
            mime = mimetypes.guess_type(av_path)[0] or "image/png"
            avatar_url = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[arknights_fun] 头像读取失败: {e}")
    ops = operators or {}
    box = user.get("box") or {}
    box_parts = []
    for name, cnt in box.items():
        r = int((ops.get(name) or {}).get("rarity", 3))
        box_parts.append(f"{name}:{r}:{cnt}")
    data = {
        "nickname": str(nickname),
        "avatar": avatar_url,
        "user": {"user_id": str(uid)},
        "user_info": {
            "sign_date": str(user.get("sign_date") or ""),
            "sign_times": int(user.get("sign_days", 0) or 0),
            "user_feeling": int(user.get("feeling", 0) or 0),
            "user_mood": int(user.get("mood", 15) or 15),
            "jade_point": int(user.get("jade", 0) or 0),
            "jade_point_max": int(user.get("jade_today", 0) or 0),
        },
        "user_gacha_info": {
            "coupon": int(user.get("coupon", 0) or 0),
            "gacha_break_even": int(user.get("break_even", 0) or 0),
        },
        "operator_box": {"operator": "|".join(box_parts)},
    }
    return avatar_url, data


def _save_png(out, shot):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        f.write(shot)
    return out


async def render_user_card(
    uid: str,
    nickname: str,
    user: dict,
    avatar_cache: str,
    custom_font: str = "",
    fetch_avatar: bool = True,
    jade_cap: int = 30000,
    operators: dict | None = None,
) -> str | None:
    """用 Playwright 浏览器渲染 Amiya 原版 userInfo 模板 → 截图（与查询插件一致）。

    数据完全按 Amiya userInfo.html 的 window.init(data)：user_info / user_gacha_info /
    operator_box / nickname / avatar。信怠值 = user.feeling（签到 +50，显示 feeling/10%）。
    custom_font / jade_cap 参数仅为兼容旧签名保留。
    """
    avatar_url, data = await asyncio.to_thread(
        _build_card_data, uid, nickname, user, avatar_cache, fetch_avatar, operators
    )
    if data is None:
        return None
    try:
        path_abs = await asyncio.to_thread(os.path.abspath, _USER_CARD_TEMPLATE)
        url = "file:///" + path_abs.replace("\\", "/")
        browser = await _ensure_browser()
        page = await browser.new_page(viewport={"width": 700, "height": 320}, device_scale_factor=2)
        await page.goto(url, timeout=30000)
        await page.wait_for_load_state("load", timeout=30000)
        await page.evaluate("if ('init' in window) { init(" + json.dumps(data) + ") }")
        await asyncio.sleep(0.8)
        with contextlib.suppress(Exception):
            await page.wait_for_function(
                "() => Array.from(document.querySelectorAll('img')).every(i => i.complete)",
                timeout=10000,
            )
        shot = await page.screenshot(full_page=True)
        await page.close()
        out = os.path.join(avatar_cache, f"user_card_{int(time.time() * 1000)}_{random.randrange(100000)}.png")
        await asyncio.to_thread(_save_png, out, shot)
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[arknights_fun] 用户信息卡片浏览器渲染失败: {e}")
        return None
