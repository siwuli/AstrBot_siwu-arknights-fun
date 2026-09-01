"""抽卡模块：标准概率 + 保底 + UP 权重 + 数字/中文数字指令解析。

参考 Amiya-Bot amiyabot-arknights-gacha 的概率与保底规则移植：
- 基础概率 3★40% / 4★50% / 5★8% / 6★2%，50 抽后每抽 +2%，99 抽必出；
- 十连保底 5★；卡池 UP 干员按 up_rate 分配权重；
- 指令支持阿拉伯数字与中文数字（一百连 / 三百抽 / 三十次寻访），1~300 次。
"""

import random
import re

from .gamedata import GameData

BASE_RATES = {6: 2, 5: 8, 4: 50, 3: 40}
PITY_START = 50  # 连续 50 抽未出 6★ 后提升
JADE_PER_PULL = 600  # 凭证不足时每抽消耗的合成玉

CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_TOKENS = "零一二两三四五六七八九十百"


def cn_to_int(s: str) -> int | None:
    """中文数字 → 阿拉伯数字（支持 1~999：三百二十 / 一百零五 / 两 等）。"""
    s = s.replace("两", "二")
    total = 0
    section = 0
    num = 0
    for ch in s:
        if ch == "零":
            num = 0
        elif ch == "十":
            section += (num or 1) * 10
            num = 0
        elif ch == "百":
            total += section + (num or 1) * 100
            section = 0
            num = 0
        elif ch in CN_DIGITS:
            num = CN_DIGITS[ch]
        else:
            return None
    return total + section + num


_CN = f"[{_CN_TOKENS}]"
# 严格抽卡触发：数量后必须紧跟「连/抽/次寻访」等抽卡词（不含裸“次”，
# 否则“第一次/第三次”这种日常用语会被误判成 1 次抽卡而触发单抽）。
# 原版 Amiya 只认阿拉伯数字，这里保留中文数字但锚定抽卡词。
PULL_RE = re.compile(
    rf"(?:单抽|十连)"
    rf"|(?:(?:\d+|{_CN}+)\s*(?:连抽|连|抽|次\s*寻访))"
    rf"|(?:(?:抽|寻访)\s*(?:\d+|{_CN}+)\s*(?:次|连)?)"
    rf"|(?:(?:抽卡|来)\s*(?:\d+|{_CN}+))",
)


def parse_pull_count(text: str) -> int | None:
    """从消息文本中提取抽卡次数；无法识别返回 None。

    支持：一百连 / 三百抽 / 三十次寻访 / 抽卡一百 / 来 100 / 单抽 / 十连。
    """
    m = PULL_RE.search(text or "")
    if not m:
        return None
    seg = m.group(0)
    if seg == "单抽":
        return 1
    if seg == "十连":
        return 10
    nm = re.search(rf"(\d+|{_CN}+)", seg)
    if not nm:
        return None
    cnt = nm.group(1)
    return int(cnt) if cnt.isdigit() else cn_to_int(cnt)


# 抽卡意图闸门：整个消息须为「引导词 + 抽卡短语 + 语气词」结构，杜绝闲聊误触。
# 抽卡短语本身仍由 PULL_RE 提取次数（filter.regex 负责命中入口）。
_LEAD_RE = re.compile(
    r"^(?:兔兔|阿米娅|echo|小兔|宝宝|机器人|bot)?[，,：:、\s]*"
    r"(?:来|给我|帮我|我想|我要|想|试试|来一发|给我来|来点)?[，,：:、\s]*",
)
_STRICT_PULL = re.compile(
    rf"^{_LEAD_RE.pattern}"
    rf"(?:(?:单抽|十连)"
    rf"|(?:(?:\d+|{_CN}+)\s*(?:连抽|连|抽|次\s*寻访))"
    rf"|(?:(?:抽|寻访)\s*(?:\d+|{_CN}+)\s*(?:次|连)?)"
    rf"|(?:(?:抽卡|来)\s*(?:\d+|{_CN}+)\s*(?:连|抽|次)?))"
    rf"[，,。！!？?\s]*(?:试试|吧|嘛|呢|啊|啦|欧|好运|欧气|一发)?[，,。！!？?\s]*$",
)


def pull_request_of(text: str) -> int | None:
    """严格判定消息是否为抽卡请求：是则返回抽卡次数，否则返回 None。

    配合 filter.regex 使用，防止闲聊误触：
    - 数量后必须带「连/抽/次寻访」抽卡词（裸“次”不算，杜绝“第一次”→单抽）；
    - 超长或结构不符的消息（如“这个十连抽卡视频很好笑”）整句校验不通过，
      handlers 直接静默返回，不回复任何内容；
    - 先剥离称呼/引导词再取次数，避免“来一发十连”被“来一”抢先解析成 1 次。
    """
    t = (text or "").strip()
    if not t or not _STRICT_PULL.fullmatch(t):
        return None
    core = _LEAD_RE.sub("", t)
    count = parse_pull_count(core)
    if count is None:
        count = parse_pull_count(t)  # “来 100 / 抽卡 100”这类纯数字引导句
    return count


