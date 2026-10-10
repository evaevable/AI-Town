# 架构与接口

## 目录

```
backend/
  main.py                 FastAPI 入口（挂 /api、/admin、/ws）
  app/
    config.py             配置（读 .env，全部有默认值）
    llm.py                LLM 客户端：流式 / JSON / Mock，并发限流
    db.py                 SQLite 单库 + 建表
    registry.py           npcs.yaml ↔ 内存注册表（校验、热加载、写回）
    prompts.py            提示词组装（模板/自定义、分析、独白、互聊、反思）
    memory.py             六层记忆：写入、混合检索、遗忘、档案、反思
    dialogue.py           对话引擎：一轮 = 流式回复 + 后台分析 + 好感度 + 委托
    world.py              游戏时钟、日程、心情、独白、NPC 互聊、导演指令
    hub.py                WebSocket 广播中心
    api.py                REST + WebSocket 路由
  data/
    npcs.yaml             NPC 人设（真源）
    game.yaml             地点、好感度档位、心情、道具、话题种子、事件预设、随机度
    town.db               运行时数据库（已 gitignore）
  admin/index.html        管理台单页
  tests/test_backend.py   14 项测试（Mock 模型）

helloagents-ai-town/      Godot 4.5 客户端
  project.godot           autoload：Config、Net
  scenes/main.tscn        NPC 改为运行时生成
  scripts/
    net.gd                WebSocket 客户端（autoload Net）
    world_map.gd          地点→坐标 + 路点图（BFS 找路）
    sprites.gd            运行时按行切出角色动画
    npc.gd                寻路、气泡、表情、卡住自救
    main.gd               HUD（时钟/事件/委托）、分发后端推送、自检模式
    dialogue_ui.gd        对话框：流式、快捷回复、送礼、好感度条
    player.gd             玩家移动与 E 交互
```

## 一次对话的调用预算

| 步骤 | 调用 | 玩家等多久 |
|---|---|---|
| 生成回复 | 1 次流式 | 首字，通常 1-3 秒 |
| 好感度/重要性/情绪/记忆摘要/玩家档案/快捷选项 | 1 次 JSON（回复完成后结算） | 需要等判分结果，默认总时限12秒；界面显示计算中 |
| 攒够重要性时的反思 | 约每 10 轮 1 次 | 不等 |
| 独白 | 每 90 游戏分钟，所有 NPC 合并成 1 次 | 不等 |
| NPC 互聊 | 相遇时 1 次，生成整段对话 | 不等 |

回复与判分是两次模型调用：正文可以先看到，但判分完成前发送按钮仍等待，不能说第二次完全不在关键路径。反思和委托现已移出判分等待链路，生成后独立推送。背景请求最多占 N-1 个并发槽（N>1时），为聊天/判分留一个槽；详细日志记录排队、模型token和各阶段毫秒耗时。分析超时不伪造加分，保存原文并保留现有关系。当前 echo-2.2.2 通过 `chat_template_kwargs.enable_thinking=false` 禁用判分长思考；设 `ANALYSIS_DISABLE_THINKING=0` 可恢复或适配不支持此参数的模型。模型服务负载仍会造成数秒波动，不能保证瞬时结算。

## WebSocket 协议（`ws://127.0.0.1:8010/ws`）

客户端 → 服务端

| type | 字段 | 说明 |
|---|---|---|
| `chat` | npc_id, message | 说话 |
| `gift` | npc_id, item | 送礼 |
| `near` | npc_id | 走近了（服务端按性格概率决定要不要主动搭话） |
| `hello` / `ping` | — | 重连 / 心跳 |

服务端 → 客户端

| type | 说明 |
|---|---|
| `welcome` | 连接时下发：npcs、clock、event、locations |
| `chat_start` | 本轮开始，含检索到的记忆摘要 |
| `chat_delta` | 流式文本片段 |
| `chat_replace` | 检测到复读时整句替换 |
| `chat_reply_done` | 正文已完成，接下来计算好感度；尚未判分 |
| `chat_end` | 本轮结算：affinity/affinity_change/level/emotion/importance/memory/reply_options/reason/analysis_ok/timings |
| `gift_result` | 送礼结果：text/affinity/liked |
| `inventory` / `quests` | 背包 / 委托变化 |
| `bubble` | 头顶气泡（kind: monologue/director/greet） |
| `npc_move` | 某 NPC 换了地点（reason: schedule/director） |
| `npc_chat` | 两个 NPC 的整段对话（客户端依次冒泡） |
| `clock` | 每次 tick 的时钟状态 |
| `event` | 世界事件 |
| `toast` | 一次性提示（委托完成等） |
| `npcs_changed` | 管理台改了人设，客户端重新生成 NPC |

## REST（管理台用）

- `GET /api/npcs`、`GET/POST/PUT/DELETE /api/npcs/{id}`
- `GET /api/npcs/{id}/prompt?message=` 预览最终 system prompt
- `GET /api/npcs/{id}/memory`、`POST .../memory/episodes|facts`、`PUT/DELETE /api/memory/episodes/{id}`
- `GET /api/npcs/{id}/recall?query=` 检索打分（相关度/近因/重要性/总分）
- `GET /api/world`、`POST /api/world/clock|event|direct|behavior|monologue|npc_chat`
- `GET /api/player`（好感度、背包、委托）、`POST /api/player/inventory`
- `POST /api/chat` 非流式聊天（curl 调试用）
