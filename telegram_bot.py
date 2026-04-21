"""
telegram_bot.py
텔레그램 답장 수신 → 네이버 카페 댓글 자동 등록

흐름:
1. getUpdates API로 텔레그램 메시지 폴링
2. 카페 새글 알림에 대한 답장(reply)인지 확인
3. 답장 텍스트를 카페 해당 게시글에 댓글로 등록
"""

import json
import os
import time
import logging
import threading
import requests

from config import CAFE_ID, CAFE_URL, NAVER_COOKIE_PATH
from notifier import _tg_config, send_telegram
from cafe_monitor import get_article_mapping

logger = logging.getLogger(__name__)

# ── 상태 ────────────────────────────────────
_bot_thread = None
_running = False
_poll_interval = 3  # 3초 간격
_last_update_id = 0
_bot_pid = None  # 중복 실행 방지용
_processed_updates = set()  # 처리된 update_id (중복 방지)


def _get_updates(offset=0) -> list:
    """텔레그램 getUpdates API"""
    token = _tg_config["bot_token"]
    if not token:
        return []

    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        params = {"timeout": 10, "allowed_updates": ["message"]}
        if offset:
            params["offset"] = offset

        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("result", [])
        return []
    except Exception as e:
        logger.debug(f"getUpdates 오류: {e}")
        return []


def _post_cafe_comment(article_id: str, comment_text: str) -> bool:
    """네이버 카페에 댓글 등록 (API 방식)"""
    if not os.path.exists(NAVER_COOKIE_PATH):
        logger.warning("네이버 쿠키 없음 — 댓글 등록 불가")
        return False

    try:
        with open(NAVER_COOKIE_PATH, "r", encoding="utf-8") as f:
            cookies = json.load(f)
    except Exception as e:
        logger.warning(f"쿠키 로드 실패: {e}")
        return False

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"{CAFE_URL}/{article_id}",
    })

    for c in cookies:
        session.cookies.set(
            c["name"], c["value"],
            domain=c.get("domain", ".naver.com"),
            path=c.get("path", "/"),
        )

    try:
        # 네이버 카페 댓글 작성 API
        api_url = f"https://apis.naver.com/cafe-web/cafe-articleapi/v2/cafes/{CAFE_ID}/articles/{article_id}/comments"

        payload = {
            "content": comment_text,
        }

        # Referer 필수
        session.headers.update({
            "Referer": f"https://cafe.naver.com/ca-fe/cafes/{CAFE_ID}/articles/{article_id}",
            "Content-Type": "application/json",
        })

        resp = session.post(api_url, json=payload, timeout=10)

        if resp.status_code in (200, 201):
            logger.info(f"✅ 댓글 등록 완료: article={article_id}")
            return True
        else:
            logger.warning(f"댓글 등록 실패: {resp.status_code} {resp.text[:200]}")

            # Fallback: 다른 API 엔드포인트 시도
            api_url2 = (
                f"https://cafe.naver.com/CommentPost.nhn"
            )
            data2 = {
                "clubid": CAFE_ID,
                "articleid": article_id,
                "content": comment_text,
            }
            session.headers["Content-Type"] = "application/x-www-form-urlencoded"
            resp2 = session.post(api_url2, data=data2, timeout=10)
            if resp2.status_code in (200, 201, 302):
                logger.info(f"✅ 댓글 등록 완료 (fallback): article={article_id}")
                return True
            else:
                logger.warning(f"댓글 등록 실패 (fallback): {resp2.status_code}")
                return False

    except Exception as e:
        logger.warning(f"댓글 등록 오류: {e}")
        return False


