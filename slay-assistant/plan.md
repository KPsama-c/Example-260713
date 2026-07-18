# Slay Assistant — Slay the Spire 2 游戏助手

## 概述

Rust 实现的 STS2 实时决策辅助工具。通过游戏内 Mod 暴露的 HTTP API 读取
完整游戏状态，调用 LLM 分析当前局面，在悬浮窗中显示推荐操作。

```
热键 (Ctrl+Shift+A) → 读状态 (HTTP) → 分析 (LLM + 本地规则) → 悬浮窗显示
```

---

## 技术栈

| 层 | 选型 | 说明 |
|----|------|------|
| 语言 | Rust 2021 Edition | `rustc 1.97+` |
| 异步 | Tokio | current_thread runtime |
| HTTP | reqwest 0.12 | rustls-tls, JSON |
| 序列化 | serde + serde_json | 游戏状态 JSON ↔ Rust struct |
| 全局热键 | win-hotkeys 0.5 | Windows `WH_KEYBOARD_LL` 钩子 |
| LLM | OpenAI 兼容 API | DeepSeek / Claude / 本地模型 |
| 悬浮窗 | egui + egui_overlay | Phase 3, 透明可穿透窗口 |
| 配置 | TOML | 可编辑的配置文件 |

## 架构

```
┌──────────────────────────────────────────────┐
│              STS2 游戏进程 (Godot 4.5.1)      │
│  ┌────────────────┐                          │
│  │ STS2MCP Mod    │  HTTP API                │
│  │ localhost:15526 │  /api/v1/game-state     │
│  └───────┬────────┘                          │
└──────────┼───────────────────────────────────┘
           │ HTTP JSON
           ▼
┌──────────────────────────────────────────────┐
│            slay-assistant (Rust)              │
│                                               │
│  main.rs         热键 + 事件循环              │
│  config.rs       TOML 配置管理                │
│                                               │
│  game/                                        │
│    client.rs     HTTP → GameState             │
│    state.rs      类型定义 (Combat/Map/...)    │
│                                               │
│  analysis/        分析调度                    │
│    mod.rs        screen_type → 路由           │
│    combat.rs     战斗分析 (斩杀/伤害/格挡)    │
│    map.rs        地图路线评估                  │
│    rewards.rs    卡牌奖励选择                  │
│    shop.rs       商店购买建议                  │
│    events.rs     事件选项分析                  │
│                                               │
│  llm/             LLM 集成                    │
│    mod.rs        OpenAI-compatible 客户端      │
│    prompts.rs    各场景 Prompt 模板            │
│                                               │
│  ui/              UI 层                       │
│    overlay.rs    悬浮窗 (Phase 3 egui)        │
│    panels.rs     推荐面板渲染                  │
└──────────────────────────────────────────────┘
```

## 数据流

```
热键按下 (Ctrl+Shift+A)
    │
    ▼
① HTTP GET http://localhost:15526/api/v1/game-state
    │  (依次尝试 /api/v1/game_state, /game-state, /state)
    ▼
② 反序列化 JSON → GameState
    │  .screen_type → 场景分类
    │  .combat_state / .map_state / .shop_state / ...
    ▼
③ 分析调度 (analysis::analyze)
    │
    ├── Combat  → 本地启发式 (Phase 4) + LLM (Phase 2)
    ├── Map     → LLM 路线分析
    ├── Shop    → LLM 购买建议
    ├── Event   → LLM 选项评估
    └── Reward  → LLM 卡牌选择
    │
    ▼
④ 渲染推荐 → 终端 (Phase 1-2) / 悬浮窗 (Phase 3)
    │
    ▼
⑤ 自动隐藏 (5s) / 再次按热键隐藏
```

## 游戏状态类型

### GameState (顶层)

| 字段 | 类型 | 说明 |
|------|------|------|
| screen_type | ScreenType | Map/Combat/Shop/Event/Reward/Rest/BossReward/GameOver |
| character | Option\<String\> | 角色职业 |
| act / floor | Option\<u8\> | 当前关卡/楼层 |
| current_hp / max_hp | Option\<i32\> | 生命值 |
| gold | Option\<i32\> | 金币 |
| deck | Vec\<Card\> | 当前牌组 |
| relics | Vec\<Relic\> | 圣物列表 |
| potions | Vec\<Potion\> | 药水列表 |
| combat_state | Option\<CombatState\> | 战斗状态 |
| map_state | Option\<MapState\> | 地图状态 |
| shop_state | Option\<ShopState\> | 商店状态 |
| event_state | Option\<EventState\> | 事件状态 |
| reward_state | Option\<RewardState\> | 奖励状态 |

