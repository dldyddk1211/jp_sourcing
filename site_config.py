"""
site_config.py
사이트 / 카테고리 설정 — 크롤링 대상 관리
"""

# ── 사이트 & 카테고리 정의 ─────────────────────
# 구조: SITES[site_id] → { name, base_url, categories: { cat_id: {name, params} } }
#
# URL 생성: base_url + "/products/?" + urlencode(params)
# 예: https://www.supersports.com/ja-jp/xebio/products/?discount=sale
#     https://www.supersports.com/ja-jp/xebio/products/?category=running

SITES = {
    "xebio": {
        "name": "제비오 (Xebio)",
        "domain": "https://www.supersports.com",
        "base_url": "https://www.supersports.com/ja-jp/xebio",
        "scraper": "xebio_search",          # 사용할 스크래퍼 모듈
        "categories": {
            "sale": {
                "name": "세일",
                "name_ja": "セール",
                "params": {"discount": "sale"},
            },
            "running": {
                "name": "런닝",
                "name_ja": "ランニング",
                "params": {"category": "running"},
            },
            "soccer-futsal": {
                "name": "축구/풋살",
                "name_ja": "サッカー・フットサル",
                "params": {"category": "soccer-futsal"},
            },
            "basketball": {
                "name": "농구",
                "name_ja": "バスケットボール",
                "params": {"category": "basketball"},
            },
            "tennis": {
                "name": "테니스",
                "name_ja": "テニス",
                "params": {"category": "tennis"},
            },
            "golf": {
                "name": "골프",
                "name_ja": "ゴルフ",
                "params": {"category": "golf"},
            },
            "training": {
                "name": "트레이닝",
                "name_ja": "トレーニング",
                "params": {"category": "training"},
            },
        },
    },
    # ── 향후 추가 사이트 ──
    # "abc_mart": {
    #     "name": "ABC마트",
    #     "domain": "https://www.abc-mart.net",
    #     "base_url": "https://www.abc-mart.net",
    #     "scraper": "abc_search",
    #     "categories": {
    #         "running": {"name": "런닝", "params": {...}},
    #         "sneakers": {"name": "스니커즈", "params": {...}},
    #     },
    # },
}


def get_site(site_id: str) -> dict:
    """사이트 설정 반환"""
    return SITES.get(site_id)


def get_category(site_id: str, cat_id: str) -> dict:
    """카테고리 설정 반환"""
    site = SITES.get(site_id)
    if not site:
        return None
    return site["categories"].get(cat_id)


def build_url(site_id: str, cat_id: str) -> str:
    """사이트 + 카테고리로 스크래핑 URL 생성"""
    site = SITES.get(site_id)
    if not site:
        return ""
    cat = site["categories"].get(cat_id)
    if not cat:
        return ""
    from urllib.parse import urlencode
    return f"{site['base_url']}/products/?{urlencode(cat['params'])}"


def get_sites_for_ui() -> list:
    """대시보드 UI용 사이트/카테고리 트리 반환"""
    result = []
    for site_id, site in SITES.items():
        cats = []
        for cat_id, cat in site["categories"].items():
            cats.append({
                "id": cat_id,
                "name": cat["name"],
            })
        result.append({
            "id": site_id,
            "name": site["name"],
            "categories": cats,
        })
    return result
