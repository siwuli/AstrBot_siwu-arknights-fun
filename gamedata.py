"""共享 gamedata 模块（下载器 + GameData 干员/卡池/语音/档案数据）。

抽卡与猜干员共用同一份数据实例，数据访问约定与生态内其他方舟插件一致：
下载/读取 AstrBot 公共数据目录 {data}/resource/gamedata；本插件自带独立下载器，
谁先下载谁用、只读不依赖，避免重复下载与跨插件耦合。

增量判据（本插件的自足更新逻辑，不依赖其它插件）：
    远端 `excel/<name>.json` 是否需要更新，用 **git blob sha 比内容**——GitHub contents
    API 返回的 sha 与本地文件按 `sha1("blob <size>\\0" + 内容)` 算出的值同口径。
    不用"比较字节数"：像 data_version.txt 这种版本标记文件，v076→v078 内容变了但长度
    一样（都是 85 字节），按大小比较会永远判定"无需更新"。
"""

import contextlib
import hashlib
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

# 跨插件数据更新锁：与 arknights-query / gacha / guess-operator 约定同一路径。
# 放在 GAMEDATA_ROOT 下而不是 excel/ 内——arknights-query 解压 zip 时会整体替换
# gamedata/ 目录，锁放在里面会被一起删掉，互斥就失效了。
UPDATE_LOCK_DIR = os.path.join(GAMEDATA_ROOT, ".gamedata.update.lock")

# excel 文件镜像（raw.githubusercontent 在部分网络/机房不可达，按顺序尝试，首个成功即用）
EXCEL_MIRRORS = (
    "https://cdn.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/excel/{name}",
    "https://ghproxy.net/https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/{name}",
    "https://gh-proxy.com/https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/{name}",
    "https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/{name}",
)
# 远端文件清单（含 git blob sha，用于内容级增量判断）；纯 API，实测在受限网络下也可达
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
    "handbook_info_table.json",
]

# 内置资产（任何环境开箱即用的兜底数据）
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
BUILTIN_OPERATORS = os.path.join(ASSETS_DIR, "operators.json")
BUILTIN_POOLS = os.path.join(ASSETS_DIR, "pools.json")

# 卡池官方数据源：阿米娅官方 gacha API（与原版 amiyabot-arknights-gacha 完全相同），
# 含真实池名/UP 名单/池子图（101 个池：常驻、中坚、往期限定等），启动与定时任务自动同步。
AMIYA_POOL_API = "https://cdn.amiyabot.com/api/v1/gacha"
POOLS_CACHE = os.path.join(GAMEDATA_ROOT, "pools_remote.json")

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
    try:
        os.makedirs(UPDATE_LOCK_DIR, exist_ok=False)
        return True
    except FileExistsError:
        return False
    except OSError as e:  # 权限/路径异常时不阻塞更新
        logger.warning(f"{LOG_TAG} 创建数据更新锁失败，跳过互斥: {e}")
        return True


def _release_update_lock() -> None:
    with contextlib.suppress(OSError):
        os.rmdir(UPDATE_LOCK_DIR)


def excel_ready() -> bool:
    """核心表就绪即视为数据可用（全量表由后台任务继续补全）。"""
    path = os.path.join(EXCEL_DIR, "character_table.json")
    return os.path.exists(path) and os.path.getsize(path) > 1024 * 1024


def _git_blob_sha(path: str) -> str:
    """计算文件的 git blob sha（与 GitHub contents API 返回的 sha 同口径）。"""
    size = os.path.getsize(path)
    digest = hashlib.sha1()
    digest.update(f"blob {size}\0".encode("ascii"))
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _excel_remote() -> list[tuple[str, str, int]]:
    """远端 excel 目录清单 (name, sha, size)；失败回退核心表（sha 空 / size 0 表示未知）。"""
    try:
        data = _http_get(GITHUB_EXCEL_API, timeout=30)
        items = json.loads(data.decode("utf-8"))
        result = [
            (str(it.get("name", "")), str(it.get("sha", "")), int(it.get("size", 0) or 0))
            for it in items
            if isinstance(it, dict) and str(it.get("name", "")).endswith(".json")
        ]
        if result:
            return sorted(result, key=lambda item: item[0])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"{LOG_TAG} 文件列表获取失败，使用核心表兜底: {e}")
    return [(name, "", 0) for name in REQUIRED_EXCEL]


