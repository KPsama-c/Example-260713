# NarraFork AI 管理说明

本机项目：`E:\ai\20-项目代码\yinghua-auto`  
仅限本机操作；**禁止**将 Cookie / API Key 写入知识库或公开仓库。

## 安装（一次）

```bash
cd "E:/ai/20-项目代码/yinghua-auto"
pip install -r requirements.txt
python -m playwright install chromium
cp config.example.yaml config.yaml   # Windows 可用 copy
```

编辑 `config.yaml` 中的 `base_url`、可选 `course_id` / `course_url`  
（推荐 `.../user/study_record/video?courseId=`）。

**禁止提交**：`config.yaml`、`data/storage_state.json`、`debug/`、密钥。

## 常用命令

| 意图 | 命令 |
|------|------|
| 首次登录（有界面） | `python main.py --login` |
| 终端菜单 | `python main.py` |
| 列待办/目录 | `python nfctl.py list` |
| 刷下一节 | `python nfctl.py next` |
| 连续刷 | `python nfctl.py all` |
| 状态 | `python nfctl.py status` |
| 停止 | `python nfctl.py stop` |
| Web 控制台 | `python webapp.py` → http://127.0.0.1:8766 |

## HTTP API（仅 127.0.0.1）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 任务状态 + 最近日志 |
| GET | `/api/pending` | 当前页可解析的章节列表（尽力） |
| POST | `/api/jobs` | body: `{"action":"list\|next\|all\|stop"}`（Web 建议带 `accept_risk: true`） |
| GET/POST | `/api/settings` | 非密钥配置（禁止写 api_key） |
| POST | `/api/clear-progress` | 清空断点 |
| POST | `/api/clear-failed` | 清空失败列表 |

同步 CLI 也可用：`python nfctl.py list`（默认同步跑完）；后台：`python nfctl.py next --async`。

## 协作约定

- **@ai_2**：规划、验收、风险边界  
- **@ai_3**：实现与调试  
- 用户：本人账号、真实 `base_url`、可选 LLM Key  

## 功能边界（提醒 AI）

- 默认不做考试自动交卷  
- 不代刷多账号  
- 不暴露服务到 `0.0.0.0`  
