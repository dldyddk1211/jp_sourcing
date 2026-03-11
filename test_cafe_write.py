"""
test_cafe_write.py
카페 메뉴 → 글쓰기 버튼 클릭 → 에디터 확인
"""

import asyncio
import json
import os
from playwright.async_api import async_playwright

COOKIE_PATH = "naver_cookies.json"
CAFE_ID     = "28938799"
MENU_ID     = "100"
MENU_URL    = f"https://cafe.naver.com/f-e/cafes/{CAFE_ID}/menus/{MENU_ID}?viewType=L"


async def main():
    if not os.path.exists(COOKIE_PATH):
        print("❌ 쿠키 없음")
        return
    with open(COOKIE_PATH, "r", encoding="utf-8") as f:
        cookies = json.load(f)
    print(f"✅ 쿠키 로드: {len(cookies)}개")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--window-size=1280,900"]
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
        await context.add_cookies(cookies)
        page = await context.new_page()

        # 1단계: 메뉴 페이지 이동
        print(f"\n[1] {MENU_URL}")
        await page.goto(MENU_URL, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
        print(f"    URL: {page.url}")

        # 2단계: 글쓰기 버튼 클릭
        write_selectors = [
            "a:has-text('글쓰기')",
            "button:has-text('글쓰기')",
            "a:has-text('카페 글쓰기')",
            "button:has-text('카페 글쓰기')",
        ]
        clicked = False
        for sel in write_selectors:
            el = page.locator(sel).first
            if await el.count() > 0:
                print(f"[2] 글쓰기 버튼 클릭: {sel}")
                await el.click()
                clicked = True
                break
        if not clicked:
            print("[2] ❌ 글쓰기 버튼 없음 — 페이지 버튼 목록:")
            btns = await page.evaluate("""
                [...document.querySelectorAll('a,button')].map(e=>({
                    tag:e.tagName, text:e.innerText.trim().slice(0,20), href:e.href||'', cls:e.className.slice(0,40)
                })).filter(e=>e.text)
            """)
            for b in btns[:20]:
                print(f"  <{b['tag']}> '{b['text']}' cls='{b['cls']}' href='{b['href']}'")

        await asyncio.sleep(3)
        print(f"    클릭 후 URL: {page.url}")

        # 3단계: iframe 확인
        print("\n[3] iframe 목록:")
        for f in page.frames:
            print(f"  name='{f.name}' url='{f.url}'")

        # 4단계: 제목 입력란 찾기
        print("\n[4] 제목 입력란 찾기:")
        title_sels = [
            "input.textarea_input", "#subject", "input[name='subject']",
            "input[placeholder*='제목']",
        ]
        for sel in title_sels:
            # iframe 내부
            try:
                el = page.frame_locator("iframe#cafe_main").locator(sel).first
                cnt = await el.count()
                if cnt > 0:
                    print(f"  ✅ iframe#cafe_main → {sel}")
                    await el.fill("테스트 제목")
                    print("     제목 입력 성공!")
                    break
            except Exception as e:
                pass
            # 직접
            try:
                el = page.locator(sel).first
                cnt = await el.count()
                if cnt > 0:
                    print(f"  ✅ 직접 → {sel}")
                    break
            except Exception:
                pass
        else:
            print("  ❌ 제목 입력란 없음")

        print("\n⏸️  20초 후 종료...")
        await asyncio.sleep(20)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
