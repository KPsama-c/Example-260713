"""班级列表与 classroom_id 解析。

用户常把 course.id 误当成 classroom_id（例如智·汇大讲堂）。
通过 /v2/api/web/courses/list 可拿到正确映射。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from playwright.sync_api import Page


@dataclass
class ClassroomInfo:
    classroom_id: str
    course_id: str | None
    name: str
    course_name: str
    teacher: str

    def label(self) -> str:
        cn = self.course_name or self.name or "(未命名)"
        if self.name and self.name != cn:
            return f"{self.name} · {cn}"
        return cn


@dataclass
class FetchRoomsResult:
    """班级列表拉取结果；auth_failed 时 rooms 为空且不应显示「共 0 个班级」。"""

    rooms: list[ClassroomInfo]
    ok: bool = True
    auth_failed: bool = False
    error: str | None = None
    raw_errcode: Any = None


def _page_fetch_json(page: Page, path: str) -> dict[str, Any] | None:
    data = page.evaluate(
        """async (path) => {
            try {
              const r = await fetch(path, {
                credentials: 'include',
                headers: { 'Accept': 'application/json, text/plain, */*' },
              });
              const text = await r.text();
              try { return JSON.parse(text); } catch (e) { return null; }
            } catch (e) { return null; }
        }""",
        path,
    )
    return data if isinstance(data, dict) else None


def is_auth_error_payload(payload: dict[str, Any] | None) -> bool:
    """识别雨课堂未登录 / session 失效类响应。"""
    if not payload:
        return False
    err = payload.get("errcode", payload.get("code"))
    errmsg = str(payload.get("errmsg") or payload.get("msg") or "").lower()
    # 401002 Cookie has no sessionid；其它 401xxx / UNAUTHENTICATED
    if err in (401002, "401002", 401, "401", 401001, "401001"):
        return True
    if err in (50000, "50000") and "unauth" in errmsg:
        return True
    if "sessionid" in errmsg or "unauth" in errmsg or "not login" in errmsg:
        return True
    raw_msg = str(payload.get("errmsg") or payload.get("msg") or "")
    if "未登录" in raw_msg or ("登录" in raw_msg and "失败" in raw_msg):
        return True
    return False


def auth_error_user_message(detail: str | None = None) -> str:
    base = (
        "登录已失效或未完成（缺少 sessionid）。"
        "请取消勾选「无头模式」，填写课堂后点「刷新待办」，在弹出浏览器中重新登录雨课堂；"
        "登录成功后再点「刷新我的班级」。"
    )
    if detail:
        return f"{base}（{detail}）"
    return base


def _parse_room_list(raw_list: list[Any]) -> list[ClassroomInfo]:
    out: list[ClassroomInfo] = []
    seen: set[str] = set()
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        cid = item.get("classroom_id")
        if cid is None:
            continue
        cid_s = str(cid).strip()
        if not cid_s or cid_s in seen:
            continue
        seen.add(cid_s)
        course = item.get("course") if isinstance(item.get("course"), dict) else {}
        teacher = item.get("teacher") if isinstance(item.get("teacher"), dict) else {}
        course_id = course.get("id")
        out.append(
            ClassroomInfo(
                classroom_id=cid_s,
                course_id=str(course_id) if course_id is not None else None,
                name=str(item.get("name") or "").strip(),
                course_name=str(course.get("name") or "").strip(),
                teacher=str(teacher.get("name") or "").strip(),
            )
        )
    return out


def fetch_joined_classrooms_detailed(
    page: Page,
    *,
    log: Callable[[str], None] = print,
) -> FetchRoomsResult:
    """拉取班级列表，并区分「未登录」与「真的 0 个班」。"""
    # 登录页直出时 API 也会 401，先记 URL 辅助判断
    try:
        cur = (page.url or "").lower()
    except Exception:
        cur = ""
    on_login_page = (
        "login" in cur
        or ("/web/?" in cur and "next=" in cur)
        or cur.rstrip("/").endswith("/web")
    )

    payload = _page_fetch_json(page, "/v2/api/web/courses/list?identity=2")
    if not payload or (
        payload.get("errcode", 0) not in (0, "0", None)
        and not is_auth_error_payload(payload)
    ):
        # 非认证失败时再试教师身份；认证失败不必重试
        alt = _page_fetch_json(page, "/v2/api/web/courses/list?identity=1")
        if alt and (
            alt.get("errcode", 0) in (0, "0", None) or is_auth_error_payload(alt)
        ):
            payload = alt
        elif not payload:
            payload = alt

    if not payload:
        msg = "无法获取课程列表 API"
        if on_login_page:
            log(f"[classroom] {msg}（当前像登录页）")
            return FetchRoomsResult(
                rooms=[],
                ok=False,
                auth_failed=True,
                error=auth_error_user_message("页面停留在登录"),
            )
        log(f"[classroom] {msg}")
        return FetchRoomsResult(rooms=[], ok=False, error=msg)

    if is_auth_error_payload(payload):
        detail = str(payload.get("errmsg") or payload.get("msg") or payload.get("errcode") or "")
        log(f"[classroom] 认证失败: {detail}")
        return FetchRoomsResult(
            rooms=[],
            ok=False,
            auth_failed=True,
            error=auth_error_user_message(detail or None),
            raw_errcode=payload.get("errcode", payload.get("code")),
        )

    err = payload.get("errcode", payload.get("code", 0))
    if err not in (0, "0", None):
        detail = str(payload.get("errmsg") or err)
        log(f"[classroom] 课程列表失败: {detail}")
        return FetchRoomsResult(rooms=[], ok=False, error=f"课程列表失败: {detail}", raw_errcode=err)

    raw_list = ((payload.get("data") or {}).get("list")) or []
    if not isinstance(raw_list, list):
        raw_list = []
    out = _parse_room_list(raw_list)
    log(f"[classroom] 已加入班级 {len(out)} 个")
    return FetchRoomsResult(rooms=out, ok=True)


def fetch_joined_classrooms(
    page: Page,
    *,
    log: Callable[[str], None] = print,
) -> list[ClassroomInfo]:
    """拉取当前账号已加入的班级（学生身份）。兼容旧调用；认证失败时返回 []。"""
    return fetch_joined_classrooms_detailed(page, log=log).rooms


def resolve_classroom_id(
    page: Page,
    raw_id: str,
    *,
    log: Callable[[str], None] = print,
    classrooms: list[ClassroomInfo] | None = None,
) -> tuple[str | None, list[ClassroomInfo], str]:
    """把用户输入解析为可用的 classroom_id。

    返回 (classroom_id|None, 班级列表, 说明信息)。
    - 输入已是 classroom_id → 原样返回
    - 输入是 course_id → 映射到 classroom_id
    - 无法解析 → classroom_id 为 None，message 含候选提示
    """
    raw = str(raw_id or "").strip()
    rooms = classrooms if classrooms is not None else fetch_joined_classrooms(page, log=log)

    if not raw:
        return None, rooms, "未提供 classroom_id"

    # 1) 直接命中 classroom_id
    for r in rooms:
        if r.classroom_id == raw:
            log(f"[classroom] 确认 classroom_id={raw}（{r.label()}）")
            return raw, rooms, f"已确认: {r.label()}"

    # 2) 命中 course_id → 换 classroom_id
    matches = [r for r in rooms if r.course_id == raw]
    if len(matches) == 1:
        r = matches[0]
        log(
            f"[classroom] 输入 {raw} 是 course_id，已自动转换为 "
            f"classroom_id={r.classroom_id}（{r.label()}）"
        )
        return r.classroom_id, rooms, f"已从 course_id 转换: {r.label()}"
    if len(matches) > 1:
        # 罕见：同一 course 多个班
        lines = "、".join(f"{m.classroom_id}({m.label()})" for m in matches)
        log(f"[classroom] course_id={raw} 对应多个班级: {lines}")
        return None, rooms, f"course_id={raw} 对应多个班级，请手动选择: {lines}"

    # 3) 不在列表：仍可能是合法 classroom_id（列表 API 不完整时）
    if raw.isdigit() and len(raw) >= 5:
        log(
            f"[classroom] 警告: {raw} 不在已加入列表中；"
            "将原样尝试（若失败请核对是否填成了 course_id）"
        )
        hint = _format_room_hints(rooms)
        return raw, rooms, f"未在列表中匹配，将原样使用 {raw}。" + hint

    return None, rooms, "无效的 classroom_id。" + _format_room_hints(rooms)


def _format_room_hints(rooms: list[ClassroomInfo], limit: int = 8) -> str:
    if not rooms:
        return " 当前账号未拉取到任何班级。"
    parts = [
        f"{r.classroom_id}={r.label()}"
        + (f"(course={r.course_id})" if r.course_id else "")
        for r in rooms[:limit]
    ]
    more = f" 等{len(rooms)}个" if len(rooms) > limit else ""
    return " 可选: " + "; ".join(parts) + more


def rooms_to_dicts(rooms: list[ClassroomInfo]) -> list[dict[str, Any]]:
    return [
        {
            "classroom_id": r.classroom_id,
            "course_id": r.course_id,
            "name": r.name,
            "course_name": r.course_name,
            "teacher": r.teacher,
            "label": r.label(),
        }
        for r in rooms
    ]
