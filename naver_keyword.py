"""
naver_keyword.py
네이버 검색광고 API — 키워드 검색량 조회

사용법:
  1. .env 또는 config에 API 키 설정
  2. get_keyword_stats(["루이비통 중고", "프라다 가방"]) 호출
  3. 결과: [{keyword, monthlyPcQcCnt, monthlyMobileQcCnt, total, competition, ...}]

API 문서: https://naver.github.io/searchad-apidoc/
"""

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# API 설정 (config.py 또는 환경변수에서 로드)
_api_config = {
    "api_key": "",       # 액세스 라이선스
    "secret_key": "",    # 비밀키
    "customer_id": "",   # 고객 ID
}

API_BASE = "https://api.searchad.naver.com"


def set_api_keys(api_key, secret_key, customer_id):
    """API 키 설정"""
    _api_config["api_key"] = api_key
    _api_config["secret_key"] = secret_key
    _api_config["customer_id"] = customer_id


def load_api_keys():
    """설정 파일에서 API 키 로드"""
    config_path = os.path.join(os.path.dirname(__file__), "naver_ad_config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                d = json.load(f)
                _api_config.update(d)
                return True
        except Exception:
            pass
    # 환경변수 폴백
    _api_config["api_key"] = os.environ.get("NAVER_AD_API_KEY", "")
    _api_config["secret_key"] = os.environ.get("NAVER_AD_SECRET_KEY", "")
    _api_config["customer_id"] = os.environ.get("NAVER_AD_CUSTOMER_ID", "")
    return bool(_api_config["api_key"])


def save_api_keys(api_key, secret_key, customer_id):
    """API 키를 설정 파일에 저장"""
    config_path = os.path.join(os.path.dirname(__file__), "naver_ad_config.json")
    data = {"api_key": api_key, "secret_key": secret_key, "customer_id": customer_id}
    with open(config_path, "w") as f:
        json.dump(data, f, indent=2)
    _api_config.update(data)


def _generate_signature(timestamp, method, path):
    """HMAC-SHA256 서명 생성 (네이버 검색광고 API 규격)

    공식 문서: https://naver.github.io/searchad-apidoc/#/guides
    서명 = Base64(HMAC-SHA256(secretKey, timestamp + "." + method + "." + path))
    """
    import base64
    sign_str = f"{timestamp}.{method}.{path}"
    secret = _api_config["secret_key"]
    signature = hmac.new(
        secret.encode("utf-8"),
        sign_str.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(signature).decode("utf-8")


def _api_headers(method, path):
    """API 요청 헤더 생성"""
    timestamp = str(int(time.time() * 1000))
    return {
        "Content-Type": "application/json",
        "X-Timestamp": timestamp,
        "X-API-KEY": _api_config["api_key"],
        "X-Customer": _api_config["customer_id"],
        "X-Signature": _generate_signature(timestamp, method, path),
    }


def get_keyword_stats(keywords, show_detail=True):
    """키워드 검색량 조회

    Args:
        keywords: 키워드 리스트 (최대 5개씩 분할 요청)
        show_detail: True면 월간 상세 데이터 포함

    Returns:
        [{keyword, monthlyPcQcCnt, monthlyMobileQcCnt, total,
          compIdx, plAvgDepth, ...}]
    """
    if not _api_config["api_key"]:
        load_api_keys()
    if not _api_config["api_key"]:
        raise ValueError("네이버 검색광고 API 키가 설정되지 않았습니다")

    results = []
    seen = set()  # 중복 키워드 방지
    # 1개씩 조회
    for keyword in keywords:
        # 공백 제거 (네이버 API 규격)
        kw = keyword.replace(" ", "")
        if not kw or kw in seen:
            continue
        seen.add(kw)
        path = "/keywordstool"
        params = {
            "hintKeywords": kw,
            "showDetail": "1" if show_detail else "0",
        }
        try:
            resp = requests.get(
                f"{API_BASE}{path}",
                params=params,
                headers=_api_headers("GET", path),
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("keywordList", [])
                for item in items:
                    pc = item.get("monthlyPcQcCnt", 0)
                    mobile = item.get("monthlyMobileQcCnt", 0)
                    # "< 10" 같은 문자열 처리
                    if isinstance(pc, str):
                        pc = 5 if "< 10" in pc else int(pc.replace(",", ""))
                    if isinstance(mobile, str):
                        mobile = 5 if "< 10" in mobile else int(mobile.replace(",", ""))
                    item["monthlyPcQcCnt"] = pc
                    item["monthlyMobileQcCnt"] = mobile
                    item["total"] = pc + mobile
                    results.append(item)
            else:
                logger.warning(f"[키워드API] 오류 {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"[키워드API] 요청 실패: {e}")

        # API 호출 간격
        time.sleep(0.3)

    return results


def get_related_keywords(keyword, max_results=50):
    """연관 키워드 조회 (입력 키워드 기반 확장)"""
    stats = get_keyword_stats([keyword])
    # 연관 키워드도 같이 반환됨 (hintKeywords에 1개만 넣으면)
    # 검색량 기준 정렬
    stats.sort(key=lambda x: x.get("total", 0), reverse=True)
    return stats[:max_results]


def analyze_brand_keywords(brand_name):
    """브랜드별 유망 키워드 분석"""
    seed_keywords = [
        f"{brand_name} 중고",
        f"{brand_name} 빈티지",
        f"{brand_name} 가방",
        f"{brand_name} 구매대행",
        f"{brand_name} 지갑",
    ]
    results = get_keyword_stats(seed_keywords)
    results.sort(key=lambda x: x.get("total", 0), reverse=True)
    return results


# CLI 사용
if __name__ == "__main__":
    import sys
    load_api_keys()
    if len(sys.argv) > 1:
        keywords = sys.argv[1:]
    else:
        keywords = ["루이비통 중고", "프라다 가방", "일본 명품 구매대행"]
    print(f"키워드 {len(keywords)}개 조회 중...")
    results = get_keyword_stats(keywords)
    print(f"\n{'키워드':<30} {'PC검색':<10} {'모바일':<10} {'합계':<10} {'경쟁도'}")
    print("-" * 80)
    for r in sorted(results, key=lambda x: x["total"], reverse=True):
        comp = r.get("compIdx", "-")
        print(f"{r['relKeyword']:<30} {r['monthlyPcQcCnt']:<10,} {r['monthlyMobileQcCnt']:<10,} {r['total']:<10,} {comp}")
