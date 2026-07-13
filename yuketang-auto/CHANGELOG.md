# Changelog

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
