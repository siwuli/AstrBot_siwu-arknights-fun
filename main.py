"""AstrBot 明日方舟娱乐整合插件（arknights_fun）——签到 + 抽卡 + 猜干员。

统一经济闭环（参考 Amiya-Bot 生态移植）：
- 签到：每天发放寻访凭证（可配合成玉）；
- 抽卡：优先消耗寻访凭证，凭证不足按 600 合成玉/抽抵扣（均可配置）；
- 猜干员：答对赚合成玉（连击加成），每局前三名排名奖励，受 jade_max 上限约束。
- 数据：公共 gamedata 目录（与生态内其他方舟插件共享，不重复下载），
  自带独立下载器，启动/定时增量更新，未下载时内置资产兜底，开箱即用。

命令（需 @ 或唤醒词；娱乐命令全员可用）：
  签到
  单抽 / 十连 / N连(N抽/N次寻访 / 一百连 / 三百抽)，1~300
  保底 / 卡池 / 卡池切换 N / box / 我的干员
  寻访凭证 / 我的资源 / 凭证还有多少
  猜干员 / 猜干员 难度 / 猜干员排行 / 猜干员数据 / 数据状态
"""

import asyncio
import json
import logging
import os
import threading
import time

from astrbot.api import star
from astrbot.api.all import AstrBotConfig, AstrMessageEvent
from astrbot.api.event import filter
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from . import render as render_mod
from .admin_web import ArknightsFunWebAdmin
from .gacha import JADE_PER_PULL, PULL_RE, GachaEngine, parse_pull_count
from .gamedata import (
    EXCEL_DIR,
    POOLS_CACHE,
    GameData,
    download_excel_sync,
    download_pools_sync,
    excel_ready,
)
from .guess import _SESSIONS, DIFFICULTIES, GameActiveFilter, GuessEngine

logger = logging.getLogger("astrbot")

LOG_TAG = "[arknights_fun]"

DATA_DIR = os.path.join(get_astrbot_plugin_data_path(), "arknights_fun")
USER_FILE = os.path.join(DATA_DIR, "users.json")
AVATAR_CACHE = os.path.join(DATA_DIR, "avatars")
POOL_OVERRIDE_FILE = os.path.join(DATA_DIR, "pool_overrides.json")

# 旧独立插件的遗留数据（一次性迁移，成功后不再读取）
LEGACY_GACHA_FILE = os.path.join(
    get_astrbot_plugin_data_path(), "arknights_gacha", "user_data.json",
)
LEGACY_SCORE_FILE = os.path.join(
    get_astrbot_plugin_data_path(), "guess_operator", "scoreboard.json",
)

_RESOURCE_RE = (
    r"(?:寻访凭证|凭证|合成玉|玉).{0,8}(?:多少|几|剩|还有)"
    r"|(?:多少|还剩|还有).{0,8}(?:凭证|合成玉|玉)"
)

_user_lock = threading.Lock()


def _insert_empty(text, max_num: int, half: bool = False) -> str:
    """复刻 Amiya core.util.insert_empty：文本/数字右补空格到指定宽度。"""
    return f"{text}{('　' if half else ' ') * (max_num - len(str(text)))}"


def _default_user(config: AstrBotConfig) -> dict:
    """新玩家初始数据（凭证/合成玉初始值可配置）。"""
    return {
        "coupon": int(config.get("gacha_coupon_init", 10) or 10),
        "jade": int(config.get("gacha_jade_init", 0) or 0),
        "sign_date": "",
        "sign_days": 0,
        "break_even": 0,
        "pool": "",
        "stats": {"3": 0, "4": 0, "5": 0, "6": 0},
        "box": {},
        "total": 0,
        "guess_score": 0,
        "feeling": 0,
        "mood": 15,
        "jade_today": 0,
        "jade_today_date": "",
    }


