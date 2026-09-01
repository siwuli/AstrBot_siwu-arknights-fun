"""猜干员游戏模块（文本版，参考 Amiya-Bot amiyabot-game-guess 简化移植）。

- 群内发起「猜干员 难度」后全员参与，直接发送干员名作答；
- 难度：初级（基础线索）/ 中级（技能名）/ 高级（语音台词）/ 资深（档案片段），
  数据不足时高级/资深自动回退中级；
- 答对获得得分与合成玉（连击加成），每局前三名额外获得合成玉奖励。
"""

import asyncio
import random
import time

from astrbot.api.all import AstrBotConfig, AstrMessageEvent
from astrbot.api.event.filter import CustomFilter
from astrbot.api.platform import MessageType

from .gamedata import GameData

DIFFICULTIES = ("初级", "中级", "高级", "资深")
DIFF_RATE = {"初级": 1, "中级": 2, "高级": 3, "资深": 4}

COMMAND_TIP = {"提示", "线索", "tip"}
COMMAND_SKIP = {"跳过", "下一题", "skip"}
COMMAND_END = {"结束竞猜", "不猜了"}

_SKIP_STORY_TITLES = {"基础档案", "综合体检测试", "综合性能检测结果", "临床诊断分析"}

# gid -> session（模块级，放行过滤器需要直接访问）  # noqa: RUF012
_SESSIONS: dict[str, dict] = {}


class GameActiveFilter(CustomFilter):
    """当前群有进行中的猜干员游戏时放行，交给高优先级 handler。"""

    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        if event.get_message_type() != MessageType.GROUP_MESSAGE:
            return False
        gid = str(event.get_group_id() or "")
        sess = _SESSIONS.get(gid) if gid else None
        return sess is not None and sess.get("phase") == "playing"


