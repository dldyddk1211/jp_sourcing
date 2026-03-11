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
            permissions=["clipboard-read", "clipboard-write"],
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
    def _log(msg):
        if log:
            log(msg)

    try:
        # 가격 계산
        price_info = calc_buying_price(product.get("price_jpy", 0))

        # 게시글 제목 & 내용 생성 (Claude API 우선, 실패 시 기본 템플릿)
        from post_generator import generate_cafe_post
        post = generate_cafe_post(product, price_info)
        title = post["title"]
        content = post["content"]

        # ── 1단계: 카페 메뉴 페이지로 이동 ──
        menu_url = f"https://cafe.naver.com/f-e/cafes/{CAFE_ID}/menus/{CAFE_MENU_ID}"
        _log(f"   🌐 카페 메뉴 페이지 이동: {menu_url}")
        await page.goto(menu_url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)

        # 로그인 페이지로 리다이렉트 됐는지 확인
        if "login" in page.url or "nidlogin" in page.url:
            _log("   ❌ 쿠키 만료 — 재로그인 필요")
            return False

        _log(f"   ✅ 현재 URL: {page.url}")

        # ── 2단계: 글쓰기 버튼 클릭 ──
        write_btn_selectors = [
            "a:has-text('카페 글쓰기')",
            "button:has-text('카페 글쓰기')",
            "a:has-text('글쓰기')",
            "button:has-text('글쓰기')",
            "[class*='WriteButton']",
            "[class*='write-button']",
        ]
        clicked_write = False
        for sel in write_btn_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click()
                    clicked_write = True
                    _log(f"   ✅ 글쓰기 버튼 클릭: {sel}")
                    break
            except Exception:
                continue

        if not clicked_write:
            _log("   ❌ 글쓰기 버튼을 찾지 못했습니다")
            return False

        # ── 3단계: 에디터 렌더링 대기 ──
        # React SPA가 write 화면을 그릴 때까지 대기
        try:
            await page.wait_for_selector(
                "textarea.textarea_input, textarea[placeholder*='제목']",
                timeout=15000
            )
            _log("   ✅ 에디터 로딩 완료")
        except PlaywrightTimeout:
            _log("   ❌ 에디터 로딩 시간 초과 (15초)")
            return False

        # ── 4단계: 제목 입력 ──
        # 새 UI: <textarea class="textarea_input" placeholder="제목을 입력해 주세요.">
        title_selectors = [
            "textarea.textarea_input",
            "textarea[placeholder*='제목']",
            "input.textarea_input",
            "#subject",
            "input[name='subject']",
            "input[placeholder*='제목']",
        ]
        title_filled = False
        for sel in title_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click()
                    await asyncio.sleep(0.5)
                    # Playwright fill()은 React synthetic event를 제대로 발생시킴
                    await el.fill(title)
                    await asyncio.sleep(0.3)
                    val = await el.input_value()
                    if val.strip():
                        title_filled = True
                        _log(f"   ✅ 제목 입력 완료 ({sel}): {val[:30]}")
                        break
                    else:
                        _log(f"   ⚠️ 제목 입력 실패 (빈값) — keyboard.type 시도: {sel}")
                        # fallback: 직접 타이핑
                        await el.click()
                        await asyncio.sleep(0.3)
                        await page.keyboard.press("Control+a")
                        await page.keyboard.type(title, delay=30)
                        await asyncio.sleep(0.3)
                        val2 = await el.input_value()
                        if val2.strip():
                            title_filled = True
                            _log(f"   ✅ 제목 입력 완료 (타이핑): {val2[:30]}")
                            break
                        _log(f"   ⚠️ 타이핑도 실패: {sel}")
            except Exception as e:
                _log(f"   ⚠️ 제목 시도 실패 ({sel}): {e}")
                continue

        if not title_filled:
            _log("   ❌ 제목 입력란을 찾지 못했습니다")
            return False

        await asyncio.sleep(0.5)

        # ── 5단계: 본문 입력 ──
        # 새 UI: Smart Editor 3 — .se-content 클릭 후 키보드 입력
        await type_content_to_editor(page, content)

        # ── 6단계: 이미지 업로드 (대표 이미지 파일 업로드) ──
        if product.get("img_url"):
            await upload_image_from_url(page, product["img_url"])

        # ── 7단계: 등록 버튼 클릭 ──
        submit_selectors = [
            "button.BaseButton--submit",
            "button:has-text('등록')",
            "button:has-text('확인')",
            "a.btn_upload",
            "button[class*='submit']",
            "input[type='submit']",
        ]
        for sel in submit_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click()
                    _log(f"   ✅ 등록 버튼 클릭")
                    await asyncio.sleep(3)
                    return True
            except Exception:
                continue

        _log("   ❌ 등록 버튼을 찾지 못했습니다")
        return False

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


