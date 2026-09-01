"""共享 gamedata 模块（下载器 + GameData 干员/卡池/语音/档案数据）。

抽卡与猜干员共用同一份数据实例，数据访问约定与生态内其他方舟插件一致：
下载/读取 AstrBot 公共数据目录 {data}/resource/gamedata；本插件自带独立下载器，
谁先下载谁用、只读不依赖，避免重复下载与跨插件耦合。
"""

import contextlib
import json
import logging
import os
import re
import threading
import time
import urllib.request

from astrbot.core.utils.astrbot_path import get_astrbot_data_path

logger = logging.getLogger("astrbot")

LOG_TAG = "[arknights_fun]"

GAMEDATA_ROOT = os.path.join(get_astrbot_data_path(), "resource", "gamedata")
EXCEL_DIR = os.path.join(GAMEDATA_ROOT, "gamedata", "excel")
AVATAR_DIR = os.path.join(GAMEDATA_ROOT, "avatar")
PORTRAIT_DIR = os.path.join(GAMEDATA_ROOT, "portrait")

# 数据源（GitHub 直链，免 git、免登录）
GITHUB_RAW = "https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/{name}"
GITHUB_EXCEL_API = (
    "https://api.github.com/repos/Kengxxiao/ArknightsGameData/contents/zh_CN/gamedata/excel"
)
# 列表接口不可用时的兜底核心表
REQUIRED_EXCEL = [
    "character_table.json",
    "skill_table.json",
    "gacha_table.json",
    "skin_table.json",
    "charword_table.json",
    "handbook_table.json",
]

# 内置资产（任何环境开箱即用的兜底数据）
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
BUILTIN_OPERATORS = os.path.join(ASSETS_DIR, "operators.json")
BUILTIN_POOLS = os.path.join(ASSETS_DIR, "pools.json")

_download_lock = threading.Lock()


def _http_get(url: str, timeout: int = 60) -> bytes:
    """同步下载 URL 内容（标准库，在后台线程中调用）。"""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (AstrBot arknights_fun)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _acquire_update_lock() -> bool:
    """跨插件互斥锁：目录能否创建即是否获得锁（原子判断）。"""
    lock_dir = os.path.join(EXCEL_DIR, ".update.lock")
    try:
        os.makedirs(lock_dir, exist_ok=False)
        return True
    except FileExistsError:
        return False


def _release_update_lock() -> None:
    with contextlib.suppress(OSError):
        os.rmdir(os.path.join(EXCEL_DIR, ".update.lock"))


def excel_ready() -> bool:
    """核心表就绪即视为数据可用（全量表由后台任务继续补全）。"""
    path = os.path.join(EXCEL_DIR, "character_table.json")
    return os.path.exists(path) and os.path.getsize(path) > 1024 * 1024


def _excel_remote() -> list[tuple[str, int]]:
    """远端 excel 目录文件列表（name,size）；失败回退核心表（size=0 未知）。"""
    try:
        data = _http_get(GITHUB_EXCEL_API, timeout=30)
        items = json.loads(data.decode("utf-8"))
        result = [
            (str(it.get("name", "")), int(it.get("size", 0) or 0))
            for it in items
            if isinstance(it, dict) and str(it.get("name", "")).endswith(".json")
        ]
        if result:
            return sorted(result, key=lambda kv: kv[0])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"{LOG_TAG} 文件列表获取失败，使用核心表兜底: {e}")
    return [(name, 0) for name in REQUIRED_EXCEL]


