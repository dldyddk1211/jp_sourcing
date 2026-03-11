"""
네이버 카페 글쓰기 에디터 구조 진단
- 글쓰기 버튼 클릭 후 URL 확인
- iframe 유무 확인
- 제목/본문 입력 가능한 selector 찾기

python test_cafe_editor.py
"""
import asyncio
import json
import os
from playwright.async_api import async_playwright

COOKIE_PATH = "naver_cookies.json"
CAFE_ID     = "28938799"
CAFE_MENU_ID = "100"
MENU_URL = f"https://cafe.naver.com/f-e/cafes/{CAFE_ID}/menus/{CAFE_MENU_ID}?viewType=L"


def load_cookies():
    if not os.path.exists(COOKIE_PATH):
        print("naver_cookies.json 없음 — 먼저 네이버 로그인 필요")
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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        await ctx.add_cookies(cookies)
        page = await ctx.new_page()

        print(f"\n[1] 메뉴 페이지 이동: {MENU_URL}")
        await page.goto(MENU_URL, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
        print(f"    현재 URL: {page.url}")

        # 글쓰기 버튼 찾기
        write_selectors = [
            "a:has-text('글쓰기')",
            "button:has-text('글쓰기')",
            "a:has-text('카페 글쓰기')",
            "[class*='WriteButton']",
            "[class*='write']",
        ]
        print("\n[2] 글쓰기 버튼 탐색:")
        for sel in write_selectors:
            try:
                el = page.locator(sel).first
                cnt = await el.count()
                if cnt > 0:
                    txt = (await el.inner_text()).strip()[:30]
                    href = await el.get_attribute("href") or ""
                    print(f"    FOUND  {sel!r} => text={txt!r}  href={href[:60]}")
                else:
                    print(f"    -      {sel!r}")
            except Exception as e:
                print(f"    ERR    {sel!r}: {e}")

        # 글쓰기 클릭
        print("\n[3] 글쓰기 버튼 클릭...")
        clicked = False
        for sel in write_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click()
                    clicked = True
                    print(f"    클릭: {sel!r}")
                    break
            except Exception:
                continue

        if not clicked:
            print("    글쓰기 버튼 클릭 실패")
            await asyncio.sleep(5)
            await browser.close()
            return

        await asyncio.sleep(3)
        print(f"\n[4] 클릭 후 URL: {page.url}")

        # URL 변화 대기 (write 페이지로 이동)
        for _ in range(5):
            if "write" in page.url or "article" in page.url:
                break
            await asyncio.sleep(1)
        print(f"    최종 URL: {page.url}")

        # iframe 확인
        print("\n[5] iframe 확인:")
        frames = page.frames
        print(f"    총 프레임 수: {len(frames)}")
        for i, f in enumerate(frames):
            print(f"    [{i}] name={f.name!r}  url={f.url[:80]}")

        iframe_names = ["cafe_main", "se_iframe", "editor"]
        for name in iframe_names:
            try:
                el = page.locator(f"iframe#{name}, iframe[name='{name}']").first
                cnt = await el.count()
                print(f"    iframe#{name}: {'있음' if cnt else '없음'}")
            except Exception:
                pass

        # 제목 입력 selector 탐색
        print("\n[6] 제목 입력란 탐색:")
        title_selectors = [
            "input.textarea_input",
            "#subject",
            "input[name='subject']",
            "input[placeholder*='제목']",
            "input[type='text']",
            "[class*='title'] input",
            "[class*='Title'] input",
            "[class*='subject'] input",
            "textarea[placeholder*='제목']",
        ]
        for sel in title_selectors:
            try:
                el = page.locator(sel).first
                cnt = await el.count()
                if cnt:
                    ph = await el.get_attribute("placeholder") or ""
                    nm = await el.get_attribute("name") or ""
                    print(f"    FOUND  {sel!r}  placeholder={ph!r}  name={nm!r}")
            except Exception:
                pass

        # 에디터 영역 탐색
        print("\n[7] 에디터 본문 영역 탐색:")
        editor_selectors = [
            ".se-content",
            "[contenteditable=true]",
            ".se2_input",
            ".ProseMirror",
            "[class*='editor']",
            "[class*='Editor']",
            "div[role='textbox']",
        ]
        for sel in editor_selectors:
            try:
                items = page.locator(sel)
                cnt = await items.count()
                if cnt:
                    cls = await items.first.get_attribute("class") or ""
                    print(f"    FOUND  {sel!r}  count={cnt}  class={cls[:60]!r}")
            except Exception:
                pass

        # 이미지 업로드 버튼 탐색
        print("\n[8] 이미지 업로드 버튼 탐색:")
        img_selectors = [
            "button[data-name='image']",
            "button[aria-label*='사진']",
            "button[aria-label*='이미지']",
            "button[title*='사진']",
            "button[title*='이미지']",
            ".se-toolbar button",
            "input[type='file']",
        ]
        for sel in img_selectors:
            try:
                items = page.locator(sel)
                cnt = await items.count()
                if cnt:
                    lbl = await items.first.get_attribute("aria-label") or await items.first.get_attribute("title") or ""
                    print(f"    FOUND  {sel!r}  count={cnt}  label={lbl!r}")
            except Exception:
                pass

        # 등록 버튼 탐색
        print("\n[9] 등록(submit) 버튼 탐색:")
        submit_selectors = [
            "button.BaseButton--submit",
            "button:has-text('등록')",
            "button:has-text('확인')",
            "a.btn_upload",
            "button[class*='submit']",
            "button[type='submit']",
        ]
        for sel in submit_selectors:
            try:
                el = page.locator(sel).first
                if await el.count():
                    txt = (await el.inner_text()).strip()
                    print(f"    FOUND  {sel!r}  text={txt!r}")
            except Exception:
                pass

        print("\n[완료] 10초 후 브라우저 닫힘...")
        await asyncio.sleep(10)
        await browser.close()


asyncio.run(main())
