"""
blog_uploader.py
네이버 블로그 게시글 업로드 — cafe_uploader.py 구조 기반

흐름:
1. 블로그 계정 쿠키로 로그인
2. 블로그 글쓰기 페이지 이동
3. 제목 + 본문 + 이미지 + 태그 입력
4. 발행
"""

import asyncio
import json
import logging
import os
import random
import tempfile
from datetime import datetime

import requests
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

# ── 전역 상태 ──
_blog_upload_stop = False
_last_blog_fail_reason = ""


def request_blog_upload_stop():
    global _blog_upload_stop
    _blog_upload_stop = True


def reset_blog_upload_stop():
    global _blog_upload_stop
    _blog_upload_stop = False


def is_blog_upload_stop_requested():
    return _blog_upload_stop


def load_blog_cookies(cookie_path: str = None):
    """블로그 쿠키 로드"""
    path = cookie_path or "blog_cookies_1.json"
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        return cookies if cookies else None
    except Exception:
        return None


async def verify_blog_login(context) -> bool:
    """블로그 로그인 확인"""
    page = await context.new_page()
    try:
        await page.goto("https://blog.naver.com/", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)
        url = page.url
        # 로그인 안 된 경우 nidlogin으로 리다이렉트
        if "nidlogin" in url or "login" in url.lower():
            return False
        return True
    except Exception:
        return False
    finally:
        await page.close()