def _needs_download(target: str, sha: str, rsize: int) -> bool:
    """本地文件是否需要（重新）下载。

    判据优先级：远端 git blob sha（内容级，最可靠）> 远端字节数 > 远端信息未知则不动。
    """
    if not os.path.exists(target) or os.path.getsize(target) <= 1024:
        return True
    if sha:
        try:
            return _git_blob_sha(target) != sha
        except OSError as e:
            logger.warning(f"{LOG_TAG} 校验 {os.path.basename(target)} 失败: {e}")
            return True
    if rsize > 0:
        return os.path.getsize(target) != rsize
    return False


_mirror_index = 0  # 记住上次成功的镜像，后续文件优先走它，避免每个文件都重试坏源


def download_excel_file(name: str, timeout: int = 120) -> bytes:
    """按镜像顺序下载单个 excel 文件；全部失败时抛出最后一个异常。"""
    global _mirror_index
    order = list(range(len(EXCEL_MIRRORS)))
    if _mirror_index < len(EXCEL_MIRRORS):
        order = [_mirror_index, *(i for i in order if i != _mirror_index)]
    last_error: Exception | None = None
    for index in order:
        url = EXCEL_MIRRORS[index].format(name=name)
        try:
            data = _http_get(url, timeout=timeout)
            if len(data) < 1024:
                raise RuntimeError(f"内容异常({len(data)}B)")
            if index != _mirror_index:
                logger.info(f"{LOG_TAG} 切换数据镜像: {url.split('/')[2]}")
                _mirror_index = index
            return data
        except Exception as e:  # noqa: BLE001
            last_error = e
            logger.debug(f"{LOG_TAG} 镜像不可用 {url.split('/')[2]}: {e}")
    raise last_error or RuntimeError(f"{name} 所有镜像均不可用")


def download_excel_sync() -> bool:
    """全量/增量下载 excel JSON 到公共目录（模块锁 + 跨插件目录锁）。

    缺失、或**内容**与远端不一致的文件才下载（git blob sha 比对）；全部一致时为空跑。
    """
    with _download_lock:
        if not _acquire_update_lock():
            return excel_ready()  # 已有其他插件在更新，本轮跳过
        try:
            os.makedirs(EXCEL_DIR, exist_ok=True)
            remote = _excel_remote()
            stale = [
                item
                for item in remote
                if _needs_download(os.path.join(EXCEL_DIR, item[0]), item[1], item[2])
            ]
            if stale:
                logger.info(f"{LOG_TAG} 需更新 {len(stale)}/{len(remote)} 个数据表")
            ok = True
            changed = 0
            for index, (name, _sha, _rsize) in enumerate(stale, 1):
                target = os.path.join(EXCEL_DIR, name)
                tmp = target + ".tmp"
                try:
                    logger.info(f"{LOG_TAG} 更新数据 [{index}/{len(stale)}]: {name} ...")
                    data = download_excel_file(name)
                    with open(tmp, "wb") as f:
                        f.write(data)
                    os.replace(tmp, target)
                    changed += 1
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
                logger.info(f"{LOG_TAG} excel 数据更新完成（{changed} 个文件）")
            return ok and excel_ready()
        finally:
            _release_update_lock()


def _parse_remote_pool(p: dict) -> dict | None:
    """阿米娅官方 Pool 记录 → 插件池模型（字段对齐原版 pool_methods.copy_props）。"""
    name = str(p.get("pool_name") or "").strip()
    if not name:
        return None
    return {
        "id": str(p.get("id") or ""),
        "pool_uuid": str(p.get("pool_uuid") or ""),
        "name": name,
        "desc": str(p.get("pool_description") or "").strip(),
        "pool_image": str(p.get("pool_image") or "").strip(),
        "limit_pool": int(p.get("limit_pool") or 0),
        "is_classic_only": bool(p.get("is_classicOnly")),
        "pickup_6": str(p.get("pickup_6") or "").strip(),
        "pickup_5": str(p.get("pickup_5") or "").strip(),
        "pickup_6_rate": 0.5,
        "pickup_5_rate": 0.5,
        "remote": True,
    }


