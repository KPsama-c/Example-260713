# 雨课堂 · 本地学习进度助手（yuketang-auto）

面向雨课堂「智·汇大讲堂」类**直播回放**的 **Playwright 本机助手**：解析待办/未观看列表，按规则推进本地观看进度（默认有效进度约总时长 **65%**），并提供本机 Web 控制面。

> **作品集表述**：浏览器自动化编排 + 多配置档 + 断点隔离 + 本机 API/Web（默认 `127.0.0.1`）。可作「运维向工具 / 本机控制面」项目说明；**勿**以对抗平台规则为卖点。  
> **免责声明**：[DISCLAIMER.md](./DISCLAIMER.md) · 非官方 · 仅限本人账号 · 风险自负  
> **范围**：观看回放为主；默认不跳播、不 API 签到、不答题  
> **版本**：0.9.4

---

## 5 分钟上手

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 方式 A：网页控制台（推荐）

```bash
python webapp.py
# 浏览器打开 http://127.0.0.1:8765
```

Windows 可双击 `start_web.bat`（缺依赖时会自动 `pip` + 安装 Chromium）。

1. 填写 `classroom_id` 或学习日志 URL（或 **刷新我的班级** 点选）  
2. 勾选 **免责声明** → **保存**（自动写入配置档）  
3. **刷新待办** / **下一节** / **全部** / **观看勾选**  

多门课：保存后出现在 **配置档** 下拉，可切换/删除；断点按 `classroom:lesson` 隔离。  
页面下方可看 **最近运行** 摘要（仅计数，无课程标题）。  
默认只绑本机 `127.0.0.1`，**请勿**改成 `0.0.0.0` 暴露公网。

### 方式 B：终端

```bash
python main.py
# 或
python main.py --id 你的classroom_id --list-only --headed
```

交互菜单 **[8]** 可管理配置档（列出 / 切换 / 保存 / 删除），与 Web 下拉一致。

---

## 高标准行为（红线）

| 规则 | 说明 |
|------|------|
| 不伪造 | 不伪造观看心跳、不协议改签到/答题 |
| 仅平台确认写断点 | 默认 `require_platform_confirm`；本地达线后 grace + soft_boost 真播 |
| 播放中不跳日志页 | 完成检查不 `page.goto` 学习日志 |
| 仅本机自用 | Cookie / 断点在 `data/`，永不进仓、不进纯净包 |

### 能力边界（v0.9.3+）

| 配置 | 默认 | 含义 |
|------|------|------|
| `resume_partial` | true | 续播：seek 到本机已观测进度 |
| `allow_skip_ahead` | **false** | 达线后真 seek 到片尾前 `tail_seek_sec` |
| `allow_checkin_assist` | **false** | 同上（不改签到 API；不保证「已签到」） |
| `tail_seek_sec` | 90 | 片尾真播秒数（硬限 30–180） |

Web 控制台「能力边界」可勾选；也可写在 `config.yaml`。开启激进项 = 风险自负。

### 全量观看 `full`（v0.9.4）

| 项 | 行为 |
|----|------|
| 列表 | 全部活动（含已观看回放），不按平台签到/回放过滤 |
| 跳过 | 仅本地 `progress` 完成 **或** soft≥`complete_ratio` |
| 续播 | 有 partial 记录才续，否则从 0 |
| 签到 | 达线后片尾真 seek，播后只读观测；**不保证**变已签到 |

入口：Web **全量观看** · 菜单 **[f]** · `python main.py --full`

断点键：`classroom_id:lesson_id`。SOFT 记入 `data/soft.json`，对账后转正。  
**全部观看**：仅当 `soft.json` 明确记录本地已播到 ≥`complete_ratio`（0→阈值跑完）才跳过；无记录或不达标 → 重看/续看。  
未看完中断写入 `data/partial.json`，下次**真 seek 续播**。补平台确认用「仅 SOFT 再跑」。  
建议倍速 **≤1.5**；过高易导致平台不认、SOFT 增多。

---

## 观看范围与配置档

- **全部 / 仅缺勤 / 仅已签到**（均只处理「未观看回放」）  
- **观看勾选**：只跑待办里勾中的节  
- **配置档**（v0.8.1+）：`profiles` + `active_profile`，Web 下拉切换；点选班级即 upsert  

```yaml
# config.example.yaml 片段
profiles:
  - name: 课A
    classroom_id: "YOUR_CLASSROOM_ID"
    course_url: "https://www.yuketang.cn/v2/web/studentLog/YOUR_CLASSROOM_ID"
active_profile: 课A
```

