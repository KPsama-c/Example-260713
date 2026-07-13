"""
选择器集中管理。雨课堂前端版本多，失败时优先改这里，再 --once 验证。

调试流程：
  1. python main.py --once --headed
  2. 失败后看 data/ 截图 与 debug/ 导出
  3. 调整下方选择器
"""

# ---------- 登录 ----------
# 出现这些文案/按钮之一，多半未登录
LOGIN_HINTS = [
    "text=登录",
    "text=扫码登录",
    "text=手机号登录",
    "text=请登录",
    '[class*="login"] button',
]

# 登录成功后常见元素（任一命中即可）
LOGGED_IN_HINTS = [
    '[class*="avatar"]',
    '[class*="user-info"]',
    '[class*="userInfo"]',
    'text=学习空间',
    'text=我的课程',
    'text=退出登录',
    'img[class*="avatar"]',
]

# ---------- 课程目录 / 叶子节点 ----------
# 宽匹配：按优先级尝试
LEAF_ROW_CANDIDATES = [
    # 桌面端常见
    '[class*="leaf-detail"]',
    '[class*="leafDetail"]',
    '[class*="leaf-title"]',
    '[class*="section-list"] [class*="leaf"]',
    '[class*="chapter"] [class*="leaf"]',
    '.leaf-title',
    '[class*="activity-item"]',
    '[class*="study-list"] li',
    '[class*="unit-item"]',
    # 移动端 /m/v2/course/normalcourse/logs
    '[class*="log-list"] [class*="log-item"]',
    '[class*="logList"] [class*="logItem"]',
    '[class*="course-log"] li',
    '[class*="courseLog"] li',
    '[class*="logs-"] [class*="item"]',
    'div[class*="leaf"]',
    'li[class*="leaf"]',
    '[class*="catalog"] [class*="item"]',
    '[class*="content-item"]',
    '[class*="contentItem"]',
]

# 视频类型提示（标题旁图标/文案）
VIDEO_TYPE_HINTS = ["视频", "video", "录像", "微课"]

# 非视频（跳过）
NON_VIDEO_HINTS = ["作业", "考试", "测验", "讨论", "图文", "课件", "问卷", "直播", "PPT"]

# 完成态
DONE_HINTS = ["已完成", "完成", "100%", "已学完"]
UNDONE_HINTS = ["未完成", "未学", "进行中", "未开始"]

# ---------- 播放器 ----------
VIDEO_SELECTORS = [
    "video",
    "video.vjs-tech",
    ".vjs-tech",
    '[class*="player"] video',
    '[class*="video-player"] video',
    "iframe",  # 特殊处理：进入 frame 再找 video
]

PLAY_BUTTON_CANDIDATES = [
    "button.vjs-big-play-button",
    ".vjs-big-play-button",
    '[class*="play-btn"]',
    '[class*="playBtn"]',
    'button[aria-label*="播放"]',
    'button[title*="播放"]',
    "text=播放",
    "text=开始学习",
    "text=继续学习",
]

# ---------- 智汇大讲堂 / 直播回放 ----------
STUDENT_CARD = ".studentCard"
ACTIVITY_INFO = ".activity-info"
STATISTICS_BOX = ".statistics-box"

REPLAY_PLAY_CANDIDATES = [
    "text=立即播放",
    ".play-btn",
    ".playback-overlay",
    ".video-play",
    ".fix-play-txt",
    '[class*="play-btn"]',
    '[class*="playback-overlay"]',
    "text=从这一页播放",
]

REPLAY_DONE_HINTS = ["已观看回放"]
REPLAY_UNDONE_HINTS = ["未观看回放", "缺勤"]


# ---------- 弹窗关闭 ----------
DISMISS_CANDIDATES = [
    "text=知道了",
    "text=我知道了",
    "text=继续学习",
    "text=开始学习",
    "text=关闭",
    "text=确定",
    "text=跳过",
    '[class*="close"]',
    '[aria-label="Close"]',
    ".el-dialog__headerbtn",
    ".ant-modal-close",
]
