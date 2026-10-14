# 明日方舟娱乐整合（arknights_fun）

明日方舟主题的签到、模拟抽卡与猜干员三合一插件，统一「寻访凭证 / 合成玉」经济，含 WebUI 管理后台。

## 功能
- 每日「签到」领取寻访凭证，可按配置附带合成玉
- 模拟抽卡：单抽、十连与 1~300 连，含保底与真实卡池
- 猜干员答题：连击加成与每局前三名合成玉奖励
- 抽卡与干员箱支持拼图渲染，素材缺失回退文本；数据存公共 gamedata 目录

## 指令
| 指令（需 @ 或唤醒词） | 说明 |
| --- | --- |
| `签到` | 每日领取寻访凭证并附用户信息卡片 |
| `单抽` / `十连` / `一百连` / `三百抽` | 抽卡，支持 1~300 次与中文数字 |
| `保底` / `卡池` / `卡池切换 N` | 查询水位、查看或切换卡池（含 UP） |
| `box` / `我的干员` | 查看干员箱 |
| `寻访凭证` / `我的资源` | 查看凭证、合成玉与抽卡统计 |
| `我的信息` / `个人信息` | 发送用户信息卡片图 |
| `猜干员 [难度]` / `猜干员排行` / `猜干员数据` | 开局、积分榜与数据；游戏内发干员名作答 |
| `数据状态` | 查看数据就绪与更新时间 |

## 配置
| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `fun_user_title` | 博士 | 对用户的称呼 |
| `sign_enabled` | true | 启用每日签到 |
| `sign_coupon` | 50 | 每日签到发放寻访凭证 |
| `sign_feeling` | 50 | 每次签到增加信赖值 |
| `sign_card_enabled` | true | 签到附用户信息卡片图 |
| `sign_jade` | 0 | 每日签到发放合成玉 |
| `jade_max` | 30000 | 合成玉持有上限 |
| `gacha_enabled` | true | 启用抽卡功能 |
| `gacha_auto_download` | true | 启动时下载全量数据 |
| `gacha_free` | false | 免费抽卡不计消耗 |
| `gacha_coupon_init` | 10 | 新用户初始寻访凭证 |
| `gacha_jade_init` | 0 | 新用户初始合成玉 |
| `jade_per_pull` | 600 | 凭证不足时每抽消耗合成玉 |
| `gacha_guarantee5` | true | 十连保底 5★ |
| `gacha_pickup_6` | 空 | 6★ UP 覆盖名单 |
| `gacha_pickup_5` | 空 | 5★ UP 覆盖名单 |
| `gacha_show_pull_names` | false | 十连结果显示干员名 |
| `gacha_render_image` | true | 抽卡结果输出图片 |
| `gacha_fetch_avatar` | true | 缺失头像自动下载 |
| `gacha_image_font` | 空 | 字体路径，留空自动检测 |
| `gacha_auto_update` | true | 定时自动更新数据 |
| `gacha_update_interval_hours` | 24 | 数据更新间隔（小时） |
| `guess_enabled` | true | 启用猜干员游戏 |
| `guess_questions` | 5 | 每局题目数量 |
| `guess_timeout_sec` | 60 | 每题等待秒数 |
| `guess_points_bingo` | 100 | 猜对基础得分 |
| `guess_jade_bingo` | 300 | 猜对基础合成玉 |
| `guess_jade_rank1` | 3000 | 每局第 1 名合成玉奖励 |
| `guess_jade_rank2` | 2000 | 每局第 2 名合成玉奖励 |
| `guess_jade_rank3` | 1000 | 每局第 3 名合成玉奖励 |
| `guess_auto_update` | true | 猜干员数据自动更新 |
| `guess_update_interval_hours` | 24 | 猜干员数据更新间隔 |

## 安装
1. 上传插件 zip 安装，或解压到 `data/plugins/` 后重启 AstrBot。
2. 依赖见 `requirements.txt`；卡片渲染需 `python -m playwright install chromium`。

## 版本记录
| 版本 | 说明 |
| --- | --- |
| 1.3.17 | 精简文档 |
| 1.3.16 | 猜干员档案提示改读 handbookDict |
| 1.3.15 | 数据更新改为镜像回退与内容比对 |
| 1.3.14 | 渲染复用浏览器实例并给出错误提示 |
