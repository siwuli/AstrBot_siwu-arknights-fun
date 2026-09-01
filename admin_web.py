"""管理后台后端 API（AstrBot 内置 WebUI 插件页面）。

注册路由（页面前端经 window.AstrBotPluginPage 桥接调用，自动带插件名前缀）：
  GET  /arknights_fun/page/bootstrap       管理页初始化数据
  GET  /arknights_fun/page/users           用户列表
  POST /arknights_fun/page/users/adjust    调整用户资源（凭证/合成玉）
  POST /arknights_fun/page/users/reset-pity 重置用户保底水位
  POST /arknights_fun/page/users/delete    删除用户
  POST /arknights_fun/page/tools/update    立即触发一次数据更新
  GET  /arknights_fun/page/pools           卡池列表
页面文件：pages/dashboard/{index.html,app.js,styles.css}
"""

import asyncio
import logging
import os

from astrbot.api.web import error_response, json_response, request

from .gamedata import EXCEL_DIR, download_excel_sync, download_pools_sync, excel_ready, pools_last_update

logger = logging.getLogger("astrbot")

PLUGIN_NAME = "arknights_fun"


class ArknightsFunWebAdmin:
    """管理后台控制器：业务逻辑委托给插件实例（复用其数据锁与存储）。"""

    def __init__(self, plugin) -> None:
        self.p = plugin

    def register_routes(self, context) -> None:
        routes = [
            ("/page/bootstrap", self.api_bootstrap, ["GET"], "管理页初始化数据"),
            ("/page/users", self.api_users, ["GET"], "用户列表"),
            ("/page/users/adjust", self.api_adjust, ["POST"], "调整用户资源"),
            ("/page/users/reset-pity", self.api_reset_pity, ["POST"], "重置保底水位"),
            ("/page/users/delete", self.api_delete, ["POST"], "删除用户"),
            ("/page/tools/update", self.api_update_data, ["POST"], "立即更新数据"),
            ("/page/pools", self.api_pools, ["GET"], "卡池列表"),
            ("/page/pools/detail", self.api_pool_detail, ["GET"], "卡池详情与可抽干员"),
            ("/page/pools/override", self.api_pool_override, ["POST"], "按卡池覆盖 UP"),
        ]
        for path, handler, methods, desc in routes:
            context.register_web_api(f"/{PLUGIN_NAME}{path}", handler, methods, desc)

    def _last_update_text(self) -> str:
        try:
            with open(
                os.path.join(EXCEL_DIR, ".excel_last_update"),
                encoding="utf-8",
            ) as f:
                return f.read().strip() or "未知"
        except OSError:
            return "未知"

    # ---------------- 查询 ----------------
    async def api_bootstrap(self):
        p = self.p
        users = p._users
        return json_response(
            {
                "ok": True,
                "data": {
                    "data": {
                        "excel_ready": excel_ready(),
                        "excel_files": len(os.listdir(EXCEL_DIR))  # noqa: ASYNC240
                        if os.path.isdir(EXCEL_DIR)  # noqa: ASYNC240
                        else 0,
                        "last_update": self._last_update_text(),
                        "operators": len(p.data.operators),
                        "pools": len(p.data.pools),
                        "pool_source": p.data.pool_source,
                        "pools_updated": pools_last_update(),
                        "voices": sum(len(v) for v in p.data.voices.values()),
                    },
                    "summary": {
                        "users": len(users),
                        "total_pulls": sum(
                            int(u.get("total", 0)) for u in users.values()
                        ),
                        "total_jade": sum(int(u.get("jade", 0)) for u in users.values()),
                        "total_coupon": sum(
                            int(u.get("coupon", 0)) for u in users.values()
                        ),
                    },
                },
            },
        )

    async def api_users(self):
        users = [
            {
                "uid": uid,
                "coupon": int(u.get("coupon", 0)),
                "jade": int(u.get("jade", 0)),
                "total": int(u.get("total", 0)),
                "break_even": int(u.get("break_even", 0)),
                "box": len(u.get("box") or {}),
                "sign_days": int(u.get("sign_days", 0)),
                "guess_score": int(u.get("guess_score", 0)),
            }
            for uid, u in sorted(
                self.p._users.items(),
                key=lambda kv: -int(kv[1].get("total", 0)),
            )
        ]
        return json_response({"ok": True, "data": users})

    async def api_pools(self):
        by = self.p.data.by_rarity
        pools = [
            {
                "id": str(p.get("id", "")),
                "name": p.get("name", ""),
                "pickup_6": p.get("pickup_6", ""),
                "pickup_5": p.get("pickup_5", ""),
                "counts": {
                    "6": len(by[6]),
                    "5": len(by[5]),
                    "4": len(by[4]),
                    "3": len(by[3]),
                },
                "override": self.p._pool_overrides.get(str(p.get("id", ""))) or {},
            }
            for p in self.p.data.pools
        ]
        return json_response({"ok": True, "data": pools})

    async def api_pool_detail(self):
        pool_id = request.query.get("pool_id", "", str)
        pool = next((p for p in self.p.data.pools if str(p.get("id", "")) == str(pool_id)), None)
        if pool is None:
            return error_response("卡池不存在")
        by = self.p.data.by_rarity
        return json_response(
            {
                "ok": True,
                "data": {
                    "id": str(pool.get("id", "")),
                    "name": pool.get("name", ""),
                    "pickup_6": pool.get("pickup_6", ""),
                    "pickup_5": pool.get("pickup_5", ""),
                    "desc": pool.get("desc", ""),
                    "by_rarity": {str(r): by[r] for r in (6, 5, 4, 3)},
                    "override": self.p._pool_overrides.get(str(pool.get("id", ""))) or {},
                },
            },
        )

    async def api_pool_override(self):
        data = await request.json() or {}
        pool_id = str(data.get("pool_id") or "").strip()
        if not pool_id:
            return error_response("缺少卡池 ID")
        err = self.p._admin_set_pool_override(
            pool_id,
            str(data.get("pickup_6") or ""),
            str(data.get("pickup_5") or ""),
        )
        if err:
            return error_response(err)
        return json_response({"ok": True, "data": {"message": "UP 已保存"}})

    # ---------------- 写操作 ----------------
    async def api_adjust(self):
        data = await request.json() or {}
        uid = str(data.get("uid") or "").strip()
        try:
            coupon_delta = int(data.get("coupon_delta") or 0)
            jade_delta = int(data.get("jade_delta") or 0)
        except (TypeError, ValueError):
            return error_response("调整值必须是整数")
        err = self.p._admin_adjust_user(uid, coupon_delta, jade_delta)
        if err:
            return error_response(err)
        return json_response({"ok": True, "data": {"message": "已更新"}})

    async def api_reset_pity(self):
        data = await request.json() or {}
        uid = str(data.get("uid") or "").strip()
        err = self.p._admin_reset_pity(uid)
        if err:
            return error_response(err)
        return json_response({"ok": True, "data": {"message": "保底水位已重置"}})

    async def api_delete(self):
        data = await request.json() or {}
        uid = str(data.get("uid") or "").strip()
        err = self.p._admin_delete_user(uid)
        if err:
            return error_response(err)
        return json_response({"ok": True, "data": {"message": "已删除"}})

    async def api_update_data(self):
        try:
            ok_pools = await asyncio.to_thread(download_pools_sync)
            ok = await asyncio.to_thread(download_excel_sync)
            if ok or ok_pools:
                self.p.data.load()
            return json_response(
                {
                    "ok": True,
                    "data": {
                        "success": ok,
                        "message": "卡池与干员数据已更新" if (ok or ok_pools) else "更新未完成（无变化或网络失败，见日志）",
                    },
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[arknights_fun] 管理页触发数据更新异常: {e}")
            return error_response(f"更新失败：{e}")