def _process_reply(message: dict, log_callback=None) -> bool:
    """텔레그램 답장 메시지 처리 → 카페 댓글 등록"""
    reply_to = message.get("reply_to_message")
    if not reply_to:
        return False

    reply_msg_id = str(reply_to.get("message_id", ""))
    comment_text = message.get("text", "").strip()

    if not reply_msg_id or not comment_text:
        return False

    # 매핑에서 카페 게시글 정보 찾기
    mapping = get_article_mapping()
    article_info = mapping.get(reply_msg_id)

    if not article_info:
        logger.debug(f"매핑 없음: tg_msg={reply_msg_id}")
        return False

    article_id = article_info["article_id"]
    title = article_info.get("title", "")

    logger.info(f"💬 댓글 등록 시도: [{article_id}] {title} → '{comment_text[:50]}'")
    if log_callback:
        log_callback(f"💬 텔레그램 답장 → 카페 댓글: [{title[:30]}] {comment_text[:50]}")

    success = _post_cafe_comment(article_id, comment_text)

    # 결과 알림
    if success:
        send_telegram(
            f"✅ <b>댓글 등록 완료</b>\n"
            f"📌 {title[:50]}\n"
            f"💬 {comment_text[:100]}"
        )
    else:
        send_telegram(
            f"❌ <b>댓글 등록 실패</b>\n"
            f"📌 {title[:50]}\n"
            f"💬 {comment_text[:100]}\n\n"
            f"⚠️ 쿠키 만료 또는 API 오류 — 직접 등록해주세요"
        )

    return success


# ── AI 채팅 (텔레그램 → AI → 텔레그램) ──────────

_AI_COMMANDS = {
    "/상태": "server_status",
    "/status": "server_status",
    "/help": "help",
    "/도움": "help",
    "/리스트": "task_list",
    "/list": "task_list",
    "/수집": "run_task",
    "/중지": "stop_task",
    "/멈춤": "stop_all",
    "/브랜드수집": "run_per_brand",
    "/투두": "todo_list",
    "/todo": "todo_list",
}

def _process_ai_chat(message: dict, log_callback=None):
    """텔레그램 일반 메시지 → AI 응답"""
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "").strip()

    # 우리 chat_id만 응답 (보안)
    if chat_id != _tg_config.get("chat_id", ""):
        return

    if not text:
        return

    logger.info(f"💬 텔레그램 메시지 수신: {text[:50]}")

    # 특수 명령어 처리
    parts = text.split()
    cmd_word = parts[0].lower() if text.startswith("/") else ""
    cmd = _AI_COMMANDS.get(cmd_word)

    if cmd == "server_status":
        _send_server_status()
        return
    if cmd == "todo_list":
        if len(parts) > 1:
            arg = parts[1]
            # /투두 완료 번호 → 완료 처리
            if arg in ("완료", "done") and len(parts) > 2:
                _complete_todo(parts[2])
            # /투두 삭제 번호 → 삭제
            elif arg in ("삭제", "del") and len(parts) > 2:
                _delete_todo(parts[2])
            else:
                # /투두 할일내용 → 추가
                todo_text = " ".join(parts[1:])
                _add_todo(todo_text)
        else:
            _send_todo_list()
        return
    if cmd == "help":
        send_telegram(
            "🤖 <b>AI 어시스턴트 명령어</b>\n\n"
            "/상태 — 서버 상태 확인\n"
            "/투두 — To Do List 확인\n"
            "/투두 내용 — To Do 추가\n"
            "/투두 완료 번호 — 완료 처리\n"
            "/투두 삭제 번호 — 삭제\n"
            "/리스트 — 수집 작업 리스트 보기\n"
            "/수집 번호 — 해당 번호 작업 수집 시작\n"
            "  예: /수집 3\n"
            "  예: /수집 3-5 (3~5번 순차 실행)\n"
            "/수집 3,7,15 — 개별 번호 선택 실행\n"
            "/브랜드수집 — 브랜드별 순환수집 (라운드로빈)\n"
            "/중지 — 수집 강제 중지\n"
            "/도움 — 도움말\n\n"
            "그 외 자유롭게 질문하면 AI가 답변합니다."
        )
        return
    if cmd == "task_list":
        _send_task_list()
        return
    if cmd == "run_task":
        # "/수집 9", "/수집 9번", "/수집 3,7,15", "/수집 9-12" 모두 처리
        import re as _re_cmd
        arg_text = text[len(parts[0]):].strip()
        # 쉼표 구분이면 개별 번호
        if "," in arg_text:
            nums = _re_cmd.findall(r'\d+', arg_text)
            arg = ",".join(nums)
        else:
            nums_match = _re_cmd.findall(r'\d+', arg_text)
            arg = "-".join(nums_match[:2]) if len(nums_match) == 2 else (nums_match[0] if nums_match else "")
        _run_task_by_number(arg, log_callback)
        return
    if cmd == "stop_task":
        _stop_scraping()
        return
    if cmd == "stop_all":
        _stop_all()
        return
    if cmd == "run_per_brand":
        _run_per_brand(log_callback)
        return

    # AI에게 전달
    try:
        from post_generator import get_ai_config, _call_gemini, _call_claude, _call_openai
        config = get_ai_config()
        provider = config.get("provider", "none")

        if provider == "none":
            send_telegram("⚠️ AI가 설정되지 않았습니다. 대시보드에서 AI 설정을 확인해주세요.")
            return

        # 서버 상태 컨텍스트 추가
        context = _get_server_context()

        prompt = f"""당신은 일본 구매대행 쇼핑몰 'TheOne Vintage' 관리 AI 어시스턴트입니다.
관리자의 질문에 간결하고 정확하게 답변하세요.

[현재 서버 상태]
{context}

[관리자 질문]
{text}

간결하게 답변하세요. HTML 태그 사용 가능 (<b>, <i>, <code>)."""

        # 우선 provider 시도 → 실패 시 다른 provider 폴백
        result = None
        providers = []
        if provider == "gemini" and config.get("gemini_key"):
            providers = [("gemini", _call_gemini), ("openai", _call_openai), ("claude", _call_claude)]
        elif provider == "openai" and config.get("openai_key"):
            providers = [("openai", _call_openai), ("gemini", _call_gemini), ("claude", _call_claude)]
        elif provider == "claude" and config.get("claude_key"):
            providers = [("claude", _call_claude), ("openai", _call_openai), ("gemini", _call_gemini)]

        for pname, pfunc in providers:
            try:
                result = pfunc(prompt)
                if result:
                    break
            except Exception as pe:
                logger.warning(f"AI {pname} 실패: {pe}")
                continue

        if not result:
            send_telegram("⚠️ 모든 AI API가 응답하지 않습니다.")
            return

        if result:
            # 텔레그램 메시지 길이 제한 (4096자)
            if len(result) > 4000:
                result = result[:4000] + "\n\n... (길이 제한으로 잘림)"
            send_telegram(f"🤖 <b>AI 응답</b>\n\n{result}")
        else:
            send_telegram("⚠️ AI 응답이 비어있습니다.")

    except Exception as e:
        logger.warning(f"AI 채팅 오류: {e}")
        send_telegram(f"❌ AI 응답 오류: {str(e)[:200]}")


