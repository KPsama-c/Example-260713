# yinghua-auto · 验收清单（v0.1.0 · M1）

> 可给第三方按勾选验收。真机数字会随账号/课程变化；下表「示例快照」仅作对照，不是永久保证。  
> 风险与使用边界见 [DISCLAIMER.md](DISCLAIMER.md)。

## A. 安装与隐私

- [ ] `pip install -r requirements.txt`
- [ ] `python -m playwright install chromium`
- [ ] `copy config.example.yaml config.yaml`（或 `python main.py --setup`）并填写 `base_url`
- [ ] `.gitignore` 覆盖：`config.yaml`、`data/*`（保留 `.gitkeep`）、`debug/`、`.env`
- [ ] `git status` **不出现**：`config.yaml`、`storage_state.json`、Cookie、API Key、`debug/*` 截图

## B. M1 功能

### B1. 登录

- [ ] `python main.py --login` 打开浏览器，人工登录成功
- [ ] 写出 `data/storage_state.json`（本地，已 gitignore）

### B2. 列表（list）

- [ ] `python main.py --list-only` 或 `python nfctl.py list` 能解析课时链接（优先 `/user/node`）
- [ ] 状态列「已学」的课时 **不进入待办**（避免误匹配「完成时间」列）
- [ ] 多页目录时分页累加（示例快照：某课共 24 节、待办 0 全已学）

### B3. 播放（once / next）

- [ ] **条件**：配置指向 **进行中** 课程（兴趣学习 `kind=run` 或未结束课）
- [ ] `python main.py --once` 或 `python nfctl.py next` 能进入节点并驱动播放器
- [ ] **已结束课**：平台提示「课程已经结束」等 → 日志说明拦截，**不崩溃、不强行伪造进度**

### B4. 控制面

- [ ] `python nfctl.py status` 可输出状态
- [ ] `python webapp.py` 仅绑定 `127.0.0.1:8766`（或配置的本机口）
- [ ] 浏览器访问 `http://127.0.0.1:8766`，`GET /api/status` 返回 200

## C. 回归（可选）

- [ ] 若仓库含 `tests/`：`pytest` 通过（urls / 进度键 / 文案解析等无浏览器冒烟）
- [ ] 无测时：本栏 N/A，**M1 以真机 B 节为准**

## D. 发布门禁

- [ ] 版本一致：`yinghua/__init__.py`、`pyproject.toml`、README 均为 `0.1.0`
- [ ] 发布说明可见：[RELEASE_NOTES_v0.1.0.md](RELEASE_NOTES_v0.1.0.md)
- [ ] 首包 commit **不含** 隐私文件（`git show --stat` 复核）
- [ ] remote 已配置且 `git push` 成功，**或** 已同步至 `gitdb/yinghua-auto` 并随 Example-260713 推送
- [ ] 公开仓库入口可点开 DISCLAIMER

## 示例快照（2026-07-13 联调，账号相关）

| 项 | 结果 |
|----|------|
| 登录 | OK |
| list | 解析 node；分页累计约 24 节；待办 0（全已学） |
| once | 本账号该课「已结束」→ 平台拦截（预期边界） |

换课 / 换账号后请重跑 B 节勾选。
