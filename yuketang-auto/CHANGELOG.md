# Changelog

## [0.9.1] - 2026-07-13

### 跳过语义收紧
- **全部**仅跳过 `soft.json` 中 local_ratio ≥ `complete_ratio` 的节（本地已明确 0→阈值）
- `partial.json` 只用于续播，**不**作为跳过依据；无 SOFT = 不确定 → 重看/续看

## [0.9.0] - 2026-07-13

### 全部跳过 + 续播
- **全部观看**：跳过本地已达线的节；「仅 SOFT 再跑」/勾选仍会重试
- **中断续播**：`data/partial.json` 记录真实播放观测进度；取消/失败后再次打开会 seek 到上次位置（非伪造心跳）
- 配置：`skip_local_complete_on_all`、`resume_partial`、`partial_file`

## [0.8.9] - 2026-07-13

### 结构
- 轻拆任务核心：`job_state` / `pending_ops` / `watch_batch`；`jobs` 保留 re-export 与 `run_automation`
- `scripts/smoke_local.py`：本机烟雾（doctor + pytest + 关键 import，不连业务）

## [0.8.8] - 2026-07-13

### 统一列表路径
- 抽出 `load_pending_for_classroom`：打开日志 → 平台对账 → 待办列表（菜单 / jobs 共用）
- 抽出 `enrich_duration_map` 供 jobs ETA 使用
- 菜单 list 前也会对账；观看结束后再对账（与 jobs 一致）
- 菜单待办显示 SOFT 标签

## [0.8.7] - 2026-07-13

### Web 可见性
- `GET /api/doctor`：本机环境自检（与 CLI `--doctor` 同源）
- `GET /api/soft`：SOFT 列表；`POST /api/soft/clear` 清除本课/全部
- 控制台：SOFT 列表区、环境自检按钮；任务结束自动刷新 SOFT

## [0.8.6] - 2026-07-13

### 修复与去重
- 修复 `run_automation` 未放行 `soft`（CLI `--soft-only` / Web 仅 SOFT 再跑 会报未知动作）
- 抽出 `watch_lesson_batch` / `select_soft_targets` / `normalize_job_action`
- 菜单观看与 jobs 共用同一循环（重试、登录失效中止、SOFT/断点语义一致）；删除 `main.run_watch_batch`

## [0.8.5] - 2026-07-13

### 运维与补刷
- 动作 `soft`：仅重试 soft.json 中仍待办的节（Web「仅 SOFT 再跑」/ 菜单 [9] / `--soft-only`）
- CLI `--profile NAME_OR_ID` 激活配置档
- CLI `--doctor` 本机环境自检（Python/依赖/Chromium/gitignore）

## [0.8.4] - 2026-07-13

### CLI / 移动端
- 终端菜单 **[8]** 配置档：列出 / 切换 / 保存当前 / 删除（与 Web 对齐）
- 设置子菜单增加配置档；换课后自动 upsert
- Web 窄屏（≤640px）：按钮全宽、双列改单列、日志区适配

## [0.8.3] - 2026-07-13

### 体验收尾
- 运行历史：`data/run_history.json` + `GET /api/history` + Web 列表（无课程标题）
- 配置档删除：`delete_profile` + `POST /api/profile/delete`
- 日志「清空显示」：`POST /api/logs/clear`（仅界面缓冲）
- 任务结束刷新历史；通知权限在结束时按需请求

## [0.8.2] - 2026-07-13

### 发布打磨
- README 重组：5 分钟上手 / 红线行为 / 故障矩阵 / 纯净包与安全自检
- GitHub Actions：`yuketang-auto` 路径下 ruff + pytest（无私钥、不连真站）
- `start_web.bat`：依赖检查与本机安全提示
- `make_clean_zip`：加强隐私文件名与占位 ID 检漏

## [0.8.1] - 2026-07-13

### 多课堂
- 配置项 `profiles` / `active_profile`：保存多门课，Web 下拉切换
- `POST /api/profile/activate`、`/api/profile/upsert`
- 保存课堂 / 点选「我的班级」自动 upsert 配置档
- 断点仍为 `classroom:lesson`，切换不清 progress

## [0.8.0] - 2026-07-13

### 可靠核心
- 断点键 `classroom_id:lesson_id`，旧裸键自动迁移，多课不串
- 默认仅平台确认才写入断点；SOFT 写入 `data/soft.json` 并对账转正
- 本地达线后 grace 继续真播 + soft_boost 抬高目标
- 列表阶段拉真实时长，批量 ETA 更准
- 单节失败可重试；登录态失效时中止并提示

### 工程
- `main` 直跑委托 `jobs.run_automation`；共享 `yuketang/util.py`
- `pytest` 单测、`pyproject.toml`、`scripts/make_clean_zip.py`
- MIT LICENSE；`start_web.bat` 一键启动

### 体验
- Web 免责勾选（可 localStorage 记住）+ 勾选待办观看
- 待办显示约时长 / SOFT 标签；任务结束醒目提示
- 跑完摘要 `data/run_history.json`（仅计数与时间）

### 红线（不变）
- 仅真实播放；播放中不跳学习日志；仅本机本人；隐私不进仓/包

## [0.6.0] - 2026-07-13

- 平台确认断点；播放中不导航；取消 / ETA；Web 控制台
