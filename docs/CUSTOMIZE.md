# 怎么改

## 改人设 / 加 NPC（最多 4 个）

推荐在管理台点着改：<http://127.0.0.1:8010/admin> → 「NPC 人设」→ 点卡片 → 改 → 保存，游戏里立刻生效。

也可以直接编辑 `backend/data/npcs.yaml`，然后重启后端（或 `POST /api/npcs/{id}` 触发重载）。

关键字段：

- `prompt_mode: template` —— 用 `personality / expertise / hobbies / speech_style / catchphrases /
  backstory / relationships / secrets` 这些字段自动拼系统提示词，改起来省事
- `prompt_mode: custom` —— 直接写 `system_prompt`，可用占位符：
  `{name} {title} {mood} {affinity} {affinity_level} {game_time} {location} {activity}
  {facts} {reflection} {secrets} {event} {player_name} {roommates}`
- `secrets` 按好感度档位解锁（`level: 亲密 / 挚友`）
- `temperature` 越高越跳脱（0.8-1.0 比较像真人，1.2 以上容易胡言乱语）
- `behavior`：`wander / wander_radius / move_speed / chattiness（主动搭话概率）/
  can_chat_with_npcs / monologue`
- `schedule`：`{time: "20:00", location: 电脑桌, activity: 打游戏}`，`location` 必须是
  `game.yaml` 里 `locations` 的名字
- `sprite`：`character_1 ~ character_4` 四张贴图（角色图布局一致，直接换名字即可）

管理台右上角「预览完整 prompt」能看到最终拼出来的提示词，「试聊一句」可以直接验证效果。

## 改地图上的点位

NPC 走的路线在 `helloagents-ai-town/scripts/world_map.gd`：

- `POINTS` 是「地点名 → 像素坐标」，名字要和 `game.yaml` 的 `locations` 对齐
- 下划线开头的是内部路点（`_top_mid`、`_gap` 之类），用来绕过墙体
- `EDGES` 是路点之间的通道，BFS 会沿着这些边找路

改完在 Godot 里按 F5 测试；如果 NPC 走到一半卡住，它会在 1.5 秒后临时穿墙过去（`npc.gd`
里的 `_check_stuck`），看到"卡住了，临时穿墙"的日志就说明这两个点之间其实不连通，需要补一个路点。

## 换模型

`backend/.env`：

```env
LLM_BASE_URL=http://175.27.225.51:4047/v1
LLM_MODEL=echo-2.2.2
LLM_API_KEY=EMPTY          # 没有鉴权就留 EMPTY
```

- 任何 OpenAI 兼容接口都能用（DeepSeek、Qwen、中转服务、本地 vLLM 都行）
- 设为 `LLM_MOCK=1` 或把 `LLM_BASE_URL` 留空 → 离线 Mock 模式，不调用模型，适合调前端
- 推理型模型（会先输出思考）也能用：`llm.py` 会自动去掉 `<think>` 块，并把 `max_tokens`
  提到 512 以上，避免"思考把预算吃光、正文为空"

## 调随机度与节奏

`backend/data/game.yaml` 的 `randomness`：

```yaml
topic_seed_chance: 0.35      # 每轮注入随机话题的概率
memory_callback_chance: 0.25 # 主动提起旧事的概率
quest_chance: 0.12           # 每轮后提出委托的概率
npc_chat_chance: 0.25        # 同一地点每游戏小时互聊的概率
monologue_minutes: 90        # 独白间隔（游戏分钟）
```

时钟速度在 `backend/.env`：`GAME_MINUTES_PER_SECOND=1.0` 表示现实 1 秒 = 游戏 1 分钟，
也就是现实 1 分钟 = 游戏 1 小时。想快速看到一天的变化就调到 10~30。

## 加道具 / 事件 / 话题

同样是 `game.yaml`：`items`（道具及描述）、`starting_inventory`（开局背包）、
`topic_seeds`（随机话题）、`event_presets`（导演面板里的预设事件）。

NPC 对道具的好恶写在 `npcs.yaml` 的 `likes` / `dislikes`：送对了 +8，送错了 -5，其他 +3。

## 改端口

`backend/.env` 的 `API_PORT` 和 `helloagents-ai-town/scripts/net.gd` 的
`HTTP_BASE` / `WS_URL` 要同时改。

## 数据重置

```bash
rm backend/data/town.db*      # 清空所有记忆、好感度、委托、背包
```

只想清空某个 NPC 对玩家的记忆：管理台「记忆」页 → 选人 → 「清空这个 NPC 的全部记忆」。

## 语气档位（让 NPC 别像客服）

每个 NPC 有一个 `tone` 字段，管理台「NPC 人设」里也能直接下拉切换：

| 档位 | 效果 |
|---|---|
| `温和` | 客气得体，不骂人、不低俗、不阴阳怪气 |
| `正常`（默认） | 有情绪、会吐槽、会夸张抱怨，但不主动爆粗口 |
| `泼辣` | 像现实里的损友室友：会爆粗口（卧槽／滚／神经病）、毒舌互怼、阴阳怪气、含蓄的成人向玩笑、不爽就甩脸子、被冒犯会还嘴或记仇而不是道歉 |

`backend/data/game.yaml` 里的 `default_tone` 决定没写 `tone` 的 NPC 用哪一档。

实测对比（同一句「你好烦啊，别一直念叨我，你懂个屁」）：

- 柒柒（正常）：_（沉默了几秒，低下头）……我知道了。是我多嘴了，对不起。_
- 泽不易（泼辣）：_（翻了个白眼）懂个屁？我带你上分的时候你怎么不说？行，你自己玩吧，别到时候菜了又来找我哭。_

两档都会正常扣好感度（−10），泼辣档只是会还嘴，不会变成没脾气。

**明确的边界**（写在提示词里，删不掉）：不针对真实群体说歧视话、不写露骨性描写、
不教唆违法或危险行为、不涉及儿童与性。想再加粗一点可以自己改 `app/prompts.py` 里的
`TONE_STYLES`，但你的模型服务（echo-2.2.2）本身可能还有一层过滤。
