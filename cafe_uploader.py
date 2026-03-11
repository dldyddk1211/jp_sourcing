"""
cafe_uploader.py
네이버 카페 자동 업로드 (쿠키 기반 로그인)

흐름:
1. 첫 실행 시 브라우저 열림 → 사용자가 수동 로그인 → 쿠키 저장
2. 이후 저장된 쿠키로 자동 로그인 (캡차 없음)
3. 쿠키 만료 시 다시 수동 로그인 요청
"""

import asyncio
import json
import os
import logging
import requests
import tempfile
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from config import (
    CAFE_URL, CAFE_ID, CAFE_MENU_NAME, CAFE_MENU_ID,
    NAVER_COOKIE_PATH, NAVER_LOGIN_TIMEOUT,
)
from exchange import calc_buying_price, format_price

logger = logging.getLogger(__name__)


# =============================================
# 쿠키 관리
# =============================================

def save_cookies(cookies: list):
    """쿠키를 파일에 저장"""
    with open(NAVER_COOKIE_PATH, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ 쿠키 저장 완료: {NAVER_COOKIE_PATH} ({len(cookies)}개)")


def load_cookies() -> list:
    """저장된 쿠키 불러오기"""
    if not os.path.exists(NAVER_COOKIE_PATH):
        return []
    try:
        with open(NAVER_COOKIE_PATH, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        logger.info(f"✅ 쿠키 로드 완료: {len(cookies)}개")
        return cookies
    except Exception as e:
        logger.warning(f"⚠️ 쿠키 로드 실패: {e}")
        return []


def delete_cookies():
    """저장된 쿠키 삭제"""
    if os.path.exists(NAVER_COOKIE_PATH):
        os.remove(NAVER_COOKIE_PATH)
        logger.info("🗑️ 쿠키 삭제 완료")


def has_saved_cookies() -> bool:
    """쿠키 파일 존재 여부"""
    return os.path.exists(NAVER_COOKIE_PATH)


# =============================================
# 네이버 수동 로그인 (쿠키 저장)
# =============================================

async def naver_manual_login(status_callback=None):
    """
    브라우저를 열어 사용자가 직접 네이버 로그인
    로그인 완료 후 쿠키를 저장

    Returns:
        bool: 로그인 성공 여부
    """
    def log(msg):
        logger.info(msg)
        if status_callback:
            status_callback(msg)

    log("🔐 네이버 로그인 브라우저를 엽니다...")
    log(f"   ⏱️ {NAVER_LOGIN_TIMEOUT}초 안에 로그인을 완료해주세요")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--window-size=500,700"]
        )
        context = await browser.new_context(
            viewport={"width": 500, "height": 700},
            locale="ko-KR",
        )
        page = await context.new_page()

        try:
            # 네이버 로그인 페이지 이동
            await page.goto(
                "https://nid.naver.com/nidlogin.login?url=https://cafe.naver.com/",
                wait_until="domcontentloaded",
                timeout=20000
            )
            log("🌐 네이버 로그인 페이지가 열렸습니다")
            log("   👉 브라우저에서 직접 로그인해주세요!")

            # 로그인 완료 대기 (URL이 cafe.naver.com으로 바뀔 때까지)
            elapsed = 0
            while elapsed < NAVER_LOGIN_TIMEOUT:
                await asyncio.sleep(2)
                elapsed += 2

                current_url = page.url
                # 로그인 성공 판단: nidlogin 페이지를 벗어남
                if "nidlogin" not in current_url and "nid.naver.com" not in current_url:
                    log("✅ 로그인 감지! 쿠키를 저장합니다...")

                    # 쿠키 저장
                    cookies = await context.cookies()
                    save_cookies(cookies)
                    log(f"✅ 쿠키 저장 완료 ({len(cookies)}개)")
                    return True

                # 30초마다 안내 메시지
                if elapsed % 30 == 0 and elapsed < NAVER_LOGIN_TIMEOUT:
                    remaining = NAVER_LOGIN_TIMEOUT - elapsed
                    log(f"   ⏱️ 남은 시간: {remaining}초")

            log("❌ 로그인 시간 초과")
            return False

        except Exception as e:
            log(f"❌ 로그인 오류: {e}")
            return False
        finally:
            await browser.close()


# =============================================
# 쿠키 유효성 검증
# =============================================

async def verify_login(context) -> bool:
    """
    저장된 쿠키로 로그인 상태 확인

    Returns:
        bool: 로그인 유효 여부
    """
    page = await context.new_page()
    try:
        await page.goto(
            "https://cafe.naver.com/ca-fe/home",
            wait_until="domcontentloaded",
            timeout=15000
        )
        await asyncio.sleep(2)

        # 로그인 상태 확인: 프로필 영역 또는 로그인 버튼 체크
        current_url = page.url
        content = await page.content()

        # 로그인 안 된 경우 로그인 페이지로 리다이렉트되거나 로그인 버튼 표시
        if "nidlogin" in current_url or "login" in current_url:
            return False

        # 로그인된 상태 확인
        if "LogoutButton" in content or "my_info" in content or "gnb_my" in content:
            return True

        # 카페 메인이 정상 로드되면 로그인 상태로 판단
        return "cafe.naver.com" in current_url

    except Exception as e:
        logger.warning(f"로그인 검증 오류: {e}")
        return False
    finally:
        await page.close()


# =============================================
# 메인 업로드 함수
# =============================================

async def upload_products(products: list, status_callback=None, max_upload=None):
    """
    상품 리스트를 네이버 카페에 업로드

    Args:
        products       : 스크래퍼에서 받은 상품 딕셔너리 리스트
        status_callback: 진행상황 콜백
        max_upload     : 최대 업로드 개수 (None = 전체)

    Returns:
        int: 업로드 성공 개수
    """
    def log(msg):
        logger.info(msg)
        if status_callback:
            status_callback(msg)

    # 쿠키 존재 확인
    cookies = load_cookies()
    if not cookies:
        log("❌ 저장된 쿠키가 없습니다. 먼저 '네이버 로그인' 버튼을 눌러주세요")
        return 0

    success_count = 0
    upload_list = products[:max_upload] if max_upload else products

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--no-sandbox"]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="ko-KR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        # 쿠키 로드
        log("🍪 저장된 쿠키로 로그인 중...")
        await context.add_cookies(cookies)

        # 로그인 유효성 검증
        is_valid = await verify_login(context)
        if not is_valid:
            log("❌ 쿠키가 만료되었습니다. '네이버 로그인' 버튼을 다시 눌러주세요")
            delete_cookies()
            await browser.close()
            return 0

        log("✅ 로그인 확인 완료!")

        page = await context.new_page()

        try:
            # 카페 이동
            log(f"🏠 카페 이동 중: {CAFE_URL}")
            await page.goto(CAFE_URL, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)

            # 상품별 업로드
            for i, product in enumerate(upload_list, 1):
                try:
                    name_short = (product.get("name_ko") or product.get("name", ""))[:30]
                    log(f"📤 [{i}/{len(upload_list)}] 업로드 중: {name_short}")
                    ok = await upload_single_product(page, product, log)
                    if ok:
                        success_count += 1
                        log(f"   ✅ 업로드 성공 ({success_count}개 완료)")
                    else:
                        log(f"   ⚠️ 업로드 실패")
                    await asyncio.sleep(3)  # 게시글 간 딜레이
                except Exception as e:
                    log(f"   ❌ 오류: {e}")
                    continue

        except Exception as e:
            log(f"❌ 전체 오류: {e}")
            logger.exception(e)
        finally:
            await browser.close()

    log(f"🎉 업로드 완료: 총 {success_count}/{len(upload_list)}개 성공")
    return success_count


# =============================================
# 단일 상품 업로드
# =============================================

async def upload_single_product(page, product: dict, log=None) -> bool:
    """상품 하나를 카페 게시글로 작성"""
    try:
        # 가격 계산
        price_info = calc_buying_price(product.get("price_jpy", 0))

        # 게시글 제목 & 내용 생성
        title = make_post_title(product, price_info)
        content = make_post_content(product, price_info)

        # 글쓰기 페이지 이동
        write_url = f"{CAFE_URL}?iframe_url=/ArticleWrite.nhn"
        if CAFE_MENU_ID:
            write_url = f"{CAFE_URL}?iframe_url=/ArticleWrite.nhn%3Fmenuid%3D{CAFE_MENU_ID}"

        await page.goto(write_url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)

        # iframe 진입 (네이버 카페는 iframe 사용)
        frame = page.frame_locator("iframe#cafe_main").first
        if not frame:
            if log:
                log("   ⚠️ 카페 iframe을 찾지 못했습니다")
            return False

        # 게시판 선택 (메뉴 ID가 없는 경우)
        if not CAFE_MENU_ID:
            await select_cafe_menu(frame)

        # 제목 입력
        title_input = frame.locator("input.textarea_input, #subject, input[name='subject']").first
        await title_input.fill(title)
        await asyncio.sleep(0.5)

        # 에디터에 내용 입력
        await type_content_to_editor(frame, content)

        # 이미지 업로드
        if product.get("img_url"):
            await upload_image_from_url(frame, product["img_url"])

        # 등록 버튼 클릭
        submit_btn = frame.locator(
            "button.BaseButton--submit, "
            "button:has-text('등록'), "
            "a.btn_upload, "
            "button[class*='submit']"
        ).first
        await submit_btn.click()
        await asyncio.sleep(3)

        return True

    except Exception as e:
        logger.error(f"단일 업로드 오류: {e}")
        return False


async def select_cafe_menu(frame):
    """카페 게시판 선택"""
    try:
        # 게시판 선택 드롭다운 클릭
        menu_selector = frame.locator(
            "a.board_name, "
            "button[class*='select_board'], "
            "[class*='menu_select']"
        ).first
        if await menu_selector.count() > 0:
            await menu_selector.click()
            await asyncio.sleep(1)

        # 게시판 이름으로 선택
        menu_item = frame.locator(f"text={CAFE_MENU_NAME}").first
        if await menu_item.count() > 0:
            await menu_item.click()
            await asyncio.sleep(1)
            return True
    except Exception:
        pass
    return False


async def type_content_to_editor(frame, content: str):
    """스마트에디터에 내용 입력"""
    editor_selectors = [
        ".se-content .se-component-content",
        ".se-content",
        "[contenteditable=true]",
        ".se2_input",
    ]
    for sel in editor_selectors:
        try:
            el = frame.locator(sel).first
            if await el.count() > 0:
                await el.click()
                # 줄바꿈 처리: 줄별로 입력
                lines = content.split("\n")
                for j, line in enumerate(lines):
                    if line.strip():
                        await el.type(line, delay=5)
                    if j < len(lines) - 1:
                        await el.press("Enter")
                return
        except Exception:
            continue

    # iframe 안의 body에 직접 입력
    try:
        editor_frame = frame.frame_locator("iframe.se_iframe").content_frame()
        body = editor_frame.locator("body")
        await body.click()
        lines = content.split("\n")
        for j, line in enumerate(lines):
            if line.strip():
                await body.type(line, delay=5)
            if j < len(lines) - 1:
                await body.press("Enter")
    except Exception as e:
        logger.warning(f"에디터 입력 오류: {e}")


async def upload_image_from_url(frame, img_url: str):
    """이미지 URL에서 파일 다운로드 후 업로드"""
    try:
        res = requests.get(img_url, timeout=10)
        if res.status_code != 200:
            return

        # 임시 파일에 저장
        tmp_dir = tempfile.gettempdir()
        tmp_path = os.path.join(tmp_dir, f"upload_img_{datetime.now().strftime('%H%M%S')}.jpg")
        with open(tmp_path, "wb") as f:
            f.write(res.content)

        # 파일 업로드 입력
        file_input = frame.locator("input[type=file]").first
        if await file_input.count() > 0:
            await file_input.set_input_files(tmp_path)
            await asyncio.sleep(2)

        # 임시 파일 정리
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    except Exception as e:
        logger.warning(f"이미지 업로드 오류: {e}")


# =============================================
# 게시글 템플릿
# =============================================

def make_post_title(product: dict, price_info: dict) -> str:
    """게시글 제목 생성"""
    name = product.get("name_ko") or product.get("name", "상품명 없음")
    brand = product.get("brand_ko") or product.get("brand", "")
    price_krw = format_price(price_info["price_final"])

    # 제목 길이 제한 (네이버 카페 제목 최대 100자)
    title = f"[{brand}] {name}"
    if len(title) > 80:
        title = title[:77] + "..."
    return f"{title} / {price_krw}"


def make_post_content(product: dict, price_info: dict) -> str:
    """게시글 본문 생성"""
    name = product.get("name_ko") or product.get("name", "상품명 없음")
    name_ja = product.get("name", "")
    brand = product.get("brand_ko") or product.get("brand", "")
    link = product.get("link", "")
    code = product.get("product_code", "")

    # 사이즈 정보
    sizes = product.get("sizes", [])
    available_sizes = [s["size"] for s in sizes if s.get("in_stock")]
    size_text = ", ".join(available_sizes) if available_sizes else "문의 바랍니다"

    content = f"""[{brand}] {name}

상품번호: {code}
상품명(일본어): {name_ja}

━━━━━━━━━━━━━━━━━━

💴 일본 현지가: ¥{price_info['price_jpy']:,}
💱 적용 환율: 1엔 = {price_info['rate']}원
📦 국제배송비 포함

✅ 구매대행가: {format_price(price_info['price_final'])}

━━━━━━━━━━━━━━━━━━

📏 재고 사이즈: {size_text}

🔗 일본 상품 링크:
{link}

━━━━━━━━━━━━━━━━━━
※ 환율 변동에 따라 가격이 달라질 수 있습니다.
※ 구매 문의는 댓글 또는 쪽지로 연락주세요!
※ 주문 후 배송까지 약 7~14일 소요됩니다."""

    return content.strip()
