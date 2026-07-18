# Slay Assistant

Rust 实现的 *Slay the Spire 2* 实时决策助手。

```text
热键 (Ctrl+Shift+A) → 读 STS2_MCP 状态
  → 流派知识库 + 本地启发式/BFS → 终端 + 悬浮窗
  → （可选）LLM 补充
```

内置 **角色流派库**（`data/archetypes.json`）：铁甲/静默/缺陷/摄政/死灵的主流构筑、核心牌、过牌与出牌思路，会参与选牌打分与战斗/地图提示。

## 前置条件

1. 安装并启动 **STS2**，以及 **STS2_MCP**（v0.4.x）  
   - 默认 API：`http://localhost:15526`  
   - 健康检查：`GET /` → `Hello from STS2 MCP`  
   - **真实战局**：`GET /api/v1/singleplayer`（单人）/ `/api/v1/multiplayer`（多人）  
   - 旧路径 `/api/v1/game-state` 在 v0.4 **不存在**（会 404）
2. 准备 **OpenAI 兼容** 或 **Anthropic Messages** 风格的 LLM API Key（如 DeepSeek）
3. Rust 工具链（`cargo`）

## 方案 A：游戏热键 + 与 NarraFork 同款脑

| 职责 | 谁做 |
|------|------|
| 全局热键 / 读 STS2_MCP / 显示建议 | **本程序** |
| 推理模型 | 与 NarraFork **同一家 API**（如 DeepSeek） |

这不是把消息注入 NarraFork 会话，而是**同一模型 + STS2 策略 prompt**，延迟最低，适合边打边按键。

### 配置

优先项目根目录 `E:\projects\slay-assistant\config.toml`：

```powershell
cd E:\projects\slay-assistant
copy config.example.toml config.toml
notepad config.toml
# 填入与 NarraFork 相同的 DeepSeek api_key
```

也可用环境变量（**覆盖**文件，适合不写密钥到磁盘）：

```powershell
set SLAY_LLM_API_KEY=sk-你的key
set SLAY_LLM_API_STYLE=openai
set SLAY_LLM_BASE_URL=https://api.deepseek.com
set SLAY_LLM_MODEL=deepseek-chat
```


模型名以你在 NF 里实际能选的为准（如 `gpt-5.6-sol` / `gpt-5.5` 等）。

### OpenAI 兼容（DeepSeek 官方）

```toml
[llm]
api_style = "openai"
base_url = "https://api.deepseek.com"
api_key = "sk-..."
model = "deepseek-chat"
max_tokens = 2000
timeout_secs = 60
```

### Anthropic Messages（若 NF 里用的是 `/anthropic` 路径）

```toml
[llm]
api_style = "anthropic"
base_url = "https://api.deepseek.com/anthropic"
api_key = "sk-..."
model = "deepseek-chat"
max_tokens = 2000
timeout_secs = 60
```

`config.toml` 已在 `.gitignore` 中，请勿提交密钥。

## 运行

```bash
cd E:\projects\slay-assistant
cargo run --release
```

已编译的可执行文件也可直接用（需旁边有 `config.toml`）：

```text
dist\slay-assistant.exe
```

### 目录瘦身

构建缓存 `target/` 可达数 GB。日常可：

```powershell
cargo clean          # 删掉 target（可从 2GB+ 降到十几 MB）
# 需要运行时再：
cargo build --release
# 或复制 exe 到 dist\ 后 clean，只保留 dist\slay-assistant.exe（约 10MB）
```

源码本身只有约 **200KB**；体积几乎全是 `target/`。
- 按配置中的热键（默认 `Ctrl+Shift+A`）触发分析  
- `Ctrl+C` 退出  
- 日志：`RUST_LOG=debug cargo run`

## 项目结构

```text
src/
  main.rs          热键 + 多线程分析任务（可打断 LLM）
  config.rs        TOML（api_style / proxy / overlay 穿透）
  run_cache.rs     本局拥有牌 count + 降级快照
  knowledge/       流派库 + 中英卡名别名
  game/            STS2_MCP client + adapter + GameState
  analysis/        本地启发式 + BFS + 可选 LLM
  llm/             OpenAI / Anthropic
  ui/              egui 置顶悬浮窗 + 终端面板
data/
  archetypes.json  角色流派
  card_aliases.json  EN/CN 别名
```

## 进度

| Phase | 内容 | 状态 |
|-------|------|------|
| 1 | 骨架、状态读取、热键 | 完成 |
| 2 | LLM 双协议 + 全场景分析 | 完成 |
| 3 | egui 置顶悬浮窗 + 可选穿透 | **完成** |
| 4 | 本地战斗/选路/选牌启发式 | 完成 |
| 5 | STS2_MCP 对齐 + 地图 BFS | **完成** |
| 6 | 拥有牌缓存 / 商店降级 / 热键打断 | **完成** |

## 已知限制

- **商店**：STS2_MCP 在部分游戏版本上 `MerchantRoom.Inventory` 崩溃 → 无法读货架；助手会降级给进店策略（不调 LLM 空等）  
- 无 API Key / `llm.enabled=false`：纯本地秒出  
- 热键分析中再按一次可打断并重新拉状态  
- 缓存文件 `run_card_cache.json` 仅含本局**拥有**牌，不把奖励候选当牌组  

## 许可

个人学习项目。请遵守游戏与第三方 API 的使用条款。
