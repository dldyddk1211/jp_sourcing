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
    cmd = _AI_COMMANDS.get(text.lower().split()[0] if text.startswith("/") else "")
    if cmd == "server_status":
        _send_server_status()
        return
    if cmd == "help":
        send_telegram(
            "🤖 <b>AI 어시스턴트 명령어</b>\n\n"
            "/상태 — 서버 상태 확인\n"
            "/도움 — 도움말\n\n"
            "그 외 자유롭게 질문하면 AI가 답변합니다.\n"
            "예: 오늘 수집 현황 알려줘\n"
            "예: PRADA 가방 시세 분석해줘"
        )
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

        if provider == "gemini" and config.get("gemini_key"):
            result = _call_gemini(prompt)
        elif provider == "claude" and config.get("claude_key"):
            result = _call_claude(prompt)
        elif provider == "openai" and config.get("openai_key"):
            result = _call_openai(prompt)
        else:
            send_telegram("⚠️ AI API 키가 설정되지 않았습니다.")
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
    """텔레그램 봇 시작"""
    global _bot_thread, _running
    if _running:
        return False

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
