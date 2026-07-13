"""学习日志 API：拉取课堂活动与未观看回放列表。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from playwright.sync_api import Page


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
) -> dict[str, Any]:
    """在浏览器上下文中 fetch。

    注意：不要强行加 classroom-id 等头，页面内 fetch 带这些反而会 500；
    Cookie 足够时默认头即可。
    """
    path = (
        f"/v2/api/web/logs/learn/{classroom_id}"
        f"?actype={actype}&page={page_idx}&offset={offset}&sort=-1"
    )
    url = f"{origin.rstrip('/')}{path}"

    # 1) 页面内 fetch（仅 Accept；500 时短暂重试）
    import json as _json

    raw: dict[str, Any] | None = None
    for attempt in range(4):
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
        # 分页偶发 500，退避重试
        page.wait_for_timeout(800 * (attempt + 1))


    # 2) APIRequestContext + 雨课堂头
    headers = _ykt_headers(classroom_id, origin)
    resp = page.request.get(url, headers=headers)
    if resp.ok:
        try:
            payload = resp.json()
            if _payload_ok(payload):
                return payload
        except Exception:
            pass

    # 3) 刷新日志页，只抓 actype=-1（避免 actype=100 空列表覆盖）
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
                    # 优先保留有 activities 的响应
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
    if isinstance(raw, dict):
        detail = f" fetch_status={raw.get('status')} body={str(raw.get('text'))[:200]!r}"
    raise RuntimeError(f"logs API 失败: {url}{detail} request_status={getattr(resp, 'status', '?')}")


def fetch_all_activities(
    page: Page,
    classroom_id: str,
    *,
    offset: int = 20,
    origin: str = "https://www.yuketang.cn",
    log: Callable[[str], None] = print,
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


def list_pending_replays(
    page: Page,
    classroom_id: str,
    *,
    progress_keys: set[str] | None = None,
    origin: str = "https://www.yuketang.cn",
    log: Callable[[str], None] = print,
) -> list[LessonActivity]:
    progress_keys = progress_keys or set()
    items = fetch_all_activities(page, classroom_id, origin=origin, log=log)
    pending = [
        x
        for x in items
        if x.needs_replay and x.key not in progress_keys
    ]
    viewed = sum(1 for x in items if x.live_viewed)
    log(
        f"[logs] 活动 {len(items)}，已观看回放 {viewed}，"
        f"待处理 {len(pending)}（已跳过断点 {len(progress_keys)}）"
    )
    return pending


def is_live_viewed(
    page: Page,
    classroom_id: str,
    lesson_id: str,
    *,
    origin: str = "https://www.yuketang.cn",
) -> bool | None:
    """重新拉列表检查某节是否已观看回放。失败返回 None。"""
    try:
        items = fetch_all_activities(
            page, classroom_id, origin=origin, log=lambda *_: None
        )
    except Exception:
        return None
    for it in items:
        if it.lesson_id == str(lesson_id):
            return it.live_viewed
    return None


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