async def type_content_to_editor(page, content: str):
    """
    Smart Editor 3 (새 네이버 카페 UI) 본문 입력

    구조: .se-content > .se-canvas > .se-section-text (클릭 가능한 영역)
    방법: 클릭 후 클립보드 붙여넣기 (긴 텍스트 타이핑보다 빠름)
    """
    # HTML 태그 제거한 순수 텍스트 (에디터에 직접 입력)
    # <img src="..."> 태그는 에디터 내 이미지로 처리 불가 → URL 텍스트로 변환
    import re
    plain = re.sub(r'<img\s+src="([^"]+)"[^>]*>', r'\n[이미지: \1]', content)
    plain = re.sub(r'<[^>]+>', '', plain)  # 나머지 HTML 태그 제거
    plain = plain.strip()

    editor_selectors = [
        ".se-content",
        ".se-section-text",
        "[class*='se-module-text']",
        "[contenteditable=true]",
    ]

    clicked = False
    for sel in editor_selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.click()
                clicked = True
                logger.info(f"에디터 클릭: {sel}")
                break
        except Exception:
            continue

    if not clicked:
        logger.warning("에디터 영역을 찾지 못했습니다")
        return

    await asyncio.sleep(0.5)

    # 방법 1: DataTransfer paste 이벤트 시뮬레이션 (클립보드 API 불필요)
    try:
        pasted = await page.evaluate("""(text) => {
            const dt = new DataTransfer();
            dt.setData('text/plain', text);
            const target = document.activeElement || document.body;
            target.dispatchEvent(new ClipboardEvent('paste', {
                clipboardData: dt, bubbles: true, cancelable: true
            }));
            return true;
        }""", plain)
        await asyncio.sleep(1)
        logger.info("본문 붙여넣기 완료 (DataTransfer paste)")
        return
    except Exception as e:
        logger.warning(f"DataTransfer paste 실패: {e} → keyboard.type으로 전환")

    # 방법 2: 직접 키보드 타이핑 (느리지만 확실)
    logger.info(f"keyboard.type 시작 (글자 수: {len(plain)})")
    await page.keyboard.type(plain, delay=5)
    logger.info("본문 키보드 입력 완료")


async def upload_image_from_url(page, img_url: str):
    """
    이미지 URL → 파일 다운로드 → 에디터에 파일 업로드

    전략:
    1) 에디터 이미지 버튼 클릭 → file input 활성화 → set_input_files
    2) 숨겨진 input[type=file] 직접 set_input_files (fallback)
    """
    if not img_url:
        return

    # ── 이미지 다운로드 ──────────────────────
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(img_url, headers=headers, timeout=15)
        if res.status_code != 200:
            logger.warning(f"이미지 다운로드 실패: {res.status_code}")
            return
    except Exception as e:
        logger.warning(f"이미지 다운로드 오류: {e}")
        return

    # 확장자 추출 (png/jpg/webp 등)
    ext = "jpg"
    ct = res.headers.get("content-type", "")
    if "png" in ct:
        ext = "png"
    elif "webp" in ct:
        ext = "webp"
    elif "gif" in ct:
        ext = "gif"

    tmp_dir = tempfile.gettempdir()
    tmp_path = os.path.join(tmp_dir, f"naver_img_{datetime.now().strftime('%H%M%S%f')}.{ext}")
    with open(tmp_path, "wb") as f:
        f.write(res.content)

    logger.info(f"이미지 저장: {tmp_path} ({len(res.content):,} bytes)")

    try:
        # ── 방법 1: 에디터 이미지 버튼 클릭 후 file_chooser 이용 ──
        img_btn_selectors = [
            "button[data-name='image']",
            "button[aria-label*='사진']",
            "button[aria-label*='이미지']",
            "button[title*='사진']",
            "button[title*='이미지']",
            ".se-toolbar button[class*='image']",
            "[class*='image'] button",
        ]
        for sel in img_btn_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    async with page.expect_file_chooser(timeout=4000) as fc_info:
                        await el.click()
                    fc = await fc_info.value
                    await fc.set_files(tmp_path)
                    await asyncio.sleep(2)
                    logger.info(f"이미지 업로드 완료 (버튼 클릭): {sel}")
                    return
            except Exception:
                continue

        # ── 방법 2: hidden input[type=file] 직접 set_input_files ──
        file_input_selectors = [
            "input[type='file'][accept*='image']",
            "input[type='file']",
        ]
        for sel in file_input_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.set_input_files(tmp_path)
                    await asyncio.sleep(2)
                    logger.info(f"이미지 업로드 완료 (file input): {sel}")
                    return
            except Exception:
                continue

        logger.warning("이미지 업로드: file input을 찾지 못했습니다")

    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


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