def download_excel_sync() -> bool:
    """全量/增量下载 excel JSON 到公共目录（模块锁 + 跨插件目录锁）。

    缺失或与远端大小不一致的文件才下载（增量更新）；全部一致时为空跑。
    """
    with _download_lock:
        if not _acquire_update_lock():
            return excel_ready()  # 已有其他插件在更新，本轮跳过
        try:
            os.makedirs(EXCEL_DIR, exist_ok=True)
            remote = _excel_remote()
            ok = True
            changed = False
            for index, (name, rsize) in enumerate(remote, 1):
                target = os.path.join(EXCEL_DIR, name)
                if (
                    os.path.exists(target)
                    and os.path.getsize(target) > 1024
                    and (rsize <= 0 or os.path.getsize(target) == rsize)
                ):
                    continue  # 已有且与远端一致
                tmp = target + ".tmp"
                try:
                    logger.info(f"{LOG_TAG} 更新数据 [{index}/{len(remote)}]: {name} ...")
                    data = _http_get(GITHUB_RAW.format(name=name), timeout=120)
                    if len(data) < 1024:
                        raise RuntimeError(f"{name} 内容异常({len(data)}B)")
                    with open(tmp, "wb") as f:
                        f.write(data)
                    os.replace(tmp, target)
                    changed = True
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"{LOG_TAG} 下载 {name} 失败: {e}")
                    ok = False
                    if os.path.exists(tmp):
                        os.remove(tmp)
            if changed:
                with open(
                    os.path.join(EXCEL_DIR, ".excel_last_update"),
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
                logger.info(f"{LOG_TAG} excel 数据更新完成")
            return ok and excel_ready()
        finally:
            _release_update_lock()


class GameData:
    """干员/卡池/语音/档案数据（公共 gamedata 优先，内置资产兜底）。

    gacha 与猜干员共用同一实例，避免重复加载与重复下载。
    """

    def __init__(self) -> None:
        self.operators: dict[str, dict] = {}  # name -> {id,rarity,prof,sub,sex,team,group,nation,skills}
        self.by_rarity: dict[int, list[str]] = {3: [], 4: [], 5: [], 6: []}
        self._by_id: dict[str, str] = {}  # id -> name
        self.pools: list[dict] = []
        self.voices: dict[str, list[str]] = {}  # id -> 语音台词文本
        self.stories: dict[str, list[str]] = {}  # id -> 档案文本
        self.using_builtin = True
        self.load()

    @staticmethod
    def is_pullable(op_id: str, name: str, prof: str) -> bool:
        """是否可进入卡池：排除召唤物（token_/trap_/TOKEN 职业）与预备干员。"""
        oid = str(op_id or "")
        if oid and not oid.startswith("char_"):
            return False
        if str(prof or "").upper() == "TOKEN":
            return False
        return "预备干员" not in str(name or "")

    def load(self) -> None:
        """优先读公共 excel；失败回退内置资产。"""
        if excel_ready():
            try:
                self._load_excel()
                self.using_builtin = False
                return
            except Exception as e:  # noqa: BLE001
                logger.warning(f"{LOG_TAG} excel 解析失败，回退内置数据: {e}")
        self._load_builtin()

    # ---------------- 内置兜底 ----------------
    def _load_builtin(self) -> None:
        with open(BUILTIN_OPERATORS, encoding="utf-8") as f:
            data = json.load(f)
        for o in data.get("operators", []):
            if (
                o.get("rarity") in (3, 4, 5, 6)
                and o.get("name")
                and self.is_pullable(o.get("id") or "", o.get("name") or "", o.get("prof") or "")
            ):
                self.operators[o["name"]] = o
        self._index_rarity()
        try:
            with open(BUILTIN_POOLS, encoding="utf-8") as f:
                self.pools = json.load(f).get("pools", [])
        except (OSError, ValueError):
            self.pools = []

    # ---------------- 全量 excel ----------------
    def _load_excel(self) -> None:
        with open(os.path.join(EXCEL_DIR, "character_table.json"), encoding="utf-8") as f:
            char = json.load(f)
        skills = {}
        path = os.path.join(EXCEL_DIR, "skill_table.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                skills = json.load(f)
        self.operators = {}
        for cid, c in char.items():
            if not isinstance(c, dict) or not c.get("name"):
                continue
            try:
                rar = int(str(c.get("rarity", "0")).split("_")[-1])
            except ValueError:
                continue
            if rar not in (3, 4, 5, 6):
                continue
            if not self.is_pullable(cid, c.get("name") or "", c.get("profession") or ""):
                continue
            names = []
            for s in (c.get("skills") or []):
                sid = s.get("skillId") if isinstance(s, dict) else None
                if sid and sid in skills and isinstance(skills.get(sid), dict):
                    names.append(skills[sid].get("name", ""))
            self.operators[c["name"]] = {
                "id": cid,
                "name": c["name"],
                "rarity": rar,
                "prof": c.get("profession", ""),
                "sub": c.get("subProfessionId", ""),
                "sex": c.get("sex", ""),
                "team": c.get("teamId", ""),
                "group": c.get("groupId", ""),
                "nation": c.get("nationId", ""),
                "skills": [x for x in names if x],
            }
        self._index_rarity()
        self._load_pools_excel()
        self._load_voices()
        self._load_stories()

    def _index_rarity(self) -> None:
        self.by_rarity = {3: [], 4: [], 5: [], 6: []}
        self._by_id = {}
        for name, o in self.operators.items():
            r = o.get("rarity")
            if r in self.by_rarity:
                self.by_rarity[r].append(name)
            self._by_id[str(o.get("id") or "")] = name
        for r in self.by_rarity:
            self.by_rarity[r].sort()

    # ---------------- 卡池 ----------------
    def _load_pools_excel(self) -> None:
        """从 gacha_table.json 取当期（或最新）真实卡池。"""
        path = os.path.join(EXCEL_DIR, "gacha_table.json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            pools = (data or {}).get("gachaPoolClient") or []
            now = time.time()
            active = [p for p in pools if p.get("openTime", 0) <= now <= p.get("endTime", 0)]
            cands = active or sorted(pools, key=lambda p: p.get("endTime", 0), reverse=True)[:20]
            parsed = [self._parse_pool(p) for p in cands]
            parsed = [p for p in parsed if p and p.get("name")]
            if parsed:
                self.pools = parsed
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{LOG_TAG} 真实卡池解析失败，使用内置池: {e}")

    @staticmethod
    def _parse_pool(p: dict) -> dict | None:
        """解析 gachaPoolClient 条目：名字 + up 干员（detail 文本）→ 插件池模型。"""
        name = (p.get("gachaPoolName") or "").strip()
        if not name:
            return None
        up = GameData._parse_detail_up(p.get("gachaPoolDetail") or "")
        return {
            "id": str(p.get("gachaPoolId") or ""),
            "name": name,
            "pickup_6": ",".join(up.get(6, [])),
            "pickup_5": ",".join(up.get(5, [])),
            "pickup_6_rate": 0.5,
            "pickup_5_rate": 0.5,
            "desc": "游戏内官方卡池（自动解析出现率上升）",
        }

    @staticmethod
    def _parse_detail_up(detail: str) -> dict[int, list[str]]:
        """从卡池详情文本提取各星级 UP 干员名单（容忍解析失败）。"""
        result: dict[int, list[str]] = {6: [], 5: [], 4: [], 3: []}
        try:
            text = re.sub(r"<@[^>]*>|</>", "", detail or "")
            lines = [ln.strip() for ln in text.split("\n")]
            star: int | None = None
            collecting = False
            for ln in lines:
                if "出现率上升" in ln or ln.startswith("※出现率上升"):
                    collecting = True
                    star = None
                    continue
                if "全部可能出现的干员" in ln:
                    collecting = False
                    star = None
                    continue
                if not collecting:
                    continue
                m = re.match(r"^(★+)", ln)
                if m:
                    star = min(len(m.group(1)), 6)
                    continue
                if not ln or re.match(r"^-+$", ln):
                    star = None
                    continue
                if star in result:
                    for n in re.split(r"[/、\s]+", ln):
                        n = n.strip().strip("（）()")
                        if not n or n.startswith(("占", "（", "(")):
                            continue
                        if n not in result[star]:
                            result[star].append(n)
        except Exception as _e:  # noqa: BLE001
            logger.debug(f"{LOG_TAG} 卡池详情解析失败: {_e}")
        return result

    # ---------------- 语音 / 档案（猜干员用） ----------------
    def _load_voices(self) -> None:
        path = os.path.join(EXCEL_DIR, "charword_table.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            words = data.get("charWords") if isinstance(data, dict) else data
            if not isinstance(words, dict):
                return
            skip_titles = {"标题", "戳一下"}
            for item in words.values():
                if not isinstance(item, dict) or not item.get("voiceText"):
                    continue
                cid = str(item.get("charId") or "").strip()
                if not cid or cid not in self._by_id:
                    continue
                if str(item.get("voiceTitle") or "").strip() in skip_titles:
                    continue
                text = str(item["voiceText"]).strip()
                if 4 <= len(text) <= 80:
                    self.voices.setdefault(cid, []).append(text)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{LOG_TAG} 语音表解析失败: {e}")

    def _load_stories(self) -> None:
        path = os.path.join(EXCEL_DIR, "handbook_table.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            skip_titles = {"基础档案", "综合体检测试", "综合性能检测结果", "临床诊断分析"}
            for cid, item in (data or {}).items():
                if not isinstance(item, dict) or cid not in self._by_id:
                    continue
                texts = []
                for s in (item.get("storyTexts") or []):
                    if not isinstance(s, dict) or not s.get("storyText"):
                        continue
                    if str(s.get("storyTitle") or "") in skip_titles:
                        continue
                    texts.append(str(s["storyText"]).strip())
                self.stories[cid] = texts
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{LOG_TAG} 档案表解析失败: {e}")