> **常见坑**：地址里若只有 `/logs/YOUR_COURSE_ID` 一段数字，那往往是 **course_id**。  
> 真正的 classroom_id 在「我的班级」或 `studentLog/` 后面。

---

## 命令行（可选）

```bash
python main.py --id 你的classroom_id --list-only --headed
python main.py --url "https://www.yuketang.cn/v2/web/studentLog/xxx" --once
python main.py --profile 课A --soft-only
python main.py --doctor
python main.py --setup
python main.py --rate 1.5
python main.py --list-rates
```

| 参数 | 含义 |
|------|------|
| `--id` / `--url` | 课堂 ID 或学习日志 URL |
| `--profile` | 激活配置档（name 或 classroom_id） |
| `--soft-only` | 仅重试 SOFT 节 |
| `--doctor` | 本机环境自检 |
| `--list-only` / `--once` / `--max N` | 直接动作（不进菜单） |
| `--rate` / `--speed` | 倍速 |
| `--setup` | 仅向导 |
| `--menu` / `--no-menu` | 强制/禁止菜单 |
| `--headed` / `--headless` | 有/无界面 |

终端与 Web 共用 `yuketang.jobs.run_automation`。

---

## 默认参数

| 项 | 默认 |
|----|------|
| 倍速 | 1.25（建议 ≤1.5） |
| 有效进度 | 65% 总时长（本地停播线） |
| 写断点 | 仅平台确认 |
| 登录态 | `data/storage_state.json` |
| 断点 | `data/progress.json` |
| Web | `127.0.0.1:8765` |

高级项见 `config.example.yaml`（grace、soft_boost、retry、profiles 等）。

---

## 故障矩阵

| 现象 | 处理 |
|------|------|
| forbidden / 空列表 | 用错 ID，应使用 **classroom_id**；点「刷新我的班级」 |
| 进度不涨 | `--rate 1.0` 或页面改倍速；确认有界面首次登录 |
| 登录超时 | 有界面模式；删除坏掉的 `storage_state` 后重登 |
| 本地达标但下次仍出现 | 平台未确认（SOFT）；「全部」会跳过，用「仅 SOFT 再跑」补刷 |
| 中途取消后从头播 | 确认 `resume_partial: true`；看日志是否有「续播 seek」 |
| 任务中无法切换配置档 | 先 **停止**，再切换 |
| Windows 控制台乱码 | 用 Web 控制台（推荐） |
| 依赖缺失 | `pip install -r requirements.txt` 后 `playwright install chromium` |

---

## 纯净包与隐私

```bash
python scripts/make_clean_zip.py
# → yuketang-auto-vX.Y.Z-clean.zip
```

包内**不含**：`config.yaml`、`data/*` Cookie/断点、`storage_state.json`。  
Git 忽略同上；分享代码只用 clean zip 或本仓库，勿打包 `data/`。

**安全自检**

- [ ] Web 仅 `127.0.0.1`（`webapp.py` 对非本机 host 会告警）  
- [ ] `.gitignore` 含 `config.yaml`、`data/*`、`storage_state.json`  
- [ ] clean zip 检漏通过（脚本自动查 config/storage/progress）  
- [ ] 未把个人课堂 ID 写进对外文档  

---

## 开发与测试

```bash
pip install -e ".[dev]"   # 或 pip install pytest ruff
python scripts/smoke_local.py   # doctor + pytest + 关键 import（不连业务）
pytest -q
ruff check yuketang tests webapp.py main.py
```

本机回归清单（发布前）：
1. `python scripts/smoke_local.py` 全绿  
2. `python main.py --doctor`  
3. （可选，有登录态）`python main.py --list-only --headed` 确认列表前对账  
4. 红线：仅平台确认 `mark_done`；不可跳播伪造进度  

GitHub Actions：push / PR 时在 `yuketang-auto/` 下跑 pytest（无私钥、不连真站）。

---

## 目录

```
webapp.py               # 网页控制台
main.py                 # 终端向导 + CLI
start_web.bat           # Windows 一键
webui/templates/        # 网页模板
yuketang/
  jobs.py               # run_automation + re-export（Web/CLI 入口）
  job_state.py          # JobState / STATE
  pending_ops.py        # 对账 / 待办 / soft / 时长
  watch_batch.py        # 共享观看循环
  settings.py           # 配置 + profiles
  progress.py / replay.py / logs.py
tests/                  # 不依赖真站的单测
scripts/smoke_local.py  # 本机烟雾
scripts/make_clean_zip.py
config.example.yaml
DISCLAIMER.md  LICENSE  CHANGELOG.md
data/                   # 本地隐私（勿提交）
```

---

## 免责

详见 [DISCLAIMER.md](./DISCLAIMER.md)。与雨课堂官方无关。
