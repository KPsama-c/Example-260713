# 英华学堂本地助手（yinghua-auto）

非官方 · 仅限**本人账号**自用 · 风险见 [DISCLAIMER.md](DISCLAIMER.md)

Playwright 本地自动化，对齐 `yuketang-auto` 结构：登录态、列课、刷视频、断点续跑、本机 Web（8766）、`nfctl` 给 NarraFork 调用。

## 快速开始

```bash
cd E:\ai\20-项目代码\yinghua-auto
pip install -r requirements.txt
python -m playwright install chromium

# 复制配置并填写 base_url
copy config.example.yaml config.yaml

python main.py --login          # 浏览器内登录，保存 data/storage_state.json
python main.py --list-only      # 列出待学视频
python main.py --once           # 只播一节
python nfctl.py status
python webapp.py                # http://127.0.0.1:8766
```

## 目录

| 路径 | 说明 |
|------|------|
| `yinghua/` | 核心库：browser / login / course / player / jobs … |
| `data/` | Cookie、进度（gitignore） |
| `vendor/` | 可放完整 userscript（gitignore） |
| `webui/` | Web 控制台模板 |
| `nfctl.py` | 给 AI/脚本用的 CLI |
| `NARRAFORK.md` | NarraFork 管理说明 |

## 配置要点

- `base_url`：学校站点根（各校域名不同）
- `course_id`：可选，锁定单门课
- `course_url`：可选；推荐视频记录页  
  `https://{base}/user/study_record/video?courseId={id}`  
  或兴趣学习：`/user/index/open?kind=run`（进行中）/ `kind=finish`（已结束）
- 节点播放：`/user/node?courseId=&chapterId=&nodeId=`
- `exam.enabled` / `exam.auto_submit`：默认 **false**
- 密钥只写本地 `config.yaml` 或环境变量 `YINGHUA_LLM_API_KEY`，**勿提交 git**
- Web 仅允许 `127.0.0.1` / `localhost` / `::1`

## 真站联调（M1）

1. `python main.py --login` → 保存 `data/storage_state.json`（已 gitignore）
2. `python main.py --list-only` → 应解析 `/user/node` 课时；「已学」不进待办
3. `python main.py --once` → 需**进行中**课程；平台对「已结束」课会拦截播放页
4. DOM 不对：`python scripts/dump_page.py --url <页面>` → 改 `yinghua/selectors.py`

**勿提交**：`config.yaml`、`data/*`、`debug/`、Cookie、API Key。

## M1 / M2

- **M1**：脚手架、登录、列表、播视频、断点、Web、nfctl
- **M2**：考试 + LLM 建议答案（默认关闭，人工确认）

## 许可与免责

见 LICENSE（若有）与 DISCLAIMER.md。使用即表示同意免责声明。
