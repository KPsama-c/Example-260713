# 英华学堂 · 本地学习进度助手（yinghua-auto）

**v0.1.0 · M1** · 非官方 · 仅限**本人账号**自用 · 风险见 [DISCLAIMER.md](DISCLAIMER.md)

> **作品集表述**：基于 Playwright 的**本机浏览器自动化编排**——登录态管理、课程目录解析、学习进度状态、断点续跑、本机 Web 控制面（`127.0.0.1:8766`）与 `nfctl` CLI。结构对齐 `yuketang-auto`。  
> **不是**官方客户端；请遵守学校与平台用户协议，勿用于未授权账号或对抗性用途。

验收勾选见 [ACCEPTANCE.md](ACCEPTANCE.md) · 本版说明见 [RELEASE_NOTES_v0.1.0.md](RELEASE_NOTES_v0.1.0.md)

## 快速开始

```bash
cd E:\ai\20-项目代码\yinghua-auto
pip install -r requirements.txt
python -m playwright install chromium

# 复制配置并填写 base_url / 可选 course_id
copy config.example.yaml config.yaml

python main.py --login          # 浏览器内登录 → data/storage_state.json
python main.py --list-only      # 列出课时 / 待办
python main.py --once           # 只播一节（需进行中课程）
python nfctl.py status
python webapp.py                # http://127.0.0.1:8766
```

## 目录

| 路径 | 说明 |
|------|------|
| `yinghua/` | 核心库：browser / login / course / player / jobs … |
| `data/` | Cookie、进度（**gitignore**） |
| `vendor/` | 可放完整 userscript（gitignore） |
| `webui/` | Web 控制台模板 |
| `nfctl.py` | 给 AI / 脚本用的 CLI |
| `NARRAFORK.md` | NarraFork 管理说明 |
| `ACCEPTANCE.md` | 验收清单 |

## 配置要点

- `base_url`：学校站点根（各校域名不同）
- `course_id`：可选，锁定单门课（文档示例：`1021755`，请换成自己的课）
- `course_url`：可选；推荐视频记录页  
  `https://{base}/user/study_record/video?courseId={id}`  
  兴趣学习：`/user/index/open?kind=run`（**进行中**）/ `kind=finish`（已结束）
- 节点播放：`/user/node?courseId=&chapterId=&nodeId=`
- `exam.enabled` / `exam.auto_submit`：默认 **false**
- 密钥只写本地 `config.yaml` 或环境变量 `YINGHUA_LLM_API_KEY`，**勿提交 git**
- Web 仅允许 `127.0.0.1` / `localhost` / `::1`

## 真站联调（M1）

1. `python main.py --login` → 保存 `data/storage_state.json`（已 gitignore）
2. `python main.py --list-only` → 解析 `/user/node`；状态列「已学」**不进待办**；多页分页累加  
   （示例快照：某课约 24 节、待办 0，视账号而定）
3. `python main.py --once` → 需**进行中**课程；平台对「已结束」课会拦截播放页（预期边界，非崩溃）
4. DOM 不对：`python scripts/dump_page.py --url <页面>` → 改 `yinghua/selectors.py`

### 故障排查

| 现象 | 处理 |
|------|------|
| 列表为空或待办为 0 | 课已学完，或换 `kind=run` / 其它 `course_id`；可选清 `data/progress.json` |
| once 提示「课程已经结束」 | 换进行中课程；勿强行伪造进度 |
| 登录态失效 | 重跑 `python main.py --login` |
| 解析错行 / 已学误判 | 以表格**末列状态**为准；调 `selectors.py` / `course.py` |

**勿提交**：`config.yaml`、`data/*`、`debug/`、Cookie、API Key。

## M1 / M2

- **M1**（本版）：脚手架、登录、列表（已学过滤/分页）、播视频骨架、断点、Web、nfctl
- **M2**：考试 + LLM 建议答案（默认关闭，须人工确认）

## 许可与免责

见 LICENSE（若有）与 [DISCLAIMER.md](DISCLAIMER.md)。使用即表示同意免责声明。