### CombatState

| 字段 | 说明 |
|------|------|
| turn | 当前回合数 |
| hand / draw_pile / discard_pile / exhaust_pile | 手牌/抽牌堆/弃牌堆/消耗堆 |
| energy / max_energy | 当前/最大能量 |
| block | 当前格挡 |
| powers | 玩家能力 (力量/敏捷/... 数值) |
| enemies | 敌人列表 (HP/格挡/意图/能力) |

### MapState

| 字段 | 说明 |
|------|------|
| nodes | 地图节点列表 |
| current_node_id | 当前所在节点 |

### ShopState / EventState / RewardState

各包含对应的选项/物品/价格等结构化数据。

## LLM Prompt 设计

### System Prompt 核心原则

- 角色：STS2 专家级策略分析师
- 输出格式：固定三段式 (推荐/备选/不推荐，各附一句话理由)
- 分析维度：血量安全评估、牌组协同、圣物协同、能量效率

### 各场景输入

```rust
// 所有场景共用：JSON 序列化的当前游戏状态
let user_message = format!("当前场景：{state_json}");
// 各场景附加的引导问题 (见 prompts.rs)
```

---

## 实现进度

### ✅ Phase 1: 骨架 + 状态读取 (已完成)

- [x] Cargo 项目初始化，依赖配置
- [x] config.rs — TOML 配置 (API key, 热键, LLM endpoint)
- [x] game/state.rs — 完整游戏状态类型定义
- [x] game/client.rs — HTTP 客户端 (多路径兼容)
- [x] main.rs — 热键注册 + async 事件循环
- [x] 编译通过 (14 个 expected warnings)

### ✅ Phase 2: LLM 集成 (已完成)

- [x] 接入 analysis 模块到实际 LLM 调用
- [x] combat/map/shop/event/reward（+ rest 简易）Prompt 路由
- [x] LLM 返回解析为 Recommendation（三段式 + 非结构化回退）
- [x] 双协议：`api_style = openai | anthropic`
- [x] 终端多行推荐面板；热键分析防抖
- [x] README + config.example.toml

### ✅ Phase 3: 悬浮窗 UI (v0.3)

- [x] eframe/egui 置顶窗口（`overlay_enabled`）
- [x] 中文推荐面板 + 与终端同步
- [x] 自动隐藏计时（`auto_hide_ms`）
- [ ] 鼠标穿透（可选后续）

### ✅ Phase 4: 本地启发式 (已完成)

- [x] 战斗斩杀线检测 (伤害 vs 敌人有效HP)
- [x] 格挡效率计算 (预计承伤 vs 已有/手牌格挡)
- [x] 能量效率排名 (伤害+格挡)/费用
- [x] 快速出牌建议 (贪心顺序，不调 LLM)
- [x] 战斗：本地始终输出，有 Key 时追加 LLM
### ✅ Phase 5: 打磨 (深入优化 v0.2)

- [x] 错误处理加强：GameClient 超时、解析失败 body 片段、启动 ping、分析防抖
- [x] 本地优先管线 pipeline：地图/战斗/奖励/商店/事件/篝火 秒出
- [x] `llm.enabled` / `SLAY_SKIP_LLM` 可关远程
- [x] STS2MCP v0.4 `/api/v1/singleplayer` + adapter
- [ ] 配置 UI / egui 悬浮窗
- [ ] 战斗 JSON 实机字段再对齐
---

## 前置条件

### 安装 STS2 游戏状态 Mod

1. 下载 STS2MCP 或 STS2 AI Agent Mod
2. 将 DLL 放入 `<Steam>\steamapps\common\SlayTheSpire2\mods\`
3. 启动游戏，Mod 自动在 localhost:15526 监听

### 配置 API Key

编辑 `config.toml`（首次运行自动生成）：

```toml
game_api_url = "http://localhost:15526"

[llm]
base_url = "https://api.deepseek.com/anthropic"
api_key = "YOUR_KEY_HERE"
model = "deepseek-v4-pro"
max_tokens = 2000

hotkey = "Ctrl+Shift+A"
auto_hide_ms = 5000
```

## 运行

```bash
cargo run --release
```

按 `Ctrl+Shift+A` 获取当前游戏状态分析。