def _weight(pickups: str) -> dict[str, int]:
    """\"能天使,银灰|2\" → {能天使:1, 银灰:2}；负权重过滤为 0。"""
    result: dict[str, int] = {}
    for name in (pickups or "").split(","):
        name = name.strip()
        if not name:
            continue
        weight = 1
        if "|" in name:
            try:
                weight = int(name.split("|")[1])
            except ValueError:
                weight = 1
            name = name.split("|")[0].strip()
        result[name] = result.get(name, 0) + max(weight, 0)
    return result


def _gacha_weights(
    up: dict[str, int], fillin: list[str], up_rate: float,
) -> dict[str, float]:
    """up 干员按 up_rate 分配权重，其余干员均分 (1-up_rate)（移植自 Amiya gacha）。"""
    scale = 10000.0
    up_rate = max(0.0, min(1.0, up_rate))
    final: dict[str, float] = {}

    up_total = sum(w for w in up.values() if w > 0)
    if up_total > 0:
        for name, w in up.items():
            if name and w > 0:
                final[name] = up_rate * scale * w / up_total

    fill: dict[str, int] = {}
    for name in fillin:
        fill[name] = fill.get(name, 0) + 1
    fill_total = 0
    for name, w in fill.items():
        if name not in final:
            fill_total += max(w, 1)
    if fill_total > 0:
        for name, w in fill.items():
            if name not in final and w > 0:
                final[name] = (1 - up_rate) * scale * w / fill_total
    return {k: max(v, 0.0) for k, v in final.items()}


def _choose(weight_map: dict[str, float]) -> str:
    names = list(weight_map.keys())
    if not names:
        return "未知干员"
    weights = list(weight_map.values())
    if sum(weights) <= 0:
        return random.choice(names)
    return random.choices(names, weights=weights, k=1)[0]


class GachaEngine:
    """单用户抽卡逻辑（概率/保底/UP 权重/十连保底）。"""

    def __init__(self, data: GameData, user: dict, pool: dict | None = None) -> None:
        self.data = data
        self.user = user
        self.break_even = int(user.get("break_even", 0))
        self.pool = pool if pool is not None else self._find_pool(user.get("pool", ""))
        self.pools6: dict[str, float] = {}
        self.pools5: dict[str, float] = {}
        self.pools4: list[str] = []
        self.pools3: list[str] = []
        self._build_pools()

    def _find_pool(self, pool_id: str) -> dict | None:
        if not self.data.pools:
            return None
        for p in self.data.pools:
            if str(p.get("id", "")) == str(pool_id):
                return p
        return self.data.pools[0]

    def _build_pools(self) -> None:
        by = self.data.by_rarity
        pool = self.pool or {}
        up6 = _weight(pool.get("pickup_6") or "")
        up5 = _weight(pool.get("pickup_5") or "")
        self.pools6 = _gacha_weights(
            up6, by[6], float(pool.get("pickup_6_rate", 0.5) or 0.5),
        )
        self.pools5 = _gacha_weights(
            up5, by[5], float(pool.get("pickup_5_rate", 0.5) or 0.5),
        )
        self.pools4 = by[4]
        self.pools3 = by[3]

    def get_rates(self) -> dict[int, int]:
        rates = dict(BASE_RATES)
        if self.break_even >= PITY_START:
            six = min(100, 2 + (self.break_even - PITY_START + 1) * 2)
            shift = six - rates[6]
            rates[6] = six
            for r in (3, 4, 5):  # 低星优先扣减
                if shift <= 0:
                    break
                take = min(shift, rates[r])
                rates[r] -= take
                shift -= take
        return rates

    def roll_once(self) -> dict:
        """单次抽取：返回 {rarity, name}，并更新保底计数。"""
        self.break_even += 1
        rates = self.get_rates()
        rarity = random.choices(
            [6, 5, 4, 3], weights=[rates[6], rates[5], rates[4], rates[3]], k=1,
        )[0]
        if rarity == 6:
            self.break_even = 0
        if rarity == 6:
            name = _choose(self.pools6)
        elif rarity == 5:
            name = _choose(self.pools5)
        elif rarity == 4:
            name = random.choice(self.pools4) if self.pools4 else "未知干员"
        else:
            name = random.choice(self.pools3) if self.pools3 else "未知干员"
        return {"rarity": rarity, "name": name}

    def roll(self, times: int, guarantee5: bool = True) -> list[dict]:
        """连抽；times==10 且 guarantee5 开启时十连保底 5★。"""
        if guarantee5 and times == 10:
            results = [self.roll_once() for _ in range(10)]
            if not any(r["rarity"] >= 5 for r in results):
                results[9] = self._roll_guarantee()
            return results
        return [self.roll_once() for _ in range(times)]

    def _roll_guarantee(self) -> dict:
        rnd = random.random()
        if rnd < 0.02:
            name = _choose(self.pools6)
            self.break_even = 0
            return {"rarity": 6, "name": name}
        return {"rarity": 5, "name": _choose(self.pools5)}

    def six_rate_next(self) -> int:
        if self.break_even >= PITY_START:
            return min(100, 2 + (self.break_even - PITY_START + 1) * 2)
        return 2
