"""
选择器集中管理（userscript v4.1 + 常见英华 DOM）。
失败时优先改这里，再 --once --headed 验证。
"""

# ---------- 登录 ----------
LOGIN_HINTS = [
    "text=登录",
    "text=立即登录",
    "text=请登录",
    "text=账号登录",
    "input[type='password']",
    "input[name='password']",
    "input[name='username']",
    "input[name='account']",
    '[class*="login"] button',
    "form[action*='login']",
]

LOGGED_IN_HINTS = [
    '[class*="avatar"]',
    '[class*="user-info"]',
    '[class*="userInfo"]',
    "text=退出",
    "text=退出登录",
    "text=个人中心",
    "text=我的课程",
    "text=学习记录",
    "a[href*='study_record']",
    "a[href*='course-study']",
    "img[class*='avatar']",
]

# ---------- 课程 / 章节（catalog + course 共用）----------
# 真站（超贤/英华）：.user-course .item 课程卡；视频 a[href*='/user/node']
SIDEBAR = (
    ".detmain-navlist, .course-sidebar, .section-list, .detmain-navs, "
    "[class*='sidebar'], [class*='catalog'], .stuelearn"
)
COURSE_LINKS = (
    'a[href*="/user/node"], a[href*="nodeId="], a[target="_self"], '
    ".section-item, .section-item a, a[href*='node']"
)
SECTION_TITLE = ".section-title, .section-header, .title, .name, span"
VIDEO_TAB_TEXT = "视频"
# 课程列表卡片
COURSE_CARD = ".user-course .item, .user-course .box, [class*='user-course'] .item"
COURSE_CARD_LINK = (
    ".user-course .item a[href*='courseId'], "
    ".user-course .name a[href*='course'], "
    "a[href*='/user/course?courseId=']"
)
COURSE_STUDY_RECORD_LINK = "a[href*='/user/study_record']"
# 视频记录表
RECORD_ROWS = (
    "#list tbody tr, table.yee-datatable tbody tr, "
    ".stuelearn-table tbody tr, .study-record tr, .study_record tr, "
    "table tr, .record-list .item, [class*='study-record'] tr, [class*='study_record'] li"
)
PROGRESS_CELL = ".progress, [class*='progress'], .col-progress, td.progress, .txt"

SECTION_LINK_CANDIDATES = [
    # 真站：学习记录 / 课程目录 的节点
    "a[href*='/user/node']",
    "a[href*='nodeId=']",
    "a[href*='/node?']",
    "a[href*='course-study']",
    "table a[href*='node']",
    ".section-item a",
    ".section-item",
    "[class*='section-item'] a",
    "[class*='sectionItem'] a",
    "[class*='chapter'] a",
    "[class*='catalog'] a",
    "[class*='node'] a",
    ".course-menu a",
    "#courseMenu a",
    "a[target=_self]",
    "ul.layui-nav a",
]

# 我的课程卡片（兴趣学习 / 院校课程列表）
COURSE_CARD_LINKS = [
    "a[href*='/user/course?courseId=']",
    "a[href*='/user/course?']",
    "a[href*='study_record?courseId=']",
    "a[href*='/user/course/']",
]

# 个人中心导航，勿当视频课时
NAV_TITLE_BLACKLIST = [
    "学习时长",
    "我的课程",
    "我的互评",
    "乐学圈",
    "讨论主题",
    "个人中心",
    "个人设置",
    "退出登录",
    "首页",
    "全部课程",
    "公开课",
    "教学新闻",
    "课程说明",
    "新手指南",
    "帮助中心",
    "联系我们",
    "下载APP",
    "问题答疑",
    "课程直播间",
    "兴趣学习",
    "院校课程",
    "已结束",
    "报名中",
    "上一页",
    "下一页",
    "成绩策略",
    "视频记录",
    "作业记录",
    "考试记录",
    "讨论记录",
    "课程介绍",
    "课程目录",
    "教师团队",
    "课程公告",
    "课程资料",
    "讨论板块",
    "学习成绩",
]

SECTION_ROW_CANDIDATES = [
    ".section-item",
    "[class*='section-item']",
    "[class*='sectionItem']",
    "[class*='chapter-item']",
    "[class*='node-item']",
    "li[class*='section']",
    ".layui-tree-set",
]

# 勿单独用「完成」——会误匹配表头「完成时间」
DONE_HINTS = ["已完成", "已学完", "已学", "100%", "已看完"]
UNDONE_HINTS = ["未完成", "未学完", "未学", "进行中", "未开始", "待学习", "尚未学习"]
VIDEO_TYPE_HINTS = ["视频", "录像", "微课", "课件视频", "video"]
NON_VIDEO_HINTS = ["作业", "考试", "测验", "讨论", "图文", "问卷", "文档", "PDF"]

# ---------- 播放器 ----------
VIDEO = "video, .video-player video, video.vjs-tech, .vjs-tech"
VIDEO_SELECTORS = [
    "video",
    ".video-player video",
    ".video-player",
    "[class*='video-player'] video",
    "[class*='player'] video",
    "video.vjs-tech",
    ".vjs-tech",
]

PLAY_BUTTON_CANDIDATES = [
    "button.vjs-big-play-button",
    ".vjs-big-play-button",
    "[class*='play-btn']",
    "[class*='playBtn']",
    "button[aria-label*='播放']",
    "button[title*='播放']",
    "text=播放",
    "text=开始学习",
    "text=继续学习",
]

# ---------- 验证码（layui layer）----------
CAPTCHA_LAYER = ".layui-layer-content"
CAPTCHA_IMG = ".layui-layer-content img"
CAPTCHA_INPUT = ".layui-layer-content input"
CAPTCHA_OK = ".layui-layer-btn0"
CAPTCHA_CANDIDATES = [
    ".layui-layer-content",
    ".layui-layer",
    "[class*='captcha']",
    "[class*='verify']",
    "text=请输入验证码",
    "text=验证码",
]

# ---------- 考试（M2 stub）----------
EXAM_MAIN = ".courseexamcon-main"
EXAM_TAB = "#topic-tab-"  # + N
EXAM_URL_HINTS = ["/user/exam", "exam"]

# ---------- 弹窗 ----------
DISMISS_CANDIDATES = [
    "text=知道了",
    "text=我知道了",
    "text=继续学习",
    "text=开始学习",
    "text=关闭",
    "text=确定",
    "text=跳过",
    "text=取消",
    "[class*='close']",
    "[aria-label='Close']",
    ".layui-layer-close",
    ".layui-layer-setwin .layui-layer-close1",
    ".el-dialog__headerbtn",
    ".ant-modal-close",
]

# ---------- 路由片段 ----------
PATH_STUDY_RECORD = ["/user/study_record", "/student/course-study-record", "study_record"]
PATH_VIDEO_NODE = ["/node", "/student/course-study", "course-study"]
