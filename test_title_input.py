"""
제목 입력란 단계별 테스트
python test_title_input.py
"""
import asyncio
import json
import os
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

COOKIE_PATH = "naver_cookies.json"
CAFE_ID = "28938799"
CAFE_MENU_ID = "100"
TEST_TITLE = "테스트 제목입니다 12345"


def load_cookies():
    if not os.path.exists(COOKIE_PATH):
        print("❌ naver_cookies.json 없음")
        return []
    with open(COOKIE_PATH, encoding="utf-8") as f:
        return json.load(f)


async def main():
    cookies = load_cookies()
    if not cookies:
        return

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, args=["--window-size=1280,900"])
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="ko-KR",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            permissions=["clipboard-read", "clipboard-write"],
        )
        await ctx.add_cookies(cookies)
        page = await ctx.new_page()

        # 1. 카페 메뉴 이동
        menu_url = f"https://cafe.naver.com/f-e/cafes/{CAFE_ID}/menus/{CAFE_MENU_ID}"
        print(f"\n[1] 카페 메뉴 이동: {menu_url}")
        await page.goto(menu_url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
        print(f"    현재 URL: {page.url}")

        # 2. 글쓰기 버튼 클릭
        print("\n[2] 글쓰기 버튼 탐색...")
        write_selectors = [
            "a:has-text('카페 글쓰기')",
            "button:has-text('카페 글쓰기')",
            "a:has-text('글쓰기')",
            "button:has-text('글쓰기')",
        ]
        clicked = False
        for sel in write_selectors:
            el = page.locator(sel).first
            if await el.count() > 0:
                txt = (await el.inner_text()).strip()
                print(f"    FOUND: {sel!r} => {txt!r}")
                await el.click()
                clicked = True
                print(f"    클릭 완료")
                break
        if not clicked:
            print("    ❌ 글쓰기 버튼 없음")
            await asyncio.sleep(5)
            await browser.close()
            return

        # 3. 에디터 로딩 대기
        print("\n[3] 에디터 렌더링 대기 (최대 15초)...")
        try:
            await page.wait_for_selector(
                "textarea.textarea_input, textarea[placeholder*='제목']",
                timeout=15000
            )
            print("    ✅ 에디터 로딩 완료")
        except PlaywrightTimeout:
            print("    ❌ 15초 후에도 에디터 없음")
            await asyncio.sleep(5)
            await browser.close()
            return

        # 4. 현재 DOM에서 textarea 정보
        print("\n[4] 현재 textarea 목록 (메인 프레임):")
        textareas = page.locator("textarea")
        cnt = await textareas.count()
        print(f"    textarea 총 {cnt}개")
        for i in range(cnt):
            el = textareas.nth(i)
            cls = await el.get_attribute("class") or ""
            ph = await el.get_attribute("placeholder") or ""
            vis = await el.is_visible()
            print(f"    [{i}] class={cls!r}  placeholder={ph!r}  visible={vis}")

        # 5. 프레임(iframe) 목록 확인
        print("\n[5] iframe 목록:")
        frames = page.frames
        print(f"    총 {len(frames)}개 프레임")
        for i, frame in enumerate(frames):
            print(f"    [{i}] url={frame.url[:80]}")
            # contenteditable body 확인
            try:
                body = frame.locator("body[contenteditable='true']")
                body_cnt = await body.count()
                if body_cnt > 0:
                    print(f"         ★ contenteditable body 있음! ← 본문 에디터 iframe")
            except Exception:
                pass

        # 6. 제목 입력 테스트
        print(f"\n[6] 제목 입력 테스트: {TEST_TITLE!r}")
        title_el = page.locator("textarea.textarea_input").first
        if await title_el.count() == 0:
            title_el = page.locator("textarea[placeholder*='제목']").first

        if await title_el.count() == 0:
            print("    ❌ 제목 textarea 없음")
            await asyncio.sleep(10)
            await browser.close()
            return

        print("    textarea 발견 — fill() 시도")
        await title_el.click()
        await asyncio.sleep(0.5)
        await title_el.fill(TEST_TITLE)
        await asyncio.sleep(0.5)
        val = await title_el.input_value()
        print(f"    input_value() = {val!r}")

        if val.strip():
            print(f"    ✅ fill() 성공!")
        else:
            print("    ⚠️ fill() 실패 — keyboard.type 시도")
            await title_el.click()
            await asyncio.sleep(0.3)
            await page.keyboard.type(TEST_TITLE, delay=50)
            await asyncio.sleep(0.5)
            val2 = await title_el.input_value()
            if val2.strip():
                print(f"    ✅ keyboard.type 성공: {val2[:50]}")
            else:
                print("    ❌ keyboard.type도 실패")

        # 7. 본문 에디터 (iframe body) 입력 테스트
        print("\n[7] 본문 에디터 입력 테스트 (iframe body):")
        content_frame = None
        for frame in page.frames:
            try:
                body = frame.locator("body[contenteditable='true']")
                if await body.count() > 0:
                    content_frame = frame
                    print(f"    ★ 에디터 iframe 발견: {frame.url[:60]}")
                    break
            except Exception:
                continue

        if content_frame:
            body_el = content_frame.locator("body")
            await body_el.click()
            await asyncio.sleep(0.5)
            await page.keyboard.type("테스트 본문입니다.", delay=30)
            await asyncio.sleep(0.5)
            inner = await body_el.inner_text()
            print(f"    body inner_text = {inner[:50]!r}")
            if inner.strip():
                print("    ✅ 본문 입력 성공!")
            else:
                print("    ⚠️ 본문 입력 실패")
        else:
            print("    contenteditable iframe 없음 — .se-content 시도")
            se = page.locator(".se-content").first
            if await se.count() > 0:
                await se.click()
                await page.keyboard.type("테스트 본문입니다.", delay=30)
                print("    .se-content 클릭 후 타이핑 시도")
            else:
                print("    ❌ 본문 에디터를 찾지 못함")

        print("\n[완료] 15초 후 브라우저 닫힘 (화면 직접 확인하세요)")
        await asyncio.sleep(15)
        await browser.close()


asyncio.run(main())
