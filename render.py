"""抽卡/干员箱图片渲染（复刻 Amiya-Bot amiyabot-arknights-gacha 的素材与绘制逻辑）。

- 抽卡拼图（1~10 抽）：官方组图背景 bg.png + 稀有度边框 + 立绘中央裁剪 + 职业图标，
  最后整体 0.8 倍缩放 —— 与 Amiya create_gacha_image 一致；
- 干员箱：浅灰底 + 头像 + 数量角标（rank/1-6.png），按稀有度分组 —— 与 Amiya box.py 一致；
- PIL 或素材缺失时返回 None，调用方回退纯文本。
"""

import logging
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


def render_user_card(
    uid: str,
    nickname: str,
    user: dict,
    avatar_cache: str,
    custom_font: str = "",
    fetch_avatar: bool = True,
    jade_cap: int = 30000,
) -> str | None:
    """复刻 Amiya userInfo 卡片：700×300 模糊背景 + 头像 + 签到/信赖/心情 + 合成玉进度 + 抽卡统计柱状图 + 条码。

    字段映射：信赖值 = user.feeling（签到 +50，显示 feeling/10%）；心情值 = user.mood/15*100%。
    布局为清晰三栏（头像昵称 / 签到+合成玉 / 抽卡统计+星级柱状图），右缘竖条码，避免拥挤与溢出。
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:
        return None
    card_w, card_h = 700, 300
    try:
        bg_path = _user_card_asset("user_info.jpeg")
        if not os.path.exists(bg_path):
            return None
        bg = Image.open(bg_path).convert("RGB")
        bg = bg.resize((card_w + 20, card_h + 20))
        bg = bg.filter(ImageFilter.GaussianBlur(radius=14))
        bg = bg.crop((10, 10, 10 + card_w, 10 + card_h))
        img = bg
        draw = ImageDraw.Draw(img)

        f22 = _font(22, custom_font)
        f15 = _font(15, custom_font)
        f14 = _font(14, custom_font)
        f13 = _font(13, custom_font)
        f12 = _font(12, custom_font)

        # ---- 左栏：头像 + 昵称 ----
        frame, inner = 84, 66
        fx, fy = 20, 30
        avatar_path = _load_user_avatar(uid, avatar_cache, fetch_avatar)
        draw.rounded_rectangle((fx, fy, fx + frame, fy + frame), radius=16, outline="#d4b28c", width=3)
        if avatar_path and os.path.exists(avatar_path):
            av = Image.open(avatar_path).convert("RGBA")
            side = min(av.size)
            av = av.crop(
                ((av.width - side) // 2, (av.height - side) // 2, (av.width + side) // 2, (av.height + side) // 2)
            )
            av = av.resize((inner, inner), Image.Resampling.LANCZOS)
            img.paste(av, (fx + (frame - inner) // 2, fy + (frame - inner) // 2), _circle_avatar(inner))
        nx = fx + frame + 16  # 120
        name = (str(nickname) or "博士").split("#")[0] or "博士"
        _card_text(draw, (nx, fy + 2), _fit_text(draw, name, f22, 130), f22, "#000000")
        _card_text(draw, (nx, fy + 34), f"#{uid}", f14, "#9E9E9E", shadow=False)

        # ---- 中列：签到信息 ----
        cx = 270
        row = 38
        info = [
            f"最后签到日期：{user.get('sign_date') or '尚未签到'}",
            f"累计签到次数：{user.get('sign_days', 0)}",
            f"阿米娅的信赖值：{int(user.get('feeling', 0) or 0) // 10}%",
            f"阿米娅的心情值：{max(0, min(100, round(int(user.get('mood', 15) or 15) / 15 * 100)))}%",
        ]
        for ln in info:
            _card_text(draw, (cx, row), ln, f15, "#000000")
            row += 25
        # 合成玉
        jade = int(user.get("jade", 0) or 0)
        jade_today = int(user.get("jade_today", 0) or 0)
        _card_text(draw, (cx, row + 6), f"剩余合成玉：{jade}", f15, "#000000")
        _card_text(draw, (cx, row + 30), f"今日已获取合成玉：{jade_today}/{jade_cap}", f14, "#000000")
        bar_w = 175
        _rounded_progress(draw, (cx, row + 54, cx + bar_w, row + 72), jade_today / jade_cap if jade_cap else 0)

        # ---- 右列：抽卡统计 ----
        rx = 530
        y0 = 38
        n_box = len(user.get("box") or {})
        coupon = int(user.get("coupon", 0) or 0)
        break_even = int(user.get("break_even", 0) or 0)
        total = int(user.get("total", 0) or 0)
        _card_text(draw, (rx, y0), f"干员拥有数：{n_box}", f15, "#000000")
        _card_text(draw, (rx, y0 + 26), f"剩余寻访凭证：{coupon}", f15, "#000000")
        prefix_w = draw.textlength("已经抽取了 ", font=f15)
        _card_text(draw, (rx, y0 + 52), "已经抽取了 ", f15, "#000000")
        _card_text(draw, (rx + prefix_w, y0 + 52), f"{break_even}", f15, "#e91e63")
        _card_text(draw, (rx, y0 + 78), "次而未获得六星干员", f15, "#000000")
        _card_text(draw, (rx, y0 + 104), f"抽卡总次数：{total}", f15, "#000000")

        # ---- 星级分布柱状图（名称 + 条形图内百分比） ----
        stats = user.get("stats") or {}
        colors = {6: "#ee6665", 5: "#fac858", 4: "#5470c6", 3: "#67c23a"}
        cy = y0 + 134
        if total > 0:
            for rarity in (6, 5, 4, 3):
                cnt = int(stats.get(str(rarity), 0) or 0)
                pct = cnt / total * 100
                _card_text(draw, (rx, cy), f"{rarity}★", f13, colors[rarity], shadow=False)
                bx = rx + 36
                bw = 96
                draw.rounded_rectangle((bx, cy + 1, bx + bw, cy + 15), radius=7, fill="#ffffff")
                draw.rounded_rectangle(
                    (bx, cy + 1, bx + max(2, int(bw * pct / 100)), cy + 15),
                    radius=7,
                    fill=colors[rarity],
                )
                pct_txt = f"{pct:.1f}%"
                pct_w = draw.textlength(pct_txt, font=f12)
                # 百分比优先画在填充条上（深色白字 / 浅色黑字）
                tx = bx + bw - pct_w - 4
                text_fill = "#ffffff" if rarity in (6, 4) else "#000000"
                draw.text((tx + 1, cy + 1), pct_txt, font=f12, fill=text_fill)
                draw.text((tx, cy), pct_txt, font=f12, fill=text_fill)
                cy += 21
        else:
            _card_text(draw, (rx, cy + 40), "无抽卡数据", f15, "#000000")

        # ---- 右缘竖条码 ----
        stripes = _barcode_stripes(uid)
        bx2 = card_w - 12
        sy = 42
        for i, on in enumerate(stripes):
            color = "#000000" if on else "#cccccc"
            draw.rectangle((bx2, sy + i * 4, bx2 + 3, sy + i * 4 + 4), fill=color)
        draw.rounded_rectangle((bx2 - 3, sy - 8, bx2 + 7, sy + len(stripes) * 4 + 6), radius=4, outline="#9E9E9E", width=1)

        out = os.path.join(avatar_cache, f"user_card_{int(time.time() * 1000)}_{random.randrange(100000)}.png")
        os.makedirs(avatar_cache, exist_ok=True)
        img.save(out, "PNG")
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[arknights_fun] 用户信息卡片渲染失败: {e}")
        return None