def _send_task_list():
    """수집 작업 리스트를 텔레그램으로 전송"""
    try:
        import sqlite3
        from data_manager import get_path
        db_path = os.path.join(get_path("db"), "users.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM scrape_tasks ORDER BY id").fetchall()
        conn.close()

        if not rows:
            send_telegram("📋 수집 작업 리스트가 비어있습니다.")
            return

        status_icons = {"대기": "⏳", "수집중": "🔄", "완료": "✅", "오류": "❌"}
        lines = ["📋 <b>수집 작업 리스트</b>\n"]
        for i, r in enumerate(rows):
            icon = status_icons.get(r["status"], "⏳")
            brand = r["brand_name"] or "전체"
            cat = r["cat_name"] or "전체"
            pages = r["pages"] or "전체"
            count = r["count"] or 0
            line = f"{i+1}. {icon} {brand} / {cat} (p.{pages})"
            if r["status"] == "완료" and count:
                line += f" — {count}개"
            lines.append(line)

        # 요약
        total = len(rows)
        done = sum(1 for r in rows if r["status"] == "완료")
        pending = sum(1 for r in rows if r["status"] == "대기")
        lines.append(f"\n총 {total}개 | 완료 {done} | 대기 {pending}")
        lines.append("\n<code>/수집 번호</code> 로 실행")

        msg = "\n".join(lines)
        # 텔레그램 4096자 제한
        if len(msg) > 4000:
            msg = msg[:4000] + "\n... (더 보기: 대시보드)"
        send_telegram(msg)
    except Exception as e:
        send_telegram(f"❌ 리스트 조회 실패: {e}")


