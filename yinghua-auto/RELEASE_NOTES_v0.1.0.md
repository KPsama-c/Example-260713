# Release Notes · v0.1.0（M1）

**日期**：2026-07-13  
**性质**：非官方本地助手，仅本人账号自用。详见 [DISCLAIMER.md](DISCLAIMER.md)。

## 交付内容

- Playwright 脚手架：登录态（`data/storage_state.json`）、断点进度、失败列表
- 课程列表：学习记录 / 兴趣学习入口；解析 `/user/node`；「已学」过滤；分页累加
- 视频播放骨架：`--once` / `--all`、`nfctl next|all`、本机 Web `127.0.0.1:8766`
- 配置：`config.example.yaml`（真实密钥与 Cookie **不入库**）
- 文档：README、DISCLAIMER、ACCEPTANCE、NARRAFORK

## 已知限制

| 限制 | 说明 |
|------|------|
| 已结束课程 | 平台拦截播放页，不硬播 |
| 考试（M2） | `exam.enabled` / `auto_submit` 默认 **false** |
| 验证码 | M1 以关弹窗 / 人工为主 |
| 倍速 | 过高可能不计学时，建议 1.0–1.5 |
| 多校 DOM | 各校主题差异大，需改 `yinghua/selectors.py` |

## 非目标（本版本不做）

- 多账号代刷、有偿代学
- 协议层伪造心跳 / 黑产式上报
- Web 暴露到 `0.0.0.0` 或公网
- 默认自动交卷

## 升级提示

1. `pip install -r requirements.txt && python -m playwright install chromium`
2. 对比 `config.example.yaml` 合并本地 `config.yaml`
3. 登录态失效时重跑 `python main.py --login`