class ArknightsFunPlugin(star.Star):
    """整合插件主类：签到 / 抽卡 / 猜干员 + 统一用户经济。"""

    def __init__(self, context, config: AstrBotConfig = None):
        super().__init__(context, config)
        self.config = config or {}
        self.data = GameData()
        self.guess = GuessEngine(self.data)
        self._users: dict[str, dict] = {}
        self._pool_overrides: dict[str, dict] = {}
        self._load_users()
        self._load_pool_overrides()
        self._migrate_legacy()
        self._start_download_if_needed()
        self._register_admin_web(context)

    # ------------------------------------------------------------------
    # 用户数据（统一经济）
    # ------------------------------------------------------------------
    def _load_users(self) -> None:
        try:
            with open(USER_FILE, encoding="utf-8") as f:
                data = json.load(f)
            self._users = {str(k): v for k, v in (data or {}).items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self._users = {}

    def _save_users(self) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = USER_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._users, f, ensure_ascii=False, indent=2)
        os.replace(tmp, USER_FILE)

    def _user_by_uid(self, uid: str) -> dict:
        if uid not in self._users:
            self._users[uid] = _default_user(self.config)
        return self._users[uid]

    def _user(self, event: AstrMessageEvent) -> dict:
        return self._user_by_uid(str(event.get_sender_id() or ""))

    def _with_user(self, uid: str, fn) -> dict:
        """在锁内读取-修改-保存用户数据；返回修改后的用户（调用方只读）。"""
        with _user_lock:
            user = self._user_by_uid(uid)
            fn(user)
            self._save_users()
            return user

    def _add_jade(self, user: dict, amount: int) -> int:
        """为玩家加合成玉（受 jade_max 上限约束），返回实际到账。"""
        if amount <= 0:
            return 0
        cap = int(self._cfg("jade_max", 30000) or 0)
        current = int(user.get("jade", 0) or 0)
        granted = amount if cap <= 0 else max(0, min(amount, cap - current))
        user["jade"] = current + granted
        today = time.strftime("%Y-%m-%d")
        if user.get("jade_today_date") != today:
            user["jade_today_date"] = today
            user["jade_today"] = 0
        user["jade_today"] = int(user.get("jade_today", 0) or 0) + granted
        return granted

    async def _grant_jade(self, uid: str, jade: int) -> None:
        if jade <= 0:
            return
        with _user_lock:
            user = self._user_by_uid(str(uid))
            self._add_jade(user, jade)
            self._save_users()

    async def _add_guess_points(self, uid: str, points: int) -> None:
        if points <= 0:
            return
        with _user_lock:
            user = self._user_by_uid(str(uid))
            user["guess_score"] = int(user.get("guess_score", 0)) + points
            self._save_users()

    def _migrate_legacy(self) -> None:
        """将旧独立插件（arknights_gacha / guess_operator）的用户数据一次性并入。"""
        if os.path.exists(USER_FILE):
            return
        migrated = False
        try:
            if os.path.exists(LEGACY_GACHA_FILE):
                with open(LEGACY_GACHA_FILE, encoding="utf-8") as f:
                    old = json.load(f)
                for uid, d in (old or {}).items():
                    if not isinstance(d, dict):
                        continue
                    u = self._user_by_uid(str(uid))
                    u["coupon"] = int(d.get("coupon", u["coupon"]) or 0)
                    u["jade"] = int(d.get("jade", u["jade"]) or 0)
                    u["break_even"] = int(d.get("break_even", 0) or 0)
                    u["pool"] = str(d.get("pool", "") or "")
                    u["stats"] = d.get("stats", u["stats"])
                    u["box"] = d.get("box", u["box"])
                    u["total"] = int(d.get("total", 0) or 0)
                migrated = True
            if os.path.exists(LEGACY_SCORE_FILE):
                with open(LEGACY_SCORE_FILE, encoding="utf-8") as f:
                    old = json.load(f)
                for uid, score in (old or {}).items():
                    u = self._user_by_uid(str(uid))
                    u["guess_score"] = u.get("guess_score", 0) + int(score or 0)
                migrated = True
        except (OSError, ValueError) as e:
            logger.warning(f"{LOG_TAG} 旧数据迁移失败: {e}")
            return
        if migrated:
            self._save_users()
            logger.info(f"{LOG_TAG} 已迁移旧抽卡/猜干员插件数据")

    # ------------------------------------------------------------------
    # 数据下载 / 自动更新
    # ------------------------------------------------------------------
    def _start_download_if_needed(self) -> None:
        auto_download = bool(self._cfg("gacha_auto_download", True))
        auto_update = bool(self._cfg("gacha_auto_update", True)) or bool(
            self._cfg("guess_auto_update", True),
        )
        if not auto_download and not auto_update:
            return
        if not getattr(self, "_dl_task", None) or self._dl_task.done():
            self._dl_task = asyncio.create_task(self._update_loop())

    async def _update_loop(self) -> None:
        """后台数据维护：启动时立即检查一次，之后按配置间隔增量更新。"""
        intervals = []
        if bool(self._cfg("gacha_auto_update", True)):
            intervals.append(int(self._cfg("gacha_update_interval_hours", 24) or 24) * 3600)
        if bool(self._cfg("guess_auto_update", True)):
            intervals.append(int(self._cfg("guess_update_interval_hours", 24) or 24) * 3600)
        interval = max(3600, min(intervals)) if intervals else 24 * 3600
        while True:
            try:
                ok_pools = await asyncio.to_thread(download_pools_sync)
                ok = await asyncio.to_thread(download_excel_sync)
                if (ok or ok_pools) and (self.data.using_builtin or self._data_changed_since_load()):
                    self.data.load()
                    logger.info(f"{LOG_TAG} 数据已更新并重新加载")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"{LOG_TAG} 数据更新任务异常: {e}")
            await asyncio.sleep(interval)

    def _data_changed_since_load(self) -> bool:
        paths = [
            os.path.join(EXCEL_DIR, "character_table.json"),
            POOLS_CACHE,
        ]
        stamp = getattr(self, "_loaded_stamp", 0)
        current = 0
        for path in paths:
            try:
                current = max(current, os.path.getmtime(path))
            except OSError:
                continue
        if current <= 0:
            return False
        self._loaded_stamp = current
        return current > stamp

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def _cfg(self, key: str, default=None):
        value = self.config.get(key, default)
        return default if value is None else value

    def _star(self, rarity: int) -> str:
        return "★" * rarity

    def _send(self, event: AstrMessageEvent, text: str):
        event.stop_event()
        return event.make_result().message(text)

    def _pool_name(self, pool: dict | None) -> str:
        return (pool or {}).get("name") or "默认池"

    def _user_pool(self, user: dict) -> dict | None:
        if not self.data.pools:
            return None
        for p in self.data.pools:
            if str(p.get("id", "")) == str(user.get("pool", "")):
                return p
        return self.data.pools[0]

    def _pool_with_override(self, pool: dict | None) -> dict | None:
        """把 UP 覆盖合并到卡池：管理后台按池覆盖优先，全局配置兜底。"""
        if not pool:
            return pool
        out = dict(pool)
        ov = (self._pool_overrides or {}).get(str(pool.get("id", ""))) or {}
        pickup6 = str(ov.get("pickup_6") or self._cfg("gacha_pickup_6", "") or "").strip()
        pickup5 = str(ov.get("pickup_5") or self._cfg("gacha_pickup_5", "") or "").strip()
        if pickup6:
            out["pickup_6"] = pickup6
        if pickup5:
            out["pickup_5"] = pickup5
        return out

    def _load_pool_overrides(self) -> None:
        try:
            with open(POOL_OVERRIDE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            self._pool_overrides = {str(k): v for k, v in (data or {}).items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self._pool_overrides = {}

    def _save_pool_overrides(self) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = POOL_OVERRIDE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._pool_overrides, f, ensure_ascii=False, indent=2)
        os.replace(tmp, POOL_OVERRIDE_FILE)

    def _admin_set_pool_override(self, pool_id: str, pickup_6: str, pickup_5: str) -> str | None:
        with _user_lock:
            if str(pickup_6).strip() or str(pickup_5).strip():
                self._pool_overrides[str(pool_id)] = {
                    "pickup_6": str(pickup_6).strip(),
                    "pickup_5": str(pickup_5).strip(),
                }
            else:
                self._pool_overrides.pop(str(pool_id), None)
            self._save_pool_overrides()
        return None

    # ------------------------------------------------------------------
    # 签到
    # ------------------------------------------------------------------
    @filter.command("签到")
    async def cmd_signin(self, event: AstrMessageEvent):
        if not bool(self._cfg("sign_enabled", True)):
            yield self._send(event, "签到功能已在配置里关闭啦（sign_enabled=false）。")
            return
        uid = str(event.get_sender_id() or "")
        today = time.strftime("%Y-%m-%d")
        result: dict = {"ok": False}

        def fn(user: dict) -> None:
            if user.get("sign_date") == today:
                return
            coupon = int(self._cfg("sign_coupon", 50) or 0)
            jade = int(self._cfg("sign_jade", 0) or 0)
            user["coupon"] = int(user.get("coupon", 0)) + coupon
            gained = self._add_jade(user, jade)
            user["sign_date"] = today
            user["sign_days"] = int(user.get("sign_days", 0)) + 1
            user["feeling"] = int(user.get("feeling", 0) or 0) + int(self._cfg("sign_feeling", 50) or 0)
            user["mood"] = 15
            result.update(ok=True, coupon=coupon, jade=gained)

        user = self._with_user(uid, fn)
        if not result["ok"]:
            text = "博士今天已经签到了哦，明天再来吧~ (>ω<)"
        else:
            title = str(self._cfg("fun_user_title", "博士"))
            parts = [f"签到成功！{result['coupon']} 张寻访凭证已经送到{title}的办公室啦，请{title}注意查收哦~"]
            if result["jade"] > 0:
                parts.append(f"另有 {result['jade']} 合成玉入库（当前 {user['jade']}）")
            parts.append(f"当前寻访凭证：{user['coupon']} 张")
            text = "\n".join(parts)
        reply = event.make_result()
        if bool(self._cfg("sign_card_enabled", True)):
            card = await render_mod.render_user_card(
                uid,
                str(event.get_sender_name() or ""),
                user,
                AVATAR_CACHE,
                str(self._cfg("gacha_image_font", "") or ""),
                bool(self._cfg("gacha_fetch_avatar", True)),
                int(self._cfg("jade_max", 30000) or 30000),
                operators=self.data.operators,
            )
            if card:
                reply.file_image(card)
        reply.message(text)
        event.stop_event()
        yield reply

    @filter.command("我的信息")
    @filter.command("个人信息")
    async def cmd_user_info(self, event: AstrMessageEvent):
        """复刻 Amiya user_info：用户信息卡片图（签到/信赖/心情/合成玉/抽卡统计）。"""
        uid = str(event.get_sender_id() or "")
        user = self._user(event)
        card = await render_mod.render_user_card(
            uid,
            str(event.get_sender_name() or ""),
            user,
            AVATAR_CACHE,
            str(self._cfg("gacha_image_font", "") or ""),
            bool(self._cfg("gacha_fetch_avatar", True)),
            int(self._cfg("jade_max", 30000) or 30000),
            operators=self.data.operators,
        )
        reply = event.make_result()
        if card:
            reply.file_image(card)
        else:
            reply.message("博士的档案生成失败啦，请稍后再试。")
        event.stop_event()
        yield reply

    # ------------------------------------------------------------------
    # 抽卡
    # ------------------------------------------------------------------
    @filter.regex(PULL_RE.pattern)
    async def cmd_pull_text(self, event: AstrMessageEvent):
        text = event.get_message_str().strip()
        count = parse_pull_count(text)
        if count is None:
            return
        async for r in self._gacha(event, count):
            yield r

    @filter.regex(_RESOURCE_RE)
    async def cmd_resource_phrase(self, event: AstrMessageEvent):
        text = event.get_message_str().strip()
        if parse_pull_count(text) is not None:
            return  # 抽卡类消息交给抽卡处理器
        yield self._send(event, self._resources_text(event))

    @filter.command("寻访凭证")
    @filter.command("我的资源")
    async def cmd_resource(self, event: AstrMessageEvent):
        yield self._send(event, self._resources_text(event))

    def _resources_text(self, event: AstrMessageEvent) -> str:
        user = self._user(event)
        stats = user.get("stats") or {}
        box = user.get("box") or {}
        rates = GachaEngine(self.data, user).six_rate_next()
        lines = [
            f"寻访凭证：{user.get('coupon', 0)} 张",
            f"合成玉：{user.get('jade', 0)}",
            f"累计抽数：{user.get('total', 0)}",
            f"干员分布：六星{stats.get('6', 0)}・五星{stats.get('5', 0)}・四星{stats.get('4', 0)}・三星{stats.get('3', 0)}",
            f"干员箱：{len(box)} 种（共 {sum(box.values())} 位）",
            f"保底水位：{user.get('break_even', 0)} 抽未出 6★（下次概率 {rates}%）",
        ]
        return "\n".join(lines)

    @filter.command("保底")
    async def cmd_pity(self, event: AstrMessageEvent):
        user = self._user(event)
        break_even_rate = 98
        if int(user.get("break_even", 0)) > 50:
            break_even_rate -= (int(user.get("break_even", 0)) - 50) * 2
        text = (
            f"当前已经抽取了 {user.get('break_even', 0)} 次而未获得六星干员\n"
            f"下次抽出六星干员的概率为 {100 - break_even_rate}%"
        )
        yield self._send(event, text)

    @filter.command("卡池")
    async def cmd_pools(self, event: AstrMessageEvent):
        if not self.data.pools:
            yield self._send(event, "暂无卡池数据，请稍后重试（数据下载中）。")
            return
        lines = ["博士，当前可用卡池："]
        for i, p in enumerate(self.data.pools, 1):
            name = p.get("name") or "未知池"
            if int(p.get("limit_pool") or 0):
                name = f"（限定）{name}"
            up6 = (p.get("pickup_6") or "").replace(",", "、")
            up5 = (p.get("pickup_5") or "").replace(",", "、")
            line = f"[{i}] {name}"
            if up6:
                line += f"\n    ★6 UP：{up6}"
            if up5:
                line += f"\n    ★5 UP：{up5}"
            if not up6 and not up5:
                line += "\n    （无 UP 数据，可用管理后台/配置指定）"
            lines.append(line)
        lines.append("\n回复「卡池切换 N」（或「卡池切换 池名」）切换卡池。")
        yield self._send(event, "\n".join(lines))

    @filter.command("卡池切换")
    async def cmd_pool_switch(self, event: AstrMessageEvent, index: int = 1):
        uid = str(event.get_sender_id() or "")
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = 0
        idx = index - 1 if index > 0 else -1

        if idx < 0 or idx >= len(self.data.pools):
            # 支持按池名匹配（如「卡池切换 常驻寻访」）
            text = str(getattr(event, "message_str", "") or "")
            name_hit = None
            for p in self.data.pools:
                pname = str(p.get("name") or "")
                if pname and pname in text:
                    name_hit = p
                    break
            pool = name_hit
            if pool is None:
                yield self._send(event, "博士，卡池序号不正确哦，用「卡池」看下列表吧~（可回复完整池名）")
                return
        else:
            pool = self.data.pools[idx]

        def fn(user: dict) -> None:
            user["pool"] = str(pool.get("id", ""))

        self._with_user(uid, fn)

        name = self._pool_name(pool)
        if int(pool.get("limit_pool") or 0):
            name = f"（限定）{name}"
        lines = [f"博士的卡池已切换为：【{name}】"]
        desc = str(pool.get("desc") or "").strip()
        if desc and ("," not in desc):
            lines.append(desc)
        else:
            up6 = (pool.get("pickup_6") or "").replace(",", "、")
            up5 = (pool.get("pickup_5") or "").replace(",", "、")
            if up6:
                lines.append(f"★6 UP：{up6}")
            if up5:
                lines.append(f"★5 UP：{up5}")
            if not up6 and not up5:
                lines.append("（该池无 UP 名单，可用管理后台/配置指定）")
        text = "\n".join(lines)

        img = str(pool.get("pool_image") or "").strip()
        if img and bool(self._cfg("gacha_render_image", True)):
            try:
                result = event.make_result()
                result.url_image(img)
                event.stop_event()
                yield result
                return
            except Exception:  # noqa: BLE001
                pass  # 网络图发送失败则降级纯文本
        yield self._send(event, text)

    @filter.command("box")
    @filter.command("我的干员")
    async def cmd_box(self, event: AstrMessageEvent):
        user = self._user(event)
        box = user.get("box") or {}
        if not box:
            yield self._send(event, "博士，您尚未获得任何干员")
            return
        img = None
        if bool(self._cfg("gacha_render_image", True)):
            img = await asyncio.to_thread(
                render_mod.render_box,
                self.data,
                box,
                AVATAR_CACHE,
                str(self._cfg("gacha_image_font", "") or ""),
            )
        result = event.make_result()
        if img:
            result.file_image(img)
            event.stop_event()
            yield result
            return
        # 图片渲染不可用时降级为文本列表
        lines = [f"博士的干员箱（共 {sum(box.values())} 位）："]
        groups = {3: [], 4: [], 5: [], 6: []}
        for name, cnt in box.items():
            groups[self._rarity_of(name)].append(f"{name}×{cnt}")
        for r in (6, 5, 4, 3):
            if groups[r]:
                lines.extend([f"{self._star(r)}：" + "、".join(groups[r])])
        yield self._send(event, "\n".join(lines))

    def _rarity_of(self, name: str) -> int:
        o = self.data.operators.get(name)
        return int(o.get("rarity", 4)) if o else 4

    # ------------------------------------------------------------------
    # 抽卡主流程
    # ------------------------------------------------------------------
    async def _gacha(self, event: AstrMessageEvent, times: int):
        if not bool(self._cfg("gacha_enabled", True)):
            event.stop_event()
            yield event.make_result().message("抽卡功能已在插件配置中关闭（gacha_enabled=false）。")
            return
        if times <= 0:
            return
        if times > 300:
            yield self._send(
                event,
                "博士不要着急，罗德岛的资源要好好规划使用哦，先试试 300 次以内的寻访吧 (#^.^#)",
            )
            return
        uid = str(event.get_sender_id() or "")
        outcome: dict = {}

        def fn(user: dict) -> None:
            free = bool(self._cfg("gacha_free", False))
            per = max(1, int(self._cfg("jade_per_pull", JADE_PER_PULL) or JADE_PER_PULL))
            coupon = int(user.get("coupon", 0))
            jade = int(user.get("jade", 0))
            spent_coupon = 0
            spent_jade = 0
            if not free:
                if times > coupon:
                    point_need = (times - coupon) * per
                    if jade < point_need:
                        outcome["error"] = (
                            f"博士，您的寻访资源不够哦~\n寻访凭证剩余{coupon}张\n合成玉剩余{jade}"
                        )
                        return
                    outcome["notify_jade"] = point_need
                    spent_coupon = coupon
                    spent_jade = point_need
                else:
                    spent_coupon = times
                user["coupon"] = coupon - spent_coupon
                user["jade"] = jade - spent_jade
            engine = GachaEngine(
                self.data,
                user,
                pool=self._pool_with_override(self._user_pool(user)),
            )
            results = engine.roll(times, guarantee5=bool(self._cfg("gacha_guarantee5", True)))
            user["break_even"] = engine.break_even
            stats = user.setdefault("stats", {})
            box = user.setdefault("box", {})
            for r in results:
                stats[str(r["rarity"])] = stats.get(str(r["rarity"]), 0) + 1
                box[r["name"]] = box.get(r["name"], 0) + 1
            user["total"] = int(user.get("total", 0)) + times
            outcome.update(
                engine=engine,
                results=results,
                user=dict(user),
                spent_coupon=spent_coupon,
                spent_jade=spent_jade,
            )

        self._with_user(uid, fn)
        if "error" in outcome:
            yield self._send(event, outcome["error"])
            return
        if outcome.get("notify_jade"):
            await event.send(f"寻访凭证剩余{outcome['spent_coupon']}张，将消耗{outcome['notify_jade']}合成玉")
        engine: GachaEngine = outcome["engine"]
        results: list[dict] = outcome["results"]
        user = outcome["user"]
        pool = self._pool_name(engine.pool)
        show_names = bool(self._cfg("gacha_show_pull_names", False))
        text = ""
        img = None
        if times <= 10:
            if times == 10:
                # Amiya 十连：图 + 【池名】(+名字列表) + 保底信息
                text = f"【{pool}】\n"
                if show_names:
                    text += "".join(f"【{r['name']}】" for r in results) + "\n"
                text += self._check_break_even(user)
                if bool(self._cfg("gacha_render_image", True)):
                    img = await asyncio.to_thread(
                        render_mod.render_pulls,
                        self.data,
                        results,
                        pool,
                        AVATAR_CACHE,
                        bool(self._cfg("gacha_fetch_avatar", True)),
                        str(self._cfg("gacha_image_font", "") or ""),
                    )
            else:
                # Amiya detailed_mode（1~9 抽）：文字转图片（text_image：浅灰底 + 头像图标 + 彩色星级）
                if bool(self._cfg("gacha_render_image", True)):
                    img = await asyncio.to_thread(
                        render_mod.render_detailed,
                        self.data,
                        results,
                        times,
                        pool,
                        self._check_break_even(user),
                        AVATAR_CACHE,
                        bool(self._cfg("gacha_fetch_avatar", True)),
                        str(self._cfg("gacha_image_font", "") or ""),
                    )
                if not img:
                    # 图片渲染失败时回退文本
                    text = render_mod.build_detailed_text(
                        results, times, pool, self._check_break_even(user), colored=False
                    )
        else:
            # Amiya continuous_mode（>10 抽）：统计文本转图（text_image）
            if bool(self._cfg("gacha_render_image", True)):
                img = await asyncio.to_thread(
                    render_mod.render_text_image,
                    self._format_continuous(results, times, pool, colored=True)
                    + "\n"
                    + self._check_break_even(user),
                    AVATAR_CACHE,
                    str(self._cfg("gacha_image_font", "") or ""),
                )
            if not img:
                text = self._format_continuous(results, times, pool) + "\n" + self._check_break_even(user)
        result = event.make_result()
        if img and times != 10:
            # 与原版一致：1~9 抽 / >10 抽只发文字图，内容与保底信息已在图内
            result.file_image(img)
        else:
            if img:
                result.file_image(img)
            result.message(text)
        event.stop_event()
        yield result

    def _check_break_even(self, user: dict) -> str:
        """复刻 Amiya GachaBuilder.check_break_even。"""
        break_even = int(user.get("break_even", 0))
        break_even_rate = 98
        if break_even > 50:
            break_even_rate -= (break_even - 50) * 2
        return (
            f"当前已经抽取了 {break_even} 次而未获得六星干员\n"
            f"下次抽出六星干员的概率为 {100 - break_even_rate}%\n"
            f"剩余寻访凭证 {user.get('coupon', 0)}"
        )

    def _format_continuous(self, results: list[dict], times: int, pool: str, colored: bool = False) -> str:
        """复刻 Amiya GachaBuilder.continuous_mode（>10 抽的统计文本）。"""
        rarity_sum = [0, 0, 0, 0]
        high_star: dict[int, dict[str, int]] = {5: {}, 6: {}}
        ten_gacha: list[int] = []
        purple_pack = 0
        multiple_rainbow: dict[int, int] = {}
        result = f"阿米娅给博士扔来了{times}张简历，博士细细地检阅着...\n\n【{pool}】\n"
        for item in results:
            rarity = item["rarity"]
            name = item["name"]
            rarity_sum[rarity - 3] += 1
            if rarity >= 5:
                high_star[rarity][name] = high_star[rarity].get(name, 0) + 1
            ten_gacha.append(rarity)
            if len(ten_gacha) >= 10:
                five = ten_gacha.count(5)
                six = ten_gacha.count(6)
                if five == 0 and six == 0:
                    purple_pack += 1
                if six > 1:
                    multiple_rainbow[six] = multiple_rainbow.get(six, 0) + 1
                ten_gacha = []
        for r in high_star:  # Amiya 顺序：5★ 组在前
            sd = high_star[r]
            if sd:
                star_line = self._star(r)
                if colored:
                    star_line = f"[cl {star_line}@#{render_mod.RARITY_COLOR.get(r, '7F7F7F')} cle]"
                result += f"\n{star_line}\n"
                operator_num: dict[int, list[str]] = {}
                for i in sorted(sd, key=sd.get, reverse=True):
                    num = sd[i]
                    operator_num.setdefault(num, []).append(i)
                for num in operator_num:
                    result += "、".join(operator_num[num]) + f" X {num}\n"
        if rarity_sum[2] == 0 and rarity_sum[3] == 0:
            result += "\n然而并没有高星干员..."
        result += (
            f"\n三星：{_insert_empty(rarity_sum[0], 4)}四星：{rarity_sum[1]}\n"
            f"五星：{_insert_empty(rarity_sum[2], 4)}六星：{rarity_sum[3]}\n"
        )
        enter = True
        if purple_pack > 0:
            result += "\n"
            enter = False
            result += f"出现了 {purple_pack} 次十连紫气东来\n"
        for num in multiple_rainbow:
            if enter:
                result += "\n"
                enter = False
            result += f"出现了 {multiple_rainbow[num]} 次十连内 {num} 个六星\n"
        return result

    # ------------------------------------------------------------------
    # 猜干员
    # ------------------------------------------------------------------
    @filter.command("猜干员")
    async def cmd_guess(self, event: AstrMessageEvent, difficulty: str = ""):
        if not bool(self._cfg("guess_enabled", True)):
            event.stop_event()
            yield event.make_result().message("猜干员已通过配置关闭（guess_enabled=false）。")
            return
        level = (difficulty or "").strip()
        if level == "排行":
            yield self._send(event, self._guess_ranking_text())
            return
        if not level or level not in DIFFICULTIES:
            event.stop_event()
            yield event.make_result().message(
                "博士，欢迎来猜干员！玩法：\n"
                "· 「猜干员 初级」基础线索｜「中级」技能名｜「高级」语音台词｜「资深」档案\n"
                "· 全员可参与，直接发送干员名作答；「提示」要线索、「跳过」公布答案、「结束竞猜」终止\n"
                "· 答对赚合成玉（连击加成），每局前三名还有排名奖励，可查「猜干员排行」\n"
                "回复「猜干员 难度」开局，游戏期间本群其他功能不受影响。",
            )
            return
        gid = str(event.get_group_id() or "")
        if not gid:
            event.stop_event()
            yield event.make_result().message("猜干员是群内游戏哦，请到群里 @兔兔 发起。")
            return
        if gid in _SESSIONS and _SESSIONS[gid]["phase"] == "playing":
            event.stop_event()
            yield event.make_result().message("本群已经有一局猜干员在进行啦，猜完这局再开新的吧~（可发送「结束竞猜」终止）")
            return
        sess = self.guess.new_session(
            gid,
            level,
            int(self._cfg("guess_questions", 5) or 5),
            int(self._cfg("guess_timeout_sec", 60) or 60),
        )
        _SESSIONS[gid] = sess
        rank_jades = (
            int(self._cfg("guess_jade_rank1", 3000) or 0),
            int(self._cfg("guess_jade_rank2", 2000) or 0),
            int(self._cfg("guess_jade_rank3", 1000) or 0),
        )
        await self.guess.play(
            sess,
            lambda text: event.send(text),
            self._grant_jade,
            self._add_guess_points,
            int(self._cfg("guess_points_bingo", 100) or 100),
            int(self._cfg("guess_jade_bingo", 300) or 300),
            rank_jades,
        )
        event.stop_event()
        yield event.make_result().message(sess["summary"])

    @filter.command("猜干员排行")
    async def cmd_guess_rank(self, event: AstrMessageEvent):
        yield self._send(event, self._guess_ranking_text())

    def _guess_ranking_text(self) -> str:
        top = sorted(
            ((uid, d.get("guess_score", 0)) for uid, d in self._users.items() if d.get("guess_score", 0) > 0),
            key=lambda kv: -kv[1],
        )[:10]
        if not top:
            return "赛季积分榜还空着呢，快来「猜干员 初级」开局吧~"
        lines = ["🏆 猜干员赛季积分榜 TOP10："]
        medals = ["🥇", "🥈", "🥉"]
        for i, (uid, score) in enumerate(top):
            flag = medals[i] if i < 3 else f"{i + 1}."
            lines.append(f"{flag} ID {uid}：{score} 分")
        return "\n".join(lines)

    @filter.command("猜干员数据")
    async def cmd_guess_data(self, event: AstrMessageEvent):
        last = self._last_update_text()
        lines = [
            f"gamedata excel：{'已就绪' if excel_ready() else '未就绪（后台下载中/内置兜底）'}",
            f"干员数据：{len(self.data.operators)} 名（{'真实全量' if not self.data.using_builtin else '内置兜底'}）",
            f"语音台词：{sum(len(v) for v in self.data.voices.values())} 条",
            f"档案片段：{sum(len(v) for v in self.data.stories.values())} 条",
            f"上次更新：{last}",
            f"自动更新：{'开' if bool(self._cfg('guess_auto_update', True)) else '关'}（间隔 {self._cfg('guess_update_interval_hours', 24)} 小时）",
        ]
        yield self._send(event, "\n".join(lines))

    @filter.command("数据状态")
    async def cmd_data_status(self, event: AstrMessageEvent):
        last = self._last_update_text()
        lines = [
            f"gamedata excel：{'已就绪（' + str(len(os.listdir(EXCEL_DIR))) + ' 个文件）' if excel_ready() else '未就绪（后台下载中/内置数据兜底）'}",
            f"干员数据：{len(self.data.operators)} 名（{'真实全量' if not self.data.using_builtin else '内置兜底'}）",
            f"卡池：{len(self.data.pools)} 个，语音：{sum(len(v) for v in self.data.voices.values())} 条",
            f"上次更新：{last}",
            f"自动更新：{'开' if bool(self._cfg('gacha_auto_update', True)) else '关'}（间隔 {self._cfg('gacha_update_interval_hours', 24)} 小时）",
        ]
        yield self._send(event, "\n".join(lines))

    def _last_update_text(self) -> str:
        path = os.path.join(EXCEL_DIR, ".excel_last_update")
        try:
            with open(path, encoding="utf-8") as f:  # noqa: ASYNC230
                return f.read().strip() or "未知"
        except OSError:
            return "未知"

    # ------------------------------------------------------------------
    # 游戏进行中的高优先级处理（猜干员作答）
    # ------------------------------------------------------------------
    @filter.custom_filter(GameActiveFilter, priority=95)
    async def gate_game(self, event: AstrMessageEvent):
        """游戏进行中（对齐 Amiya game-guess）：作答/提示/跳过/结束无需 @ 直接触发；
        本群其他消息一律静默——暂停其他功能与 LLM 响应。"""
        gid = str(event.get_group_id() or "")
        sess = _SESSIONS.get(gid)
        if sess and sess["phase"] == "playing":
            await self.guess.handle_msg(
                event,
                sess,
                int(self._cfg("guess_points_bingo", 100) or 100),
                int(self._cfg("guess_jade_bingo", 300) or 300),
            )
        event.stop_event()

    # ------------------------------------------------------------------
    # 管理后台（AstrBot WebUI 插件页面）
    # ------------------------------------------------------------------
    def _register_admin_web(self, context) -> None:
        try:
            self._web_admin = ArknightsFunWebAdmin(self)
            self._web_admin.register_routes(context)
            logger.info(f"{LOG_TAG} 管理后台页面已注册到 AstrBot WebUI")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{LOG_TAG} 管理后台注册失败: {e}")

    def _admin_adjust_user(self, uid: str, coupon_delta: int, jade_delta: int) -> str | None:
        """管理后台调整用户资源；返回错误文案（None 表示成功）。"""
        if not uid:
            return "缺少用户 ID"
        if coupon_delta == 0 and jade_delta == 0:
            return "调整值不能为 0"
        with _user_lock:
            user = self._users.get(uid)
            if user is None:
                return "用户不存在"
            user["coupon"] = max(0, int(user.get("coupon", 0)) + int(coupon_delta))
            user["jade"] = max(0, int(user.get("jade", 0)) + int(jade_delta))
            self._save_users()
        return None

    def _admin_reset_pity(self, uid: str) -> str | None:
        if not uid:
            return "缺少用户 ID"
        with _user_lock:
            user = self._users.get(uid)
            if user is None:
                return "用户不存在"
            user["break_even"] = 0
            self._save_users()
        return None

    def _admin_delete_user(self, uid: str) -> str | None:
        if not uid:
            return "缺少用户 ID"
        with _user_lock:
            if uid not in self._users:
                return "用户不存在"
            del self._users[uid]
            self._save_users()
        return None

    async def terminate(self):
        """卸载时清理游戏会话与后台任务。"""
        _SESSIONS.clear()