def _run_task_by_number(arg: str, log_callback=None, force=False):
    """번호로 수집 작업 실행 (force=True면 완료/오류도 재실행)"""
    if not arg:
        send_telegram("⚠️ 번호를 입력해주세요.\n예: <code>/수집 3</code> 또는 <code>/수집 3-5</code>\n강제: <code>/수집 3 강제</code>")
        return

    try:
        import sqlite3
        from data_manager import get_path
        db_path = os.path.join(get_path("db"), "users.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM scrape_tasks ORDER BY id").fetchall()
        conn.close()

        # 파싱: 3 / 3-5 / 3,7,15
        if "," in arg:
            nums = [int(n) for n in arg.split(",") if n.strip().isdigit()]
        elif "-" in arg:
            start_s, end_s = arg.split("-", 1)
            nums = list(range(int(start_s), int(end_s) + 1))
        else:
            nums = [int(arg)]

        tasks = []
        for n in nums:
            if 1 <= n <= len(rows):
                r = rows[n - 1]
                # 모든 상태에서 실행 가능 (수집중 포함 → 큐에 예약)
                if r["status"] != "대기":
                    c = sqlite3.connect(db_path)
                    c.execute("UPDATE scrape_tasks SET status='대기', count=0 WHERE id=?", (r["id"],))
                    c.commit()
                    c.close()
                tasks.append(r)
            else:
                send_telegram(f"⚠️ {n}번은 범위 밖입니다 (1~{len(rows)})")

        if not tasks:
            send_telegram("⚠️ 실행할 작업이 없습니다.")
            return

        task_names = "\n".join(f"  {r['brand_name'] or '전체'} / {r['cat_name'] or '전체'} (p.{r['pages'] or '전체'})" for r in tasks)

        # 큐 방식으로 예약 (현재 수집 중이면 대기 후 자동 실행)
        try:
            import app as _app
            _app._start_queue_worker()
            for r in tasks:
                c = sqlite3.connect(db_path)
                c.execute("UPDATE scrape_tasks SET status='예약' WHERE id=?", (r["id"],))
                c.commit()
                c.close()
                _app._scrape_queue.put(r["id"])

            queue_size = _app._scrape_queue.qsize()
            if _app.status.get("scraping"):
                send_telegram(f"⏰ <b>{len(tasks)}개 작업 큐에 예약</b>\n(현재 수집 완료 후 자동 시작)\n\n{task_names}\n\n큐 대기: {queue_size}개")
            else:
                send_telegram(f"🚀 <b>{len(tasks)}개 작업 수집 시작</b>\n{task_names}")
        except Exception as e:
            send_telegram(f"❌ 큐 등록 실패: {str(e)[:100]}")

    except Exception as e:
        send_telegram(f"❌ 실행 오류: {e}")