async def blog_upload_products(products: list, status_callback=None, max_upload=None,
                                delay_min=13, delay_max=15, on_single_success=None,
                                cookie_path: str = None):
    """블로그에 상품 게시글 업로드"""

    def log(msg):
        logger.info(msg)
        if status_callback:
            status_callback(msg)

    cookies = load_blog_cookies(cookie_path)
    if not cookies:
        log("❌ 블로그 쿠키가 없습니다. 먼저 블로그 계정 로그인을 해주세요")
        return 0

    success_count = 0
    uploaded_codes_session = set()
    upload_list = products[:max_upload] if max_upload else products

    # 중복 품번 제거
    code_count = {}
    for p in upload_list:
        code = p.get("product_code", "")
        if code:
            code_count[code] = code_count.get(code, 0) + 1
    dup_codes = {c for c, n in code_count.items() if n > 1}
    if dup_codes:
        seen = set()
        deduped = []
        for p in upload_list:
            code = p.get("product_code", "")
            if code and code in seen:
                continue
            if code:
                seen.add(code)
            deduped.append(p)
        upload_list = deduped

    log(f"📋 블로그 업로드 대상: {len(upload_list)}개")

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

        log("🍪 블로그 쿠키로 로그인 중...")
        await context.add_cookies(cookies)

        is_valid = await verify_blog_login(context)
        if not is_valid:
            log("❌ 블로그 쿠키가 만료되었습니다. 다시 로그인해주세요")
            await browser.close()
            return 0

        log("✅ 블로그 로그인 확인 완료!")
        page = await context.new_page()

        try:
            reset_blog_upload_stop()

            for i, product in enumerate(upload_list, 1):
                if is_blog_upload_stop_requested():
                    log(f"⏹ 블로그 업로드 중지됨 ({success_count}/{len(upload_list)}개 완료)")
                    break

                name_short = (product.get("name_ko") or product.get("name", ""))[:30]
                code = product.get("product_code", "")

                # 세션 내 중복 차단
                if code and code in uploaded_codes_session:
                    log(f"   ⏩ [{i}/{len(upload_list)}] 스킵: {name_short} — 이번 세션에서 이미 업로드됨")
                    continue

                # DB 상태 확인
                if code:
                    try:
                        from product_db import get_product_status
                        db_status = get_product_status(code)
                        if db_status and db_status not in ("대기", ""):
                            log(f"   ⏩ [{i}/{len(upload_list)}] 스킵: {name_short} — DB 상태 '{db_status}'")
                            continue
                    except Exception:
                        pass

                for attempt in range(1, 3):
                    try:
                        if attempt == 1:
                            log(f"📝 [{i}/{len(upload_list)}] 블로그 업로드 중: {name_short}")
                        else:
                            log(f"🔄 [{i}/{len(upload_list)}] 재시도 중: {name_short}")
                            await asyncio.sleep(5)

                        result = await upload_single_blog_post(page, product, log)
                        if result:
                            success_count += 1
                            if code:
                                uploaded_codes_session.add(code)
                            log(f"   ✅ 블로그 업로드 성공 ({success_count}개 완료)")
                            if on_single_success:
                                try:
                                    on_single_success(product)
                                except Exception:
                                    pass
                            break
                        else:
                            if attempt >= 2:
                                log(f"   ❌ 2회 실패 — 다음 상품으로")
                    except Exception as e:
                        logger.warning(f"블로그 업로드 오류: {e}")
                        if attempt >= 2:
                            log(f"   ⛔ 2회 연속 오류: {e}")

                # 딜레이
                if i < len(upload_list):
                    if is_blog_upload_stop_requested():
                        break
                    delay = random.randint(delay_min, delay_max) * 60
                    log(f"   ⏳ 다음 블로그 게시글까지 {delay // 60}분 대기...")
                    for _ in range(delay // 10):
                        if is_blog_upload_stop_requested():
                            break
                        await asyncio.sleep(10)
                    else:
                        await asyncio.sleep(delay % 10)
                        continue
                    break

        except Exception as e:
            log(f"❌ 블로그 전체 오류: {e}")
            logger.exception(e)
        finally:
            await browser.close()

    log(f"🎉 블로그 업로드 완료: 총 {success_count}/{len(upload_list)}개 성공")
    return success_count


async def upload_single_blog_post(page, product: dict, log=None) -> bool:
    """상품 하나를 블로그 게시글로 작성"""

    def _log(msg):
        if log:
            log(msg)

    from post_generator import generate_cafe_post, get_detail_image_urls

    # 게시글 데이터 생성 (카페와 동일한 포맷 재사용)
    price_info = None
    try:
        from exchange import get_price_info
        price_info = get_price_info(product.get("price_jpy", 0))
    except Exception:
        pass

    post = generate_cafe_post(product, price_info)
    title = post["title"]
    content_intro = post.get("content_intro", "")
    content_detail = post.get("content_detail", "")
    detail_images = get_detail_image_urls(product)

    # ── 1단계: 블로그 글쓰기 페이지 이동 ──
    write_url = "https://blog.naver.com/PostWrite.naver"
    _log(f"   🌐 블로그 글쓰기 페이지 이동: {write_url}")
    await page.goto(write_url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    # 로그인 리다이렉트 확인
    if "login" in page.url.lower() or "nidlogin" in page.url:
        _log("   ❌ 로그인 페이지로 리다이렉트 — 쿠키 만료")
        return False

    _log(f"   ✅ 현재 URL: {page.url}")

    # ── 2단계: 에디터 로딩 대기 ──
    # 네이버 블로그는 Smart Editor One 사용 (iframe 기반)
    editor_frame = None
    for frame_sel in ["iframe#mainFrame", "iframe[name='mainFrame']"]:
        try:
            fl = page.frame_locator(frame_sel)
            el = fl.locator("body").first
            if await el.count() > 0:
                editor_frame = fl
                _log(f"   ✅ 에디터 프레임 발견: {frame_sel}")
                break
        except Exception:
            continue

    if not editor_frame:
        # iframe 없이 직접 에디터인 경우
        editor_frame = page
        _log("   ℹ️ 직접 에디터 모드")

    await asyncio.sleep(2)

    # ── 3단계: 제목 입력 ──
    title_selectors = [
        "textarea.se_textarea",
        "div.se-title-text span",
        "textarea[placeholder*='제목']",
        ".post_title input",
        "input[name='title']",
        "div[contenteditable='true'][data-placeholder*='제목']",
    ]
    title_entered = False
    for sel in title_selectors:
        try:
            el = None
            if editor_frame != page:
                el = editor_frame.locator(sel).first
            else:
                el = page.locator(sel).first
            if await el.count() > 0:
                await el.click()
                await asyncio.sleep(0.3)
                await el.fill(title[:100])
                _log(f"   ✅ 제목 입력 완료: {title[:40]}...")
                title_entered = True
                break
        except Exception:
            continue

    if not title_entered:
        # contenteditable 방식 시도
        try:
            for sel in ["div.se-title-text", "[contenteditable='true']"]:
                try:
                    el = editor_frame.locator(sel).first if editor_frame != page else page.locator(sel).first
                    if await el.count() > 0:
                        await el.click()
                        await asyncio.sleep(0.3)
                        await page.keyboard.type(title[:100], delay=30)
                        _log(f"   ✅ 제목 입력 완료 (keyboard): {title[:40]}...")
                        title_entered = True
                        break
                except Exception:
                    continue
        except Exception:
            pass

    if not title_entered:
        _log("   ❌ 제목 입력 실패 — 에디터 구조 확인 필요")
        return False

    await asyncio.sleep(1)

    # ── 4단계: 본문 입력 ──
    # 본문 영역 클릭 (에디터 본문으로 포커스)
    body_selectors = [
        "div.se-component-content",
        "div.se-text-paragraph",
        "div[contenteditable='true'].se-text-paragraph",
        "div.post_editor",
        "div[contenteditable='true']",
    ]
    body_focused = False
    for sel in body_selectors:
        try:
            el = editor_frame.locator(sel).first if editor_frame != page else page.locator(sel).first
            if await el.count() > 0:
                await el.click()
                body_focused = True
                break
        except Exception:
            continue

    if not body_focused:
        # Tab으로 본문으로 이동
        await page.keyboard.press("Tab")
        await asyncio.sleep(0.5)

    # 인트로 + 상세 본문 합쳐서 입력
    full_content = content_intro
    if content_detail:
        full_content += "\n\n" + content_detail

    lines = full_content.split("\n")
    _log(f"   📝 본문 입력 중 ({len(lines)}줄)...")
    for line_idx, line in enumerate(lines):
        if line.strip():
            await page.keyboard.type(line, delay=15)
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.05)
    _log(f"   ✅ 본문 입력 완료 ({len(lines)}줄)")

    await asyncio.sleep(2)

    # ── 5단계: 이미지 업로드 ──
    if detail_images:
        _log(f"   📷 이미지 {len(detail_images)}개 업로드 시작")
        for img_idx, img_url in enumerate(detail_images):
            try:
                _log(f"   📷 이미지 [{img_idx + 1}/{len(detail_images)}]")
                await _blog_upload_image(page, editor_frame, img_url, log)
                await asyncio.sleep(2)
            except Exception as e:
                _log(f"   ⚠️ 이미지 {img_idx + 1} 업로드 실패: {e}")

    await asyncio.sleep(2)

    # ── 6단계: 태그 입력 ──
    tags = post.get("tags", [])
    if tags:
        tag_selectors = [
            "input.se-tag-input",
            "input[placeholder*='태그']",
            "input[placeholder*='Tag']",
            ".post_tag input",
        ]
        for sel in tag_selectors:
            try:
                el = editor_frame.locator(sel).first if editor_frame != page else page.locator(sel).first
                if await el.count() > 0:
                    for tag in tags[:10]:
                        tag_text = tag.replace("#", "")
                        await el.fill(tag_text)
                        await asyncio.sleep(0.3)
                        await page.keyboard.press("Enter")
                        await asyncio.sleep(0.3)
                    _log(f"   🏷️ 태그 {min(len(tags), 10)}개 입력 완료")
                    break
            except Exception:
                continue

    await asyncio.sleep(1)

    # ── 7단계: 발행 ──
    publish_selectors = [
        "button:has-text('발행')",
        "button:has-text('공개발행')",
        "button.publish_btn",
        "button.se-publish-button",
        "a:has-text('발행')",
    ]

    for sel in publish_selectors:
        try:
            el = editor_frame.locator(sel).first if editor_frame != page else page.locator(sel).first
            if await el.count() > 0:
                await el.click()
                _log("   ✅ 발행 버튼 클릭")
                await asyncio.sleep(3)

                # 발행 확인 팝업 (있는 경우)
                confirm_selectors = [
                    "button:has-text('발행')",
                    "button:has-text('확인')",
                    "button.confirm_btn",
                ]
                for csel in confirm_selectors:
                    try:
                        cel = page.locator(csel).first
                        if await cel.count() > 0:
                            await cel.click()
                            _log("   ✅ 발행 확인")
                            break
                    except Exception:
                        continue

                await asyncio.sleep(3)
                _log(f"   ✅ 블로그 게시글 발행 완료")
                return True
        except Exception:
            continue

    _log("   ❌ 발행 버튼을 찾을 수 없습니다")
    return False


async def _blog_upload_image(page, editor_frame, img_url: str, log=None):
    """블로그 에디터에 이미지 업로드"""
    if not img_url:
        return

    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(img_url, headers=headers, timeout=15)
        if res.status_code != 200:
            return
    except Exception:
        return

    ext = "jpg"
    ct = res.headers.get("content-type", "")
    if "png" in ct:
        ext = "png"
    elif "webp" in ct:
        ext = "webp"

    tmp_path = os.path.join(tempfile.gettempdir(), f"blog_img_{datetime.now().strftime('%H%M%S%f')}.{ext}")
    with open(tmp_path, "wb") as f:
        f.write(res.content)

    # 얼굴 감지 → 크롭
    try:
        import cv2
        import numpy as np
        img_cv = cv2.imdecode(np.frombuffer(open(tmp_path, "rb").read(), np.uint8), cv2.IMREAD_COLOR)
        if img_cv is not None:
            gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
            face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
            if len(faces) > 0:
                face_bottom = max(y + h for (x, y, w, h) in faces)
                crop_y = min(face_bottom + 20, img_cv.shape[0])
                remaining = img_cv.shape[0] - crop_y
                if remaining >= img_cv.shape[0] * 0.3:
                    cropped = img_cv[crop_y:, :]
                    cv2.imwrite(tmp_path, cropped, [cv2.IMWRITE_JPEG_QUALITY, 90])
                else:
                    os.remove(tmp_path)
                    return
    except ImportError:
        pass
    except Exception:
        pass

    # 이미지 리사이즈
    try:
        from PIL import Image
        with Image.open(tmp_path) as img:
            if img.width > 800:
                ratio = 800 / img.width
                new_size = (800, int(img.height * ratio))
                img = img.resize(new_size, Image.LANCZOS)
                img.save(tmp_path, quality=90)
    except Exception:
        pass

    # 파일 업로드
    try:
        # 이미지 버튼 찾기
        img_btn_selectors = [
            "button[data-name='image']",
            "button.se-image-toolbar-button",
            "button[aria-label*='사진']",
            "button[aria-label*='이미지']",
        ]

        async with page.expect_file_chooser(timeout=5000) as fc_info:
            for sel in img_btn_selectors:
                try:
                    el = editor_frame.locator(sel).first if editor_frame != page else page.locator(sel).first
                    if await el.count() > 0:
                        await el.click()
                        break
                except Exception:
                    continue
            else:
                # 이미지 버튼 못 찾으면 키보드로 시도
                return

        file_chooser = await fc_info.value
        await file_chooser.set_files(tmp_path)
        await asyncio.sleep(2)
    except Exception as e:
        logger.warning(f"블로그 이미지 업로드 실패: {e}")
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


async def blog_post_custom_content(title: str, body: str, images: list = None,
                                    log=None, cookie_path: str = None,
                                    category: str = ""):
    """URL에서 추출한 커스텀 콘텐츠를 블로그에 발행 (카테고리 선택 지원)"""

    def _log(msg):
        logger.info(msg)
        if log:
            log(msg)

    # 쿠키 로드
    accounts_path = os.path.join("db", "blog_accounts.json")
    active_slot = 1
    if os.path.exists(accounts_path):
        try:
            with open(accounts_path, "r", encoding="utf-8") as f:
                acc_data = json.load(f)
            active_slot = acc_data.get("active", 1)
        except Exception:
            pass

    cp = cookie_path or f"blog_cookies_{active_slot}.json"
    cookies = load_blog_cookies(cp)
    if not cookies:
        _log("❌ 블로그 쿠키가 없습니다. 먼저 로그인해주세요")
        return False

    _log(f"🍪 블로그 쿠키 로드 (슬롯 {active_slot})")

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
        await context.add_cookies(cookies)

        is_valid = await verify_blog_login(context)
        if not is_valid:
            _log("❌ 블로그 쿠키 만료 — 다시 로그인해주세요")
            await browser.close()
            return False

        _log("✅ 블로그 로그인 확인!")
        page = await context.new_page()

        try:
            # 글쓰기 페이지
            write_url = "https://blog.naver.com/PostWrite.naver"
            _log(f"🌐 블로그 글쓰기 이동: {write_url}")
            await page.goto(write_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            if "login" in page.url.lower() or "nidlogin" in page.url:
                _log("❌ 로그인 리다이렉트 — 쿠키 만료")
                return False

            # 에디터 프레임 찾기
            editor_frame = None
            for frame_sel in ["iframe#mainFrame", "iframe[name='mainFrame']"]:
                try:
                    fl = page.frame_locator(frame_sel)
                    el = fl.locator("body").first
                    if await el.count() > 0:
                        editor_frame = fl
                        break
                except Exception:
                    continue
            if not editor_frame:
                editor_frame = page

            await asyncio.sleep(2)

            # 카테고리 선택
            if category:
                _log(f"📂 카테고리 선택: {category}")
                cat_ok = False
                # 카테고리 드롭다운 버튼 클릭
                cat_btn_selectors = [
                    "button.category",
                    "button[class*='category']",
                    "a[class*='category']",
                    "div[class*='category'] button",
                    "select[name='categoryNo']",
                    "button:has-text('카테고리')",
                    "span:has-text('카테고리')",
                ]
                target = editor_frame if editor_frame != page else page
                for sel in cat_btn_selectors:
                    try:
                        el = target.locator(sel).first
                        if await el.count() > 0:
                            if sel.startswith("select"):
                                # select 태그인 경우 옵션에서 선택
                                options = await el.locator("option").all()
                                for opt in options:
                                    text = (await opt.inner_text()).strip()
                                    if category.lower() in text.lower():
                                        val = await opt.get_attribute("value")
                                        await el.select_option(value=val)
                                        cat_ok = True
                                        _log(f"   ✅ 카테고리 선택 완료: {text}")
                                        break
                            else:
                                await el.click()
                                await asyncio.sleep(1)
                            if cat_ok:
                                break
                    except Exception:
                        continue

                # 드롭다운 열린 경우 — 카테고리 항목 클릭
                if not cat_ok:
                    cat_item_selectors = [
                        f"li:has-text('{category}')",
                        f"a:has-text('{category}')",
                        f"span:has-text('{category}')",
                        f"div:has-text('{category}')",
                        f"button:has-text('{category}')",
                    ]
                    for sel in cat_item_selectors:
                        try:
                            el = target.locator(sel).first
                            if await el.count() > 0:
                                await el.click()
                                await asyncio.sleep(0.5)
                                cat_ok = True
                                _log(f"   ✅ 카테고리 선택 완료: {category}")
                                break
                        except Exception:
                            continue
                    # page 전체에서도 시도
                    if not cat_ok:
                        for sel in cat_item_selectors:
                            try:
                                el = page.locator(sel).first
                                if await el.count() > 0:
                                    await el.click()
                                    await asyncio.sleep(0.5)
                                    cat_ok = True
                                    _log(f"   ✅ 카테고리 선택 완료: {category}")
                                    break
                            except Exception:
                                continue

                if not cat_ok:
                    _log(f"   ⚠️ 카테고리 '{category}' 선택 실패 — 기본 카테고리로 진행")
                await asyncio.sleep(1)

            # 제목 입력
            title_selectors = [
                "textarea.se_textarea",
                "div.se-title-text span",
                "textarea[placeholder*='제목']",
                "div[contenteditable='true'][data-placeholder*='제목']",
                "input[name='title']",
            ]
            title_ok = False
            for sel in title_selectors:
                try:
                    el = editor_frame.locator(sel).first if editor_frame != page else page.locator(sel).first
                    if await el.count() > 0:
                        await el.click()
                        await asyncio.sleep(0.3)
                        await el.fill(title[:100])
                        title_ok = True
                        _log(f"✅ 제목 입력: {title[:40]}...")
                        break
                except Exception:
                    continue
            if not title_ok:
                _log("❌ 제목 필드를 찾을 수 없습니다")
                return False

            # 본문 영역 찾기
            body_selectors = [
                "div.se-component-content div[contenteditable='true']",
                "div[contenteditable='true'].se-text-paragraph",
                "div.post_editor div[contenteditable='true']",
                "div[contenteditable='true']",
            ]
            body_el = None
            for sel in body_selectors:
                try:
                    el = editor_frame.locator(sel).first if editor_frame != page else page.locator(sel).first
                    if await el.count() > 0:
                        body_el = el
                        break
                except Exception:
                    continue

            if not body_el:
                _log("❌ 본문 영역을 찾을 수 없습니다")
                return False

            await body_el.click()
            await asyncio.sleep(0.5)

            # 이미지 삽입
            if images:
                _log(f"📷 이미지 {len(images)}개 삽입 중...")
                for idx, img_url in enumerate(images[:10]):
                    try:
                        await _blog_upload_image(page, editor_frame, img_url, _log)
                        _log(f"   📷 이미지 [{idx+1}/{min(len(images),10)}] 완료")
                        await asyncio.sleep(1)
                    except Exception as e:
                        _log(f"   ⚠️ 이미지 {idx+1} 실패: {e}")

            # 본문 입력 (줄 단위)
            lines = body.split("\n")
            for line in lines:
                if line.strip():
                    await body_el.type(line, delay=10)
                await body_el.press("Enter")
                await asyncio.sleep(0.05)
            _log(f"✅ 본문 입력 완료 ({len(lines)}줄)")

            # 발행 버튼 클릭
            await asyncio.sleep(1)
            publish_selectors = [
                "button:has-text('발행')",
                "button.publish_btn__Y4pat",
                "button[data-testid='publish']",
                "button.se-publish-btn",
                "button:has-text('공개발행')",
            ]
            published = False
            for sel in publish_selectors:
                try:
                    el = editor_frame.locator(sel).first if editor_frame != page else page.locator(sel).first
                    if await el.count() > 0:
                        await el.click()
                        await asyncio.sleep(2)
                        published = True
                        _log("✅ 발행 버튼 클릭")
                        break
                except Exception:
                    continue

            if not published:
                _log("⚠️ 발행 버튼을 찾지 못했습니다 — 수동으로 발행해주세요")

            # 발행 확인 팝업
            confirm_selectors = [
                "button:has-text('확인')",
                "button:has-text('발행')",
                "button.confirm_btn",
            ]
            for sel in confirm_selectors:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.click()
                        await asyncio.sleep(1)
                        break
                except Exception:
                    continue

            _log("🎉 블로그 발행 완료!")
            await asyncio.sleep(2)
            return True

        except Exception as e:
            _log(f"❌ 블로그 발행 오류: {e}")
            logger.exception(e)
            return False
        finally:
            await browser.close()