class GuessEngine:
    """猜干员游戏的全部逻辑（题面生成 / 作答 / 打分 / 结算）。"""

    def __init__(self, data: GameData) -> None:
        self.data = data

    # ---------------- 会话 ----------------
    def new_session(self, gid: str, level: str, questions: int, timeout: int) -> dict:
        return {
            "gid": gid,
            "difficulty": level,
            "phase": "playing",
            "round": 0,
            "questions": max(1, questions),
            "timeout": max(15, timeout),
            "target": None,
            "puzzle_text": "",
            "tips": [],
            "used_tips": [],
            "players": {},  # uid -> 本局得分
            "hits": {},  # uid -> 猜对次数
            "combo_user": None,
            "combo_count": 0,
            "event": asyncio.Event(),
            "answered": False,
            "answer_ok": False,
            "winner": None,
            "skip": False,
            "end": False,
            "summary": "",
            "start_ts": time.time(),
        }

    @staticmethod
    def difficulty_desc(level: str) -> str:
        desc = {
            "初级": "基础线索（职业/阵营/星级/技能名）",
            "中级": "技能名提示",
            "高级": "语音台词",
            "资深": "档案片段",
        }
        return (
            f"难度 {level}（{desc.get(level, '')}）· 直接发干员名作答！「提示」要线索"
            f"\n「跳过」公布答案 「结束竞猜」终止"
        )

    # ---------------- 主循环 ----------------
    async def play(
        self,
        sess: dict,
        send,
        grant_jade,
        add_points,
        points_base: int,
        jade_base: int,
        rank_jades: tuple[int, int, int],
    ) -> None:
        """逐轮出题，等待作答/超时，结束后结算排名奖励。

        Args:
            sess: 本局会话。
            send: 群消息发送协程（async text -> None）。
            grant_jade: 发放合成玉协程（async uid, jade -> None）。
            add_points: 赛季积分累加协程（async uid, points -> None）。
            points_base: 猜对基础得分。
            jade_base: 猜对基础合成玉。
            rank_jades: 前三名基础合成玉奖励 (第1, 第2, 第3)。
        """
        gid = sess["gid"]
        await send("猜干员开始！" + self.difficulty_desc(sess["difficulty"]))
        try:
            while sess["round"] < sess["questions"] and sess["phase"] == "playing":
                if not self.pick_target(sess):
                    sess["summary"] = "干员数据不足，游戏终止，试试「猜干员 初级」?"
                    break
                self.build_puzzle(sess)
                await self.show_question(send, sess)
                sess["event"].clear()
                sess["answered"] = False
                sess["answer_ok"] = False
                sess["winner"] = None
                sess["round"] += 1
                try:
                    await asyncio.wait_for(sess["event"].wait(), timeout=sess["timeout"])
                except asyncio.TimeoutError:
                    await send(f"⌛ 无人作答，答案是 {sess['target']['name']}，进入下一题。")
                if sess["phase"] != "playing":
                    break
                if sess["answer_ok"] and sess.get("winner"):
                    await self.on_hit(send, grant_jade, add_points, sess, points_base, jade_base)
                if sess["phase"] == "playing":
                    await send(f"（第 {sess['round']}/{sess['questions']} 题）")
            if sess["phase"] == "playing":
                await self.finish(send, grant_jade, sess, rank_jades)
        except Exception as e:  # noqa: BLE001
            import logging

            logging.getLogger("astrbot").warning(f"[arknights_fun] 猜干员游戏循环异常: {e}")
            sess["summary"] = "游戏异常中断，可重新发起。"
        finally:
            sess["phase"] = "finished"
            _SESSIONS.pop(gid, None)

    # ---------------- 出题 ----------------
    def pick_target(self, sess: dict) -> bool:
        cands = [o for o in self.data.operators.values() if o.get("rarity", 3) >= 3]
        if not cands:
            return False
        sess["target"] = random.choice(cands)
        return True

    def build_puzzle(self, sess: dict) -> None:
        t = sess["target"]
        level = sess["difficulty"]
        if level in ("高级", "资深"):
            extra = self.voice_line(t["name"]) if level == "高级" else self.story_line(t["name"])
            if extra:
                sess["puzzle_text"] = extra
            else:
                level = "中级"  # 数据不足回退
                sess["difficulty"] = "中级"
        if not sess.get("puzzle_text"):
            skills = (t.get("skills") or [])[:3]
            if skills:
                sess["puzzle_text"] = f"TA的技能有：{'、'.join(skills)}"
            else:
                sess["puzzle_text"] = f"这是一位{'★' * t['rarity']}干员"
        sess["tips"] = self._build_tips(t)
        sess["used_tips"] = []

    def _build_tips(self, t: dict) -> list[str]:
        tips: list[str] = []
        if t.get("prof"):
            tips.append(f"TA的职业是{t['prof']}，分支是{t.get('sub') or '未知'}")
        for label, key in (("阵营", "group"), ("势力", "nation"), ("队伍", "team")):
            v = t.get(key)
            if v and v not in ("未知", "None", "", None):
                tips.append(f"TA的所属{label}是{v}")
                break
        if t.get("skills"):
            tips.append(f"TA的技能有：{'、'.join(t['skills'][:3])}")
        if t.get("sex"):
            tips.append(f"TA是{t['sex']}性干员")
        name = t.get("name") or ""
        if len(name) > 1:
            tips.append(f"TA的代号里有一个字是「{random.choice(name)}」")
        return tips

    async def show_question(self, send, sess: dict) -> None:
        t = sess["target"]
        header = f"🎯 第 {sess['round'] + 1}/{sess['questions']} 题（难度 {sess['difficulty']}）"
        body = sess.get("puzzle_text") or f"这是一位{'★' * t['rarity']}干员"
        await send(f"{header}\n{body}\n\n猜猜 TA 是谁？直接发送干员名~")

    # ---------------- 作答与奖励 ----------------
    async def handle_msg(self, event: AstrMessageEvent, sess: dict, points_base: int, jade_base: int) -> bool:
        """游戏进行中处理作答/提示/跳过/结束；返回是否已处理并拦截该消息。"""
        text = (event.get_message_str() or "").strip()
        target = (sess.get("target") or {}).get("name", "")
        if not text:
            return False
        if text == target:
            if not sess["answered"]:
                sess["answered"] = True
                sess["answer_ok"] = True
                uid = str(event.get_sender_id() or "")
                pts, jade = self._hit_rewards(sess, uid, points_base, jade_base)
                sess["winner"] = {"uid": uid, "name": event.get_sender_name() or "神秘博士", "points": pts, "jade": jade}
                sess["event"].set()
            event.stop_event()
            return True
        if text in COMMAND_TIP:
            unused = [x for x in sess["tips"] if x not in sess["used_tips"]]
            if unused:
                tip = random.choice(unused)
                sess["used_tips"].append(tip)
                await event.send(f"💡 提示：{tip}")
            else:
                await event.send("没有更多提示啦，直接猜名字吧~ (＞﹏＜)")
            event.stop_event()
            return True
        if text in COMMAND_SKIP:
            sess["answered"] = True
            sess["answer_ok"] = False
            await event.send(f"答案揭晓：{target}。惩罚过关，下一题减分~（不影响本局）")
            sess["event"].set()
            event.stop_event()
            return True
        if text in COMMAND_END:
            sess["phase"] = "ending"
            sess["answered"] = True
            await event.send(f"🏳️ 游戏提前结束，答案是 {target}。")
            sess["event"].set()
            event.stop_event()
            return True
        return False

    def _hit_rewards(self, sess: dict, uid: str, points_base: int, jade_base: int) -> tuple[int, int]:
        """按难度倍率与连击计算（得分, 合成玉）。"""
        rate = DIFF_RATE.get(sess["difficulty"], 1)
        if sess.get("combo_user") != uid:
            sess["combo_user"] = uid
            sess["combo_count"] = 1
        else:
            sess["combo_count"] = sess.get("combo_count", 1) + 1
        bonus = sess["combo_count"] // 3
        points = int(points_base * rate * (100 - len(sess["used_tips"]) * 2) / 100) + bonus
        jade = int(jade_base * rate * (1 + bonus * 0.1))
        return points, jade

    async def on_hit(self, send, grant_jade, add_points, sess: dict, points_base: int, jade_base: int) -> None:
        winner = sess["winner"]
        uid = winner["uid"]
        sess["players"][uid] = sess["players"].get(uid, 0) + winner["points"]
        sess["hits"][uid] = sess["hits"].get(uid, 0) + 1
        await grant_jade(uid, winner["jade"])
        await add_points(uid, winner["points"])
        await send(f"🎉 猜对了！是 {sess['target']['name']}！{winner['name']} +{winner['points']} 分，合成玉+{winner['jade']}")

    # ---------------- 结算 ----------------
    async def finish(self, send, grant_jade, sess: dict, rank_jades: tuple[int, int, int]) -> None:
        lines = [f"🏁 本局结束（难度 {sess['difficulty']}，共 {sess['round']} 题）"]
        rank = sorted(sess["players"].items(), key=lambda kv: -kv[1])
        if not rank:
            lines.append("没人答对，兔兔把答案都收起来啦~下次加油哦 ^_^")
        else:
            for i, (uid, pts) in enumerate(rank[:5]):
                flag = ["🥇", "🥈", "🥉", "4.", "5."][i]
                lines.append(f"{flag} ID {uid}：{pts} 分（猜对 {sess['hits'].get(uid, 0)} 次）")
            for i, (uid, _pts) in enumerate(rank[:3]):
                base = rank_jades[i]
                jade = int(base * DIFF_RATE.get(sess["difficulty"], 1))
                await grant_jade(uid, jade)
                lines.append(f"　　＋{jade} 合成玉（排名奖励）")
        sess["summary"] = "\n".join(lines)

    # ---------------- 数据辅助 ----------------
    def voice_line(self, name: str) -> str:
        o = self.by_name(name)
        if not o:
            return ""
        lines = self.data.voices.get(o.get("id"), [])
        return random.choice(lines).replace(name, "XXX") if lines else ""

    def story_line(self, name: str) -> str:
        o = self.by_name(name)
        if not o:
            return ""
        texts = self.data.stories.get(o.get("id"), [])
        if not texts:
            return ""
        story = random.choice(texts).replace(name, "XXX")[:200]
        return story

    def by_name(self, name: str) -> dict | None:
        return self.data.operators.get(name)