def download_pools_sync() -> bool:
    """同步阿米娅官方卡池列表到公共 gamedata 目录（原版相同数据源）。

    失败不影响其他数据；返回是否写入成功（供上层判断是否需要重载）。
    """
    with _download_lock:
        try:
            data = _http_get(AMIYA_POOL_API, timeout=60)
            payload = json.loads(data.decode("utf-8"))
            pools = ((payload or {}).get("data") or {}).get("Pool") or []
            parsed = [_parse_remote_pool(p) for p in pools]
            parsed = [p for p in parsed if p and p.get("name")]
            if not parsed:
                logger.warning(f"{LOG_TAG} 官方卡池接口返回为空，保留现有数据")
                return False
            os.makedirs(GAMEDATA_ROOT, exist_ok=True)
            tmp = POOLS_CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "source": "amiya_official",
                        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "pools": parsed,
                    },
                    f,
                    ensure_ascii=False,
                    indent=1,
                )
            os.replace(tmp, POOLS_CACHE)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{LOG_TAG} 官方卡池同步失败（保留现有卡池）: {e}")
            return False


def pools_last_update() -> str:
    """官方卡池缓存的上次更新时间（字符串或 '未知'）。"""
    try:
        with open(POOLS_CACHE, encoding="utf-8") as f:
            data = json.load(f)
        updated = str((data or {}).get("updated") or "").strip()
        if updated:
            return updated
    except (OSError, ValueError):
        pass
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(POOLS_CACHE)))
    except OSError:
        return "未知"


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
        self.pool_source = "builtin"  # amiya_official | excel | builtin
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
        """优先读公共 excel；失败回退内置资产；卡池一律官方 API 缓存优先。"""
        loaded_excel = False
        if excel_ready():
            try:
                self._load_excel()
                loaded_excel = True
                self.using_builtin = False
            except Exception as e:  # noqa: BLE001
                logger.warning(f"{LOG_TAG} excel 解析失败，回退内置数据: {e}")
        if not loaded_excel:
            self._load_builtin()
        if not self._load_pools_remote():
            # 官方缓存不存在时保留 _load_excel/_load_builtin 已加载的卡池
            pass

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
        self.pool_source = "builtin"

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
    def _load_pools_remote(self) -> bool:
        """读取阿米娅官方卡池缓存（download_pools_sync 落盘），成功返回 True。"""
        try:
            with open(POOLS_CACHE, encoding="utf-8") as f:
                data = json.load(f)
            pools = (data or {}).get("pools") or []
            if not pools or not isinstance(pools, list):
                return False
            self.pools = [p for p in pools if isinstance(p, dict) and p.get("name")]
            self.pool_source = "amiya_official" if self.pools else "builtin"
            return bool(self.pools)
        except (OSError, ValueError):
            return False

    def _load_pools_excel(self) -> None:
        """卡池加载：官方 API 缓存优先（原版数据源）→ 游戏 excel → 内置兜底。"""
        if self._load_pools_remote():
            return
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
                self.pool_source = "excel"
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
        # 干员档案在 v078 数据里是 handbook_info_table.json["handbookDict"]，
        # 结构为 {charId: {storyTextAudio: [{storyTitle, stories: [{storyText}]}]}}。
        # 旧代码读的是 handbook_table.json（该文件只剩 1 个干员、字段名也不是 storyTexts），
        # 于是猜干员的「档案提示」永远是空的。
        path = os.path.join(EXCEL_DIR, "handbook_info_table.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            skip_titles = {"基础档案", "综合体检测试", "综合性能检测结果", "临床诊断分析"}
            for cid, item in ((data or {}).get("handbookDict") or {}).items():
                if not isinstance(item, dict) or cid not in self._by_id:
                    continue
                texts = []
                for entry in item.get("storyTextAudio") or []:
                    if not isinstance(entry, dict):
                        continue
                    if str(entry.get("storyTitle") or "") in skip_titles:
                        continue
                    for story in entry.get("stories") or []:
                        text = str((story or {}).get("storyText") or "").strip()
                        if text:
                            texts.append(text)
                self.stories[cid] = texts
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{LOG_TAG} 档案表解析失败: {e}")
