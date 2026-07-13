# 雨课堂 · 智·汇大讲堂 直播回放助手

面向 **雨课堂（yuketang.cn）「智·汇大讲堂」** 一类「直播课堂 + 回放」课程的 **Playwright** 本地脚本：自动找出「未观看回放」的课堂，在浏览器中静音倍速播放，尽量把有效观看进度刷到配置阈值（默认总时长的 **65%**，对应平台常见「≥60% 有效」）。

不同用户只需填写**自己的** `classroom_id` / 学习日志 URL，并使用**自己的**账号登录即可，仓库内不包含任何个人账号或课堂绑定信息。

> **重要**：使用前请阅读 [DISCLAIMER.md](./DISCLAIMER.md)。运行本程序即视为接受免责声明。  
> **风险**：可能违反平台或学校规定，账号与成绩后果自负。  
> **范围**：只做「观看回放」；**不**伪造签到、**不**做测验/作业、**不**协议层伪造。

---

## 适用场景

| 适合 | 不适合 |
|------|--------|
| 智·汇大讲堂等「学习日志」里有直播/回放条目的课 | 普通课件章节树 + leaf 视频为主的课 |
| 本人账号、本机运行 | 代刷、多账号、有偿刷课 |
| 目标是补「未观看回放」 | 想改签到/考试成绩 |

日志常见状态：`缺勤` / `已签到` / `未观看回放` / `已观看回放`。本脚本只影响**回放观看**相关状态。

---

## 环境要求

- Windows / macOS / Linux
- Python 3.10+
- 可访问 `www.yuketang.cn`

```bash
cd yuketang-auto
pip install -r requirements.txt
python -m playwright install chromium
```

---

## 快速开始

### 1. 配置

```bash
cp config.example.yaml config.yaml   # Windows 可用 copy
```

编辑 `config.yaml`，至少设置真实的学习日志地址，例如：

```yaml
course_url: "https://www.yuketang.cn/v2/web/studentLog/你的classroom_id"
# 或显式：
# classroom_id: 你的classroom_id
```

### 2. 如何找到 classroom_id

1. 浏览器登录雨课堂，进入该门课的 **学习日志 / 直播课堂列表**。
2. 看地址栏：
   - 桌面端：`.../v2/web/studentLog/<classroom_id>`
   - 移动端：`.../logs/<course_id>/<classroom_id>` → **取第二段**为 `classroom_id`
3. 若用错成 `course_id`，页面常会 `forbidden`，请改回 `classroom_id`。

### 3. 运行

```bash
# 只列出「未观看回放」（首次建议先 list，确认登录与 ID 正确）
python main.py --list-only

# 先处理一节（有界面，便于扫码登录）
python main.py --once --headed

# 按配置处理待办
python main.py
```

首次运行会打开浏览器；请在超时时间内完成登录。登录态保存在本机 `data/storage_state.json`（已 gitignore）。

---

## 命令行参数

| 参数 | 含义 |
|------|------|
| `--config PATH` | 指定配置文件（默认 `config.yaml`） |
| `--list-only` | 只列出未观看回放 |
| `--once` | 只处理一节 |
| `--max N` | 最多 N 节 |
| `--rate` / `--speed` | **自定义倍速**（见下） |
| `--list-rates` | 列出倍速预设 |
| `--headed` / `--headless` | 有/无界面 |

---

## 自定义倍速

优先级：**命令行 > `config.yaml` > 默认 1.25x**。

```bash
# 配置文件
# playback_rate: 1.5

# 命令行（覆盖配置）
python main.py --rate 1.5
python main.py --speed 2x
python main.py --rate normal          # 预设 = 1.0
python main.py --list-rates           # 查看预设

# 示例：稳妥 1.0x 跑一节
python main.py --once --headed --rate 1.0
```

| 配置项 | 说明 |
|--------|------|
| `playback_rate` | 目标倍速，如 `1.25`、`"1.5x"`、`fast` |
| `playback_rate_min` / `max` | 允许区间，默认 `0.5` ~ `3.0`，超出钳制 |
| `rate` / `speed` | 与 `playback_rate` 等价的别名 |

播放中若页面把倍速重置，脚本会周期性拉回目标倍速。  
**建议 1.0~1.5**：更高可能导致平台不记「已观看回放」。

---

## 配置项摘要

见 `config.example.yaml`。常用：

| 项 | 说明 |
|----|------|
| `course_url` / `classroom_id` | 课堂入口（必填其一可解析出 ID） |
| `playback_rate` | 自定义倍速，建议 ≤1.5 |
| `complete_ratio` | 有效进度比例，默认 `0.65` |
| `max_watch_sec` | 单节最长等待秒数 |
| `headless` | 是否无头模式 |

---

## 工作原理（简要）

1. 分页请求学习日志：`/v2/api/web/logs/learn/{classroom_id}`
2. 筛选 `live_viewed == false`（未观看回放）
3. 打开 `/m/v2/lesson/student/{lessonId}/overview`
4. 点击「立即播放」，静音 + 倍速，支持多段 mp4
5. 累计观看至 `complete_ratio`，或检测到「已观看回放」/ `finishReplay` / `live_viewed`

---

## 目录结构

```
yuketang-auto/
  main.py                 # 入口
  config.example.yaml     # 配置模板（可提交）
  config.yaml             # 你的本地配置（勿提交）
  DISCLAIMER.md           # 免责声明
  requirements.txt
  yuketang/
    urls.py               # URL / classroom_id 解析
    logs.py               # 学习日志 API
    replay.py             # 回放播放闭环
    login.py / browser.py / progress.py / selectors.py
  data/                   # 登录态、断点、失败记录（勿提交）
  scripts/dump_page.py    # 调试：导出页面
```

---

## 故障排查

| 现象 | 建议 |
|------|------|
| forbidden / 打不开日志 | 检查是否误用 `course_id`，应使用 `classroom_id` |
| 列表为空但网页有未看 | 重新登录；确认 URL 对应正确课堂 |
| 进度不涨 | 降低倍速（如 `--rate 1.0`）；有界面观察是否被弹窗打断 |
| 本地达标但日志仍「未观看」 | 提高 `complete_ratio` 或再跑一节；以平台为准 |
| 登录超时 | `--headed`，加大 `wait_login_timeout_sec` |

失败截图默认：`data/fail_replay_*.png`。选择器可调：`yuketang/selectors.py`。

---

## 免责声明

详见 **[DISCLAIMER.md](./DISCLAIMER.md)**。

本项目与雨课堂官方无关；作者不对账号、成绩、纪律或任何损失负责。请仅在合法、合规、本人账号前提下使用。
