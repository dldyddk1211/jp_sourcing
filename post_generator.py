"""
post_generator.py
Claude API를 이용한 일본 구매대행 카페 게시글 자동 생성
"""

import logging
import anthropic
from config import ANTHROPIC_API_KEY
from exchange import format_price

logger = logging.getLogger(__name__)

_client = None
NAVER_FORM_URL = "https://naver.me/F2nuqgnV"


def get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def make_title(product: dict) -> str:
    """게시글 제목: 일본구매대행 / {일본어 상품명}"""
    name = product.get("name", "") or product.get("name_ko", "")
    code = product.get("product_code", "")
    title = f"일본구매대행 / {name}"
    if code:
        title += f" {code}"
    return title


def generate_cafe_post(product: dict, price_info: dict) -> dict:
    """
    Claude API로 카페 게시글 제목 + 본문 생성

    Returns:
        {"title": str, "content": str}
    """
    title = make_title(product)

    if not ANTHROPIC_API_KEY:
        logger.warning("⚠️ ANTHROPIC_API_KEY 미설정 — 기본 템플릿 사용")
        return {"title": title, "content": _make_fallback_content(product, price_info)}

    name_ja   = product.get("name", "")
    brand     = product.get("brand_ko") or product.get("brand", "")
    link      = product.get("link", "")
    code      = product.get("product_code", "")
    price_krw = price_info.get("price_final", 0)
    rate      = price_info.get("rate", 0)

    # 사이즈
    sizes = product.get("sizes", [])
    available = [s["size"] for s in sizes if s.get("in_stock")]
    size_text = " / ".join(available) if available else "문의 바랍니다"

    # 상세 설명 (번역본 우선)
    description = product.get("description_ko") or product.get("description", "")

    # 상세 이미지 URL (대표 이미지는 파일 업로드로 처리 — 여기서는 제외)
    images = []
    for url in product.get("detail_images", []):
        if url != product.get("img_url"):
            images.append(url)
    images = images[:8]

    image_tags = "\n".join(f'<img src="{url}">' for url in images) if images else ""

    prompt = f"""당신은 일본 구매대행 전문 카페 운영자입니다.
아래 상품 정보로 네이버 카페 게시글 본문을 작성해주세요.

[상품 정보]
- 브랜드: {brand}
- 상품명(일본어): {name_ja}
- 품번: {code}
- 구매대행가: {format_price(price_krw)} (무료배송)
- 적용 환율: 1엔 = {rate}원
- 주문 가능 사이즈: {size_text}
- 상품 링크: {link}
- 상세 설명: {description[:600] if description else "없음"}

[네이버 문의 폼]
{NAVER_FORM_URL}

[작성 규칙]
1. 아래 형식을 반드시 그대로 따를 것
2. 상품 특징은 일본어 설명을 참고해서 구체적으로 작성
3. 이모지 적절히 활용
4. 친근하고 신뢰감 있는 톤 유지

[출력 형식 - 이 형식 그대로 출력]
---
안녕하세요 서포트 센터장 입니다. ^^

오늘 소개 드릴 제품은
'{name_ja} {code}' 입니다.

👉 일본 현지 정식 유통 제품으로 진행하는 일본구매대행 상품입니다.
가격 : {format_price(price_krw)} (무료배송)
배송일 : 대략 4-7일 소요

주문 가능 사이즈
{size_text}

👉 구매 문의 & 진행 방법
일본구매대행으로 구매 관심 있으신 분은 쪽지 또는 아래 네이버 폼 작성 부탁드려요!!
{NAVER_FORM_URL}

(여기에 상품 상세 스펙/특징을 구체적으로 작성 — 실측 사이즈, 기술 사양, 핵심 포인트 등)
---

위 형식에서 (여기에 ...) 부분만 채워서 출력하세요. 나머지 고정 문구는 그대로 유지."""

    try:
        client = get_client()
        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}]
        ) as stream:
            content = stream.get_final_message().content[0].text.strip()

        # --- 구분자 제거
        if content.startswith("---"):
            content = content[3:].strip()
        if content.endswith("---"):
            content = content[:-3].strip()

        # 이미지 태그 본문 마지막에 추가
        if image_tags:
            content += f"\n\n✅상세 이미지\n{image_tags}"

        logger.info(f"✅ Claude 게시글 생성 완료")
        return {"title": title, "content": content}

    except Exception as e:
        logger.error(f"❌ Claude API 오류: {e} — 기본 템플릿 사용")
        return {"title": title, "content": _make_fallback_content(product, price_info)}


def _make_fallback_content(product: dict, price_info: dict) -> str:
    """Claude API 실패 시 기본 템플릿"""
    name_ja  = product.get("name", "")
    code     = product.get("product_code", "")
    price_krw = price_info.get("price_final", 0)

    sizes = product.get("sizes", [])
    available = [s["size"] for s in sizes if s.get("in_stock")]
    size_text = " / ".join(available) if available else "문의 바랍니다"

    images = []
    for url in product.get("detail_images", []):
        if url != product.get("img_url"):
            images.append(url)
    image_tags = "\n".join(f'<img src="{url}">' for url in images[:8])

    content = f"""안녕하세요 서포트 센터장 입니다. ^^

오늘 소개 드릴 제품은
'{name_ja} {code}' 입니다.

👉 일본 현지 정식 유통 제품으로 진행하는 일본구매대행 상품입니다.
가격 : {format_price(price_krw)} (무료배송)
배송일 : 대략 4-7일 소요

주문 가능 사이즈
{size_text}

👉 구매 문의 & 진행 방법
일본구매대행으로 구매 관심 있으신 분은 쪽지 또는 아래 네이버 폼 작성 부탁드려요!!
{NAVER_FORM_URL}"""

    if image_tags:
        content += f"\n\n✅상세 이미지\n{image_tags}"

    return content.strip()
