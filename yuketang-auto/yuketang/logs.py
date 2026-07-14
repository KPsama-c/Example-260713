"""学习日志 API：拉取课堂活动与未观看回放列表。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from playwright.sync_api import Page


class LogsApiError(RuntimeError):
    """学习日志 API 业务/网络错误（含可读提示）。"""


@dataclass
class LessonActivity:
    activity_id: str
    lesson_id: str  # courseware_id
    title: str
    live_viewed: bool
    attend_status: bool
    is_finished: bool
    type: int
    raw: dict[str, Any]

    @property
    def key(self) -> str:
        return str(self.lesson_id)

    @property
    def needs_replay(self) -> bool:
        """未观看回放则需要处理（签到无法补，只关心回放）。"""
        return not bool(self.live_viewed)


def _parse_activity(item: dict[str, Any]) -> LessonActivity:
    return LessonActivity(
        activity_id=str(item.get("id", "")),
        lesson_id=str(item.get("courseware_id", "")),
        title=str(item.get("title") or "").strip() or "(无标题)",
        live_viewed=bool(item.get("live_viewed")),
        attend_status=bool(item.get("attend_status")),
        is_finished=bool(item.get("is_finished")),
        type=int(item.get("type") or 0),
        raw=item,
    )


def _ykt_headers(classroom_id: str, origin: str) -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{origin.rstrip('/')}/v2/web/studentLog/{classroom_id}",
        "xtbz": "ykt",
        "xt-agent": "web",
        "classroom-id": str(classroom_id),
        "university-id": "0",
        "uv-id": "0",
    }


def _payload_ok(payload: Any) -> bool:
    """有效 logs 响应：有 data，且 errcode 成功。拒绝空 {}。"""
    if not isinstance(payload, dict) or not payload:
        return False
    if "data" not in payload:
        return False
    err = payload.get("errcode", payload.get("code", 0))
    return err in (0, "0", None)


def fetch_learn_logs_page(
    page: Page,
    classroom_id: str,
    *,
    page_idx: int = 0,
    offset: int = 20,
    actype: int = -1,
    origin: str = "https://www.yuketang.cn",
    allow_navigation: bool = True,
) -> dict[str, Any]:
    """在浏览器上下文中 fetch。

    注意：不要强行加 classroom-id 等头，页面内 fetch 带这些反而会 500；
    Cookie 足够时默认头即可。

    allow_navigation=False：禁止 page.goto 到学习日志（回放中调用时必须 False，
    否则会打断当前 overview 页）。
    """
    path = (
        f"/v2/api/web/logs/learn/{classroom_id}"
        f"?actype={actype}&page={page_idx}&offset={offset}&sort=-1"
    )
    url = f"{origin.rstrip('/')}{path}"

    # 1) 页面内 fetch（仅 Accept；500 时短暂重试）
    import json as _json

    raw: dict[str, Any] | None = None
    attempts = 2 if not allow_navigation else 4
    for attempt in range(attempts):
        raw = page.evaluate(
            """async (path) => {
                try {
                  const r = await fetch(path, {
                    credentials: 'include',
                    headers: { 'Accept': 'application/json, text/plain, */*' },
                  });
                  const text = await r.text();
                  return { ok: r.ok, status: r.status, text };
                } catch (e) {
                  return { ok: false, status: 0, text: String(e) };
                }
            }""",
            path,
        )
        if isinstance(raw, dict) and raw.get("text"):
            try:
                payload = _json.loads(raw["text"])
                if _payload_ok(payload):
                    return payload
            except Exception:
                pass
        page.wait_for_timeout(400 * (attempt + 1) if not allow_navigation else 800 * (attempt + 1))

    # 2) APIRequestContext + 雨课堂头（不导航）
    headers = _ykt_headers(classroom_id, origin)
    resp = page.request.get(url, headers=headers)
    if resp.ok:
        try:
            payload = resp.json()
            if _payload_ok(payload):
                return payload
        except Exception:
            pass

    # 3) 仅允许导航时：刷新日志页抓包
    if allow_navigation:
        captured: dict[str, Any] = {}

        def on_response(resp) -> None:  # type: ignore[no-untyped-def]
            u = resp.url
            if f"/v2/api/web/logs/learn/{classroom_id}" not in u:
                return
            if "actype=-1" not in u:
                return
            if f"page={page_idx}" not in u and page_idx != 0:
                return
            try:
                if "json" in (resp.headers.get("content-type") or ""):
                    body = resp.json()
                    if _payload_ok(body):
                        acts = ((body.get("data") or {}).get("activities")) or []
                        prev = captured.get("data")
                        prev_n = len(((prev or {}).get("data") or {}).get("activities") or [])
                        if len(acts) >= prev_n:
                            captured["data"] = body
            except Exception:
                pass

        page.on("response", on_response)
        try:
            page.goto(
                f"{origin.rstrip('/')}/v2/web/studentLog/{classroom_id}",
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(10000)
            if page_idx > 0:
                for _ in range(page_idx):
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(2500)
        finally:
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass

        if isinstance(captured.get("data"), dict) and _payload_ok(captured["data"]):
            return captured["data"]

    detail = ""
    body_text = ""
    if isinstance(raw, dict):
        body_text = str(raw.get("text") or "")
        detail = f" fetch_status={raw.get('status')} body={body_text[:200]!r}"

    if "403002" in body_text or "用户未加入班级" in body_text:
        raise LogsApiError(
            f"学习日志拒绝访问 classroom_id={classroom_id}（用户未加入班级 / 403002）。\n"
            "  常见原因：把 course_id 填成了 classroom_id。\n"
            "  处理：在网页控制台点「刷新我的班级」选择正确班级；\n"
            "  或打开学习日志页，地址栏 studentLog/ 后面才是 classroom_id。\n"
            f"  技术细节: {url}{detail}"
        )
    raise LogsApiError(
        f"logs API 失败: {url}{detail} request_status={getattr(resp, 'status', '?')}"
        + ("" if allow_navigation else " (no-nav mode)")
    )


def fetch_all_activities(
    page: Page,
    classroom_id: str,
    *,
    offset: int = 20,
    origin: str = "https://www.yuketang.cn",
    log: Callable[[str], None] = print,
    allow_navigation: bool = True,
) -> list[LessonActivity]:
    """分页拉取全部学习日志活动。"""
    all_items: list[LessonActivity] = []
    seen: set[str] = set()
    page_idx = 0
    while True:
        payload = fetch_learn_logs_page(
            page,
            classroom_id,
            page_idx=page_idx,
            offset=offset,
            origin=origin,
            allow_navigation=allow_navigation,
        )
        block = payload.get("data") or {}
        activities = block.get("activities") or []
        new_on_page = 0
        for raw in activities:
            if not isinstance(raw, dict):
                continue
            act = _parse_activity(raw)
            if not act.lesson_id or act.lesson_id in seen:
                continue
            seen.add(act.lesson_id)
            all_items.append(act)
            new_on_page += 1
        has_more = bool(block.get("has_more"))
        if not has_more and len(activities) >= offset and new_on_page > 0:
            has_more = True
        log(
            f"[logs] page={page_idx} got={len(activities)} new={new_on_page} "
            f"total={len(all_items)} has_more={has_more}"
        )
        if not activities or new_on_page == 0:
            break
        if not has_more:
            break
        page_idx += 1
        page.wait_for_timeout(600)
        if page_idx > 200:
            log("[logs] 分页过多，停止（>200）")
            break
    return all_items


def normalize_attend_filter(value: str | None) -> str:
    """all=不限签到 · absent=仅缺勤 · present=仅已签到。"""
    v = (value or "all").strip().lower()
    if v in ("all", "any", "全部", "不限", "无论"):
        return "all"
    if v in ("absent", "缺勤", "缺席", "no_attend", "unsigned"):
        return "absent"
    if v in ("present", "attended", "已签到", "签到"):
        return "present"
    return "all"


def list_pending_replays(
    page: Page,
    classroom_id: str,
    *,
    progress_keys: set[str] | None = None,
    origin: str = "https://www.yuketang.cn",
    attend_filter: str = "all",
    log: Callable[[str], None] = print,
    mode: str = "pending",
) -> list[LessonActivity]:
    """待处理活动列表。

    mode:
      - pending（默认）: 仅平台未观看回放，且不在 progress 断点中
      - full: 全量活动（含已观看回放）；不在此过滤 progress/live_viewed
        （跳过规则由上层 filter_skip_full_force 用 soft/progress 判定）

    attend_filter（仅 mode=pending 时生效）:
      - all: 未看回放，无论是否签到
      - absent: 仅缺勤且未看回放
      - present: 仅已签到且未看回放
    """
    progress_keys = progress_keys or set()
    af = normalize_attend_filter(attend_filter)
    list_mode = (mode or "pending").strip().lower()
    if list_mode in ("full", "force", "full_force", "all_lessons"):
        list_mode = "full"
    else:
        list_mode = "pending"

    items = fetch_all_activities(page, classroom_id, origin=origin, log=log)

    def _attend_ok(x: LessonActivity) -> bool:
        if af == "absent":
            return not bool(x.attend_status)
        if af == "present":
            return bool(x.attend_status)
        return True

    viewed = sum(1 for x in items if x.live_viewed)
    absent_n = sum(1 for x in items if not x.attend_status)

    if list_mode == "full":
        # 全量：有 lesson_id 的活动都进列表；跳过交给上层
        pending = [x for x in items if x.lesson_id]
        log(
            f"[logs] 全量模式：活动 {len(items)}，已观看回放 {viewed}，缺勤 {absent_n}，"
            f"列出 {len(pending)} 节（不按平台 live_viewed/progress 过滤）"
        )
        return pending

    pending = [
        x
        for x in items
        if x.needs_replay and x.key not in progress_keys and _attend_ok(x)
    ]
    filter_label = {"all": "不限签到", "absent": "仅缺勤", "present": "仅已签到"}[af]
    skipped = sum(
        1
        for x in items
        if x.needs_replay and x.key in progress_keys and _attend_ok(x)
    )
    log(
        f"[logs] 活动 {len(items)}，已观看回放 {viewed}，缺勤 {absent_n}，"
        f"筛选={filter_label}，待处理 {len(pending)}（本筛选断点跳过 {skipped}）"
    )
    return pending


def get_activity_flags(
    page: Page,
    classroom_id: str,
    lesson_id: str,
    *,
    origin: str = "https://www.yuketang.cn",
    allow_navigation: bool = True,
) -> tuple[bool | None, bool | None]:
    """返回 (live_viewed, attend_status)；找不到或失败为 (None, None)。

    回放进行中请传 allow_navigation=False，避免跳离 overview。
    """
    try:
        items = fetch_all_activities(
            page,
            classroom_id,
            origin=origin,
            log=lambda *_: None,
            allow_navigation=allow_navigation,
        )
    except Exception:
        return None, None
    lid = str(lesson_id)
    for it in items:
        if it.lesson_id == lid:
            return bool(it.live_viewed), bool(it.attend_status)
    return None, None


def is_live_viewed(
    page: Page,
    classroom_id: str,
    lesson_id: str,
    *,
    origin: str = "https://www.yuketang.cn",
    allow_navigation: bool = True,
) -> bool | None:
    """重新拉列表检查某节是否已观看回放。失败返回 None。

    回放进行中请传 allow_navigation=False，避免跳离 overview。
    """
    lv, _ = get_activity_flags(
        page,
        classroom_id,
        lesson_id,
        origin=origin,
        allow_navigation=allow_navigation,
    )
    return lv


def is_attended(
    page: Page,
    classroom_id: str,
    lesson_id: str,
    *,
    origin: str = "https://www.yuketang.cn",
    allow_navigation: bool = True,
) -> bool | None:
    """平台 attend_status 是否为已签到。失败/找不到返回 None。"""
    _, att = get_activity_flags(
        page,
        classroom_id,
        lesson_id,
        origin=origin,
        allow_navigation=allow_navigation,
    )
    return att


def page_shows_replay_done(page: Page) -> bool | None:
    """轻量 UI：是否显示已观看回放。None=无法判断。"""
    try:
        v = page.evaluate(
            """() => {
              const t = (document.body && document.body.innerText) || '';
              if (!t) return null;
              if (t.includes('已观看回放') && !t.includes('未观看回放')) return true;
              return false;
            }"""
        )
        if v is None:
            return None
        return bool(v)
    except Exception:
        return None


def platform_replay_confirmed(
    page: Page,
    lesson_id: str,
    *,
    classroom_id: str = "",
    origin: str = "https://www.yuketang.cn",
    allow_navigation: bool = False,
) -> bool:
    """平台是否确认回放完成（优先 basic-info，可选 logs）。"""
    fr = basic_info_finish_replay(page, lesson_id, origin=origin)
    if fr is True:
        return True
    if classroom_id:
        lv = is_live_viewed(
            page,
            classroom_id,
            lesson_id,
            origin=origin,
            allow_navigation=allow_navigation,
        )
        if lv is True:
            return True
    ui = page_shows_replay_done(page)
    return ui is True


def _page_fetch_json(page: Page, path: str) -> dict[str, Any] | None:
    data = page.evaluate(
        """async (path) => {
            try {
              const r = await fetch(path, {
                credentials: 'include',
                headers: { 'Accept': 'application/json, text/plain, */*' },
              });
              if (!r.ok) return null;
              return await r.json();
            } catch (e) { return null; }
        }""",
        path,
    )
    return data if isinstance(data, dict) else None


def basic_info_finish_replay(
    page: Page,
    lesson_id: str,
    *,
    origin: str = "https://www.yuketang.cn",
) -> bool | None:
    """查询 basic-info 的 finishReplay。"""
    path = f"/api/v3/classroom-report/lesson/basic-info?lesson_id={lesson_id}"
    try:
        data = _page_fetch_json(page, path)
        if not data:
            url = f"{origin.rstrip('/')}{path}"
            resp = page.request.get(url)
            if not resp.ok:
                return None
            data = resp.json()
        block = (data or {}).get("data") or {}
        if "finishReplay" not in block:
            return None
        return bool(block.get("finishReplay"))
    except Exception:
        return None


def replay_segment_count(
    page: Page,
    lesson_id: str,
    *,
    origin: str = "https://www.yuketang.cn",
) -> tuple[int, float]:
    """返回 (分片数, 总时长秒)。"""
    path = f"/api/v3/classroom-report/replay?lesson_id={lesson_id}&canFakeLive=1"
    try:
        data = _page_fetch_json(page, path)
        if not data:
            url = f"{origin.rstrip('/')}{path}"
            resp = page.request.get(url)
            if not resp.ok:
                return 0, 0.0
            data = resp.json()
        block = (data or {}).get("data") or {}
        lives = block.get("live") or []
        n = len(lives) if isinstance(lives, list) else 0
        dur_ms = float(block.get("lessonDuration") or 0)
        if not dur_ms and isinstance(lives, list):
            dur_ms = sum(float(x.get("duration") or 0) for x in lives if isinstance(x, dict))
        return n, dur_ms / 1000.0
    except Exception:
        return 0, 0.0
