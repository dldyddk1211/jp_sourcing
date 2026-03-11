"""
특정 상품 URL 상세 수집 테스트
python test_product_detail.py
"""
import asyncio
import json
import re
import logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

TARGET_URL = "https://www.supersports.com/ja-jp/xebio/products/A-10893434501/"


async def scrape_detail(url: str) -> dict:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        result = {}

        # ── 상품명 ──────────────────────────────
        for sel in ["h1", "[class*='productName']", "[class*='product-name']", "[class*='name']"]:
            el = page.locator(sel).first
            if await el.count() > 0:
                txt = (await el.inner_text()).strip()
                if txt and len(txt) > 2:
                    result["name"] = txt
                    break

        # ── 브랜드 ──────────────────────────────
        for sel in ["[class*='brand']", "[class*='Brand']"]:
            el = page.locator(sel).first
            if await el.count() > 0:
                txt = (await el.inner_text()).strip()
                if txt and len(txt) < 50:
                    result["brand"] = txt
                    break

        # ── 가격 ────────────────────────────────
        for sel in ["[class*='price']", "[class*='Price']"]:
            items = page.locator(sel)
            cnt = await items.count()
            for i in range(cnt):
                txt = (await items.nth(i).inner_text()).strip()
                nums = re.findall(r'[\d,]+', txt)
                if nums:
                    val = int(nums[0].replace(",", ""))
                    if val > 100:
                        result.setdefault("price_jpy", val)
                        break

        # ── 품번 ────────────────────────────────
        try:
            spec_titles = page.locator("span[class*='title']")
            cnt = await spec_titles.count()
            for i in range(cnt):
                txt = (await spec_titles.nth(i).inner_text()).strip()
                if "品番" in txt or "メーカー" in txt:
                    parent = spec_titles.nth(i).locator("xpath=..")
                    desc = parent.locator("span[class*='description']").first
                    if await desc.count() > 0:
                        code = (await desc.inner_text()).strip()
                        if code:
                            result["product_code"] = code
                            break
            if not result.get("product_code"):
                descs = page.locator("span[class*='description']")
                dcnt = await descs.count()
                for i in range(dcnt):
                    val = (await descs.nth(i).inner_text()).strip()
                    if re.match(r'^[A-Z]{1,4}[\d]', val):
                        result["product_code"] = val
                        break
        except Exception as e:
            logger.debug(f"품번: {e}")

        # ── 상세 스펙 전체 수집 ─────────────────
        specs = {}
        try:
            titles = page.locator("span[class*='title']")
            cnt = await titles.count()
            for i in range(cnt):
                title_txt = (await titles.nth(i).inner_text()).strip()
                if not title_txt:
                    continue
                parent = titles.nth(i).locator("xpath=..")
                desc_el = parent.locator("span[class*='description']").first
                if await desc_el.count() > 0:
                    desc_txt = (await desc_el.inner_text()).strip()
                    if desc_txt:
                        specs[title_txt] = desc_txt
        except Exception as e:
            logger.debug(f"스펙: {e}")
        result["specs"] = specs

        # ── 상세 설명 ───────────────────────────
        for sel in ["[class*='description']", ".product-description", "#description"]:
            el = page.locator(sel).first
            if await el.count() > 0:
                txt = (await el.inner_text()).strip()
                if txt and len(txt) > 10:
                    result["description"] = txt[:1000]
                    break

        # ── 사이즈 + 재고 ───────────────────────
        size_selectors = [
            "[class*='size'] button",
            "[class*='size'] li",
            "[class*='size-item']",
            "[class*='sizeList'] li",
            "button[class*='size']",
        ]
        for sel in size_selectors:
            items = page.locator(sel)
            cnt = await items.count()
            if cnt > 0:
                sizes = []
                for i in range(cnt):
                    item = items.nth(i)
                    size_text = re.sub(r'[^\d.]', '', (await item.inner_text()).strip())
                    cls = await item.get_attribute("class") or ""
                    disabled = await item.get_attribute("disabled")
                    in_stock = (
                        "sold" not in cls.lower() and
                        "disable" not in cls.lower() and
                        "unavailable" not in cls.lower() and
                        disabled is None
                    )
                    if size_text:
                        sizes.append({"size": size_text, "in_stock": in_stock})
                if sizes:
                    result["sizes"] = sizes
                    result["in_stock"] = any(s["in_stock"] for s in sizes)
                    break

        # ── 메인 이미지 ──────────────────────────
        for sel in ["[class*='main'] img", "[class*='hero'] img", "[class*='product'] img"]:
            el = page.locator(sel).first
            if await el.count() > 0:
                src = await el.get_attribute("src") or await el.get_attribute("data-src") or ""
                if src and "placeholder" not in src:
                    result["img_url"] = src if src.startswith("http") else ("https:" + src)
                    break

        # ── 상세 이미지 ──────────────────────────
        img_selectors = [
            "[class*='thumbnail'] img",
            "[class*='gallery'] img",
            "[class*='swiper'] img",
            "[class*='images'] img",
        ]
        for sel in img_selectors:
            imgs = page.locator(sel)
            cnt = await imgs.count()
            if cnt > 1:
                urls = []
                for i in range(min(cnt, 8)):
                    src = (await imgs.nth(i).get_attribute("src") or
                           await imgs.nth(i).get_attribute("data-src") or "")
                    if src and "placeholder" not in src:
                        src = src if src.startswith("http") else ("https:" + src if src.startswith("//") else src)
                        urls.append(src)
                if urls:
                    result["detail_images"] = urls
                    break

        # ── 정가 ────────────────────────────────
        for sel in ["[class*='original']", "[class*='regular']", "[class*='before']"]:
            el = page.locator(sel).first
            if await el.count() > 0:
                txt = await el.inner_text()
                nums = re.findall(r'[\d,]+', txt)
                if nums:
                    val = int(nums[0].replace(",", ""))
                    if val > 100:
                        result["original_price"] = val
                        break

        await browser.close()
        return result


async def main():
    print(f"\n[TEST] {TARGET_URL}\n")
    data = await scrape_detail(TARGET_URL)

    out_path = "test_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  => saved to {out_path}")
    print("=" * 60)

    print("\n[수집 항목 요약]")
    fields = [
        ("name",           "상품명(일본어)"),
        ("brand",          "브랜드"),
        ("product_code",   "품번"),
        ("price_jpy",      "세일가(엔)"),
        ("original_price", "정가(엔)"),
        ("in_stock",       "재고여부"),
        ("sizes",          "사이즈목록"),
        ("img_url",        "메인이미지"),
        ("detail_images",  "상세이미지"),
        ("description",    "상세설명"),
        ("specs",          "스펙항목"),
    ]
    for key, label in fields:
        val = data.get(key)
        if val is None:
            status = "X 미수집"
        elif isinstance(val, list):
            status = f"O {len(val)}개" if val else "X 빈 리스트"
        elif isinstance(val, dict):
            status = f"O {len(val)}개 항목" if val else "X 빈 딕셔너리"
        elif isinstance(val, str):
            status = f"O {val[:50]}" if val else "X 빈 문자열"
        else:
            status = f"O {val}"
        print(f"  {label:15s}: {status}")


asyncio.run(main())