def _run_per_brand(log_callback=None):
    """브랜드별 순환수집 (라운드로빈): 모든 대기/예약/수집중(멈춤) 작업을 브랜드별 교차 순서로 큐에 등록"""
    try:
        import sqlite3
        from data_manager import get_path
        db_path = os.path.join(get_path("db"), "users.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # '수집중' 상태 작업 → 대기로 복구 (비정상 종료된 작업)
        stuck = conn.execute("SELECT count(*) FROM scrape_tasks WHERE status='수집중'").fetchone()[0]
        if stuck > 0:
            conn.execute("UPDATE scrape_tasks SET status='대기' WHERE status='수집중'")
            conn.commit()
            send_telegram(f"🔧 수집중 멈춤 작업 {stuck}건 → 대기로 복구")
        rows = conn.execute("SELECT * FROM scrape_tasks WHERE status IN ('대기','예약') ORDER BY id").fetchall()
        conn.close()

        if not rows:
            send_telegram("⚠️ 대기/예약 상태 작업이 없습니다.")
            return

        # 브랜드별 그룹핑
        brand_map = {}
        for r in rows:
            brand = r["brand_name"] or r["brand"] or "전체"
            if brand not in brand_map:
                brand_map[brand] = []
            brand_map[brand].append(r)

        brands = list(brand_map.keys())

        # 라운드로빈 순서 생성
        round_robin = []
        max_len = max(len(v) for v in brand_map.values())
        for i in range(max_len):
            for brand in brands:
                if i < len(brand_map[brand]):
                    round_robin.append(brand_map[brand][i])

        if not round_robin:
            send_telegram("⚠️ 선택할 작업이 없습니다.")
            return

        # 브랜드별 개수 요약
        summary = "\n".join(f"  {b}: {len(ts)}개" for b, ts in brand_map.items())
        order_preview = " → ".join(brands)

        # 큐에 예약
        try:
            import app as _app
            _app._start_queue_worker()
            for r in round_robin:
                c = sqlite3.connect(db_path)
                c.execute("UPDATE scrape_tasks SET status='예약' WHERE id=?", (r["id"],))
                c.commit()
                c.close()
                _app._scrape_queue.put(r["id"])

            queue_size = _app._scrape_queue.qsize()
            send_telegram(
                f"🔄 <b>브랜드별 순환수집 시작 ({len(round_robin)}개)</b>\n\n"
                f"{summary}\n\n"
                f"순서: {order_preview} → 반복\n"
                f"큐 대기: {queue_size}개"
            )
        except Exception as e:
            send_telegram(f"❌ 큐 등록 실패: {str(e)[:100]}")

    except Exception as e:
        send_telegram(f"❌ 브랜드수집 오류: {e}")


def _stop_all():
    """전체 멈춤: 수집 + 큐 + 예약 모두 중지"""
    try:
        import requests as _req
        _req.post("http://127.0.0.1:3002/scrape/stop-all", timeout=10)
        send_telegram("⏹ <b>전체 멈춤 완료</b>\n수집 중지 + 큐 비우기 + 예약 → 대기")
    except Exception as e:
        send_telegram(f"❌ 전체 멈춤 실패: {e}")


def _stop_scraping():
    """수집 강제 중지"""
    try:
        import app as _app
        _app.status["scraping"] = False
        _app.status["stop_requested"] = True
        import asyncio
        from xebio_search import force_close_browser
        asyncio.run(force_close_browser())
        send_telegram("⛔ 수집 강제 중지 완료")
    except Exception as e:
        send_telegram(f"❌ 중지 실패: {e}")


def _get_server_context() -> str:
    """현재 서버 상태 요약"""
    try:
        import sqlite3
        from data_manager import get_path
        db_path = os.path.join(get_path("db"), "products.db")
        conn = sqlite3.connect(db_path)
        total = conn.execute("SELECT count(*) FROM products WHERE site_id='2ndstreet'").fetchone()[0]
        brands = conn.execute("SELECT brand, count(*) c FROM products WHERE site_id='2ndstreet' GROUP BY brand ORDER BY c DESC LIMIT 5").fetchall()
        conn.close()
        brand_info = ", ".join(f"{b[0]}({b[1]})" for b in brands)
        return f"총 상품: {total}개\n브랜드: {brand_info}"
    except Exception:
        return "상태 조회 불가"


def _load_todos():
    from data_manager import get_path
    todo_path = os.path.join(get_path("db"), "todos.json")
    if os.path.exists(todo_path):
        with open(todo_path, "r", encoding="utf-8") as f:
            return json.load(f), todo_path
    return [], todo_path

def _save_todos(todos, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)

def _complete_todo(num_str):
    """To Do 완료 처리"""
    try:
        todos, path = _load_todos()
        active = [t for t in todos if not t.get("done")]
        num = int(num_str)
        if num < 1 or num > len(active):
            send_telegram(f"❌ 번호 {num}이 범위 밖입니다 (1~{len(active)})")
            return
        target = active[num - 1]
        target["done"] = True
        import time as _time
        target["completed"] = _time.strftime("%Y-%m-%d %H:%M")
        _save_todos(todos, path)
        send_telegram(f"✅ 완료 처리\n\n<s>{target['text']}</s>")
    except Exception as e:
        send_telegram(f"❌ 완료 처리 실패: {e}")

def _delete_todo(num_str):
    """To Do 삭제"""
    try:
        todos, path = _load_todos()
        active = [t for t in todos if not t.get("done")]
        num = int(num_str)
        if num < 1 or num > len(active):
            send_telegram(f"❌ 번호 {num}이 범위 밖입니다 (1~{len(active)})")
            return
        target = active[num - 1]
        todos.remove(target)
        _save_todos(todos, path)
        send_telegram(f"🗑 삭제 완료\n\n{target['text']}")
    except Exception as e:
        send_telegram(f"❌ 삭제 실패: {e}")

def _add_todo(text):
    """텔레그램에서 To Do 추가"""
    try:
        import time as _time
        todos, path = _load_todos()
        todos.insert(0, {
            "id": int(_time.time() * 1000),
            "text": text,
            "priority": "normal",
            "done": False,
            "images": [],
            "created": _time.strftime("%Y-%m-%d %H:%M"),
        })
        _save_todos(todos, path)
        send_telegram(f"✅ To Do 추가 완료\n\n📝 {text}")
    except Exception as e:
        send_telegram(f"❌ To Do 추가 실패: {e}")


def _send_todo_list():
    """To Do List 텔레그램 전송"""
    try:
        from data_manager import get_path
        todo_path = os.path.join(get_path("db"), "todos.json")
        if not os.path.exists(todo_path):
            send_telegram("📝 <b>To Do List</b>\n\n할 일이 없습니다.")
            return
        with open(todo_path, "r", encoding="utf-8") as f:
            todos = json.load(f)
        if not todos:
            send_telegram("📝 <b>To Do List</b>\n\n할 일이 없습니다.")
            return

        priority_icon = {"high": "🔴", "normal": "🟡", "low": "⚪"}
        active = [t for t in todos if not t.get("done")]
        done = [t for t in todos if t.get("done")]

        lines = ["📝 <b>To Do List</b>\n"]
        if active:
            lines.append(f"<b>진행중 ({len(active)}건)</b>")
            for i, t in enumerate(active, 1):
                icon = priority_icon.get(t.get("priority", "normal"), "🟡")
                lines.append(f"  {icon} {i}. {t['text']}")
        if done:
            lines.append(f"\n<b>완료 ({len(done)}건)</b>")
            for t in done[:5]:
                lines.append(f"  ✅ <s>{t['text']}</s>")
            if len(done) > 5:
                lines.append(f"  ... 외 {len(done)-5}건")

        lines.append(f"\n총 {len(todos)}건 (진행 {len(active)} / 완료 {len(done)})")
        send_telegram("\n".join(lines))
    except Exception as e:
        send_telegram(f"📝 To Do List 로드 실패: {e}")


def _send_server_status():
    """서버 상태를 텔레그램으로 전송"""
    try:
        context = _get_server_context()
        from exchange import get_cached_rate
        rate = get_cached_rate() or 0
        send_telegram(
            f"📊 <b>서버 상태</b>\n\n"
            f"{context}\n"
            f"💱 환율: 1엔 = {rate:.2f}원\n"
            f"🟢 서버 정상 운영 중"
        )
    except Exception as e:
        send_telegram(f"❌ 상태 조회 실패: {e}")


def _bot_loop(log_callback=None):
    """텔레그램 봇 폴링 루프"""
    global _running, _last_update_id
    logger.info("🤖 텔레그램 봇 리스너 시작")
    if log_callback:
        log_callback("🤖 텔레그램 봇 시작 — 답장 대기 중...")

    while _running:
        try:
            updates = _get_updates(offset=_last_update_id + 1 if _last_update_id else 0)

            for update in updates:
                update_id = update.get("update_id", 0)
                if update_id > _last_update_id:
                    _last_update_id = update_id
                # 중복 처리 방지
                if update_id in _processed_updates:
                    continue
                _processed_updates.add(update_id)
                if len(_processed_updates) > 1000:
                    _processed_updates.clear()

                message = update.get("message", {})
                if message.get("reply_to_message"):
                    _process_reply(message, log_callback)
                elif message.get("text"):
                    # 일반 메시지 → AI 처리
                    _process_ai_chat(message, log_callback)

        except Exception as e:
            logger.debug(f"봇 폴링 오류: {e}")

        time.sleep(_poll_interval)

    logger.info("🤖 텔레그램 봇 리스너 종료")
    if log_callback:
        log_callback("🤖 텔레그램 봇 종료")


def start_bot(log_callback=None):
    """텔레그램 봇 시작 (1회만 — 파일 락)"""
    global _bot_thread, _running, _bot_pid
    if _running or (_bot_thread and _bot_thread.is_alive()):
        return False
    # 파일 락으로 중복 방지
    lock_path = os.path.join(os.path.dirname(__file__), ".bot_lock")
    try:
        import fcntl
        _lock_fd = open(lock_path, "w")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
    except (IOError, OSError):
        return False  # 이미 다른 프로세스가 실행 중

    _running = True
    _bot_thread = threading.Thread(
        target=_bot_loop, args=(log_callback,), daemon=True
    )
    _bot_thread.start()
    return True


def stop_bot():
    """텔레그램 봇 종료"""
    global _running
    _running = False


def is_bot_running() -> bool:
    """봇 실행 중 여부"""
    return _running
