# 雨课堂 · 智·汇大讲堂 直播回放助手

面向雨课堂「智·汇大讲堂」类直播回放的 **Playwright** 本地助手：找出「未观看回放」，静音倍速播放到有效进度（默认总时长 **65%**）。

> **免责声明**：[DISCLAIMER.md](./DISCLAIMER.md) · 非官方 · 仅限本人账号 · 风险自负  
> **范围**：只做观看回放；**不**签到、**不**答题

---

## 最快上手

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 方式 A：网页控制台（推荐）

```bash
python webapp.py
# 浏览器打开 http://127.0.0.1:8765
```

在页面填写 `classroom_id` 或学习日志 URL、倍速等 → 勾选 **免责声明** → **保存** → **刷新待办 / 下一节 / 全部 / 勾选观看**。  
也可 **刷新我的班级** 点选正确班级（避免把 **course_id** 误当 **classroom_id**）。  
多门课：保存后会出现在 **配置档** 下拉，可随时切换（断点按课堂隔离）。  
Windows 也可双击 `start_web.bat`。

**观看范围**（均只处理「未观看回放」）：
- **全部 / 仅缺勤 / 仅已签到**
- **观看勾选**：只跑待办里勾选的节

日志约每秒增量刷新；播放中有进度条与 **本节/批量 ETA**；可 **停止**。  
默认只绑本机 `127.0.0.1`，请勿暴露公网。

**断点策略（v0.8）**：
- 默认 **仅平台确认** 才写 `progress.json`
- 断点键 `classroom_id:lesson_id`，多课不串
- 达线后 grace + soft_boost 真播；SOFT 记 `data/soft.json` 并对账转正
- 真实时长 ETA、失败重试、登录检测
- 播放中不跳学习日志；隐私不进仓/纯净包

> **常见坑**：地址里若只有 `/logs/YOUR_COURSE_ID` 一段数字，那往往是 **course_id**。  
> 真正的 classroom_id 在「我的班级」或完整学习日志 URL 的 `studentLog/` 后面。  
> 会自动尝试把 course_id 映射为 classroom_id。

### 方式 B：终端菜单

```bash
python main.py
```

1. 向导：粘贴 URL 或输入 `classroom_id`  
2. 自动生成 `config.yaml`  
3. 菜单：列表 / 下一节 / 全部 / 设置  

### 主菜单

| 键 | 功能 |
|----|------|
| 1 | 查看未观看回放 |
| 2 | 只看下一节 |
| 3 | 连续看完全部待办 |
| 4 | 改倍速、有效线、换课、有无界面 |
| 5 | 从浏览器当前页识别 classroom_id |
| 0 | 退出 |

---

## 命令行（可选）

```bash
# 不写 config，直接带 ID
python main.py --id 你的classroom_id --list-only --headed

# 带 URL 跑一节
python main.py --url "https://www.yuketang.cn/v2/web/studentLog/xxx" --once

# 只跑向导
python main.py --setup

# 强制菜单 / 禁止菜单
python main.py --menu
python main.py --no-menu --once

# 倍速
python main.py --rate 1.5
python main.py --list-rates
```

| 参数 | 含义 |
|------|------|
| `--id` | classroom_id |
| `--url` | 学习日志 URL |
| `--list-only` / `--once` / `--max N` | 直接动作（不进菜单） |
| `--rate` / `--speed` | 倍速 |
| `--setup` | 仅向导 |
| `--menu` / `--no-menu` | 强制/禁止菜单 |
| `--headed` / `--headless` | 有/无界面 |

---

## 如何找到 classroom_id

1. 登录雨课堂 → 该课 **学习日志**  
2. 地址栏：  
   - `.../v2/web/studentLog/<classroom_id>`  
   - 或移动端 `.../logs/<course_id>/<classroom_id>` → **第二段**  
3. 也可用菜单 **[5]** 打开日志页后自动识别  

---

## 默认参数

| 项 | 默认 |
|----|------|
| 倍速 | 1.25（建议 ≤1.5） |
| 有效进度 | 65% 总时长（本地停播线） |
| 写断点 | 仅平台确认（`require_platform_confirm`） |
| 登录态 | `data/storage_state.json` |
| 断点 | `data/progress.json` |

高级项见 `config.example.yaml`；日常用菜单即可，一般不必手改 YAML。

---

## 目录

```
webapp.py               # 网页控制台入口（推荐）
main.py                 # 终端向导 + 菜单 + CLI
webui/templates/        # 网页模板
yuketang/
  jobs.py               # 后台任务（Web/CLI 共用）
  classrooms.py         # 班级列表 / course_id→classroom_id
  settings.py / ui.py
  logs.py / replay.py / rate.py
data/                   # 本地隐私数据（勿提交）
DISCLAIMER.md
```

---

## 故障排查

| 现象 | 处理 |
|------|------|
| forbidden | 用错 ID，应使用 classroom_id |
| 进度不涨 | `--rate 1.0` 或菜单改倍速 |
| 登录超时 | 有界面模式，加大等待；或重新 `python main.py` |
| 本地达标但下次仍出现 | 平台未确认，属正常；继续播或提高 `complete_ratio` |
| Windows 控制台乱码/崩溃 | v0.5.4+ 已用 ASCII 进度条；请用 Web 控制台 |

---

## 免责

详见 [DISCLAIMER.md](./DISCLAIMER.md)。与雨课堂官方无关。
