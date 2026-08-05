# -*- coding: utf-8 -*-
"""
행정안전부 도로명주소 검색 API.

검색어 정제는 필수다. 이 API 는 SQL Injection 패턴이 감지되면 승인키를 차단하고,
우리 구조는 LLM 이 손글씨 이미지에서 뽑은 문자열을 그대로 던지는 형태라 위험이 크다.
오인식으로 무엇이 섞일지 알 수 없으므로 블랙리스트가 아니라 화이트리스트로 거른다.

실측(2026-08)에서 확인한 것:
  - 지번 주소도 잘 찾는다
  - 오타 한 글자에도 0건이 나온다 (그래서 우편번호 성공률이 추출 품질의 대리 지표가 된다)
  - 리(里)가 빠진 주소는 여러 건이 잡히고 우편번호가 갈린다 → 통과로 보면 안 된다
"""
import re

import requests

ENDPOINT = "https://business.juso.go.kr/addrlink/addrLinkApi.do"

# 한글·숫자·공백·하이픈·쉼표만 남긴다. 나머지는 전부 버린다.
ALLOWED = re.compile(r"[^가-힣0-9\s\-,]")
MAX_LEN = 80


def clean(keyword):
    """정제 전후를 함께 로그에 남길 수 있도록 결과만 돌려준다."""
    s = ALLOWED.sub(" ", str(keyword or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:MAX_LEN]


def search(keyword, confm_key, timeout=15):
    """반환값은 관찰 패널이 그대로 표시할 수 있는 요약 딕셔너리."""
    raw = str(keyword or "")
    kw = clean(raw)

    result = {"done": True, "raw": raw, "clean": kw,
              "total": 0, "zips": [], "road_addr": None, "zipno": None,
              "error": None, "candidates": []}

    if not kw or not confm_key:
        result["error"] = "검색어 또는 승인키 없음"
        result["done"] = bool(kw and confm_key)
        return result

    try:
        r = requests.get(ENDPOINT, params={
            "confmKey": confm_key, "keyword": kw,
            "currentPage": 1, "countPerPage": 5, "resultType": "json",
        }, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        # 실험 중 앱을 멈추지 않는다. 원문을 화면에 표시하고 넘어간다.
        result["error"] = "%s: %s" % (type(e).__name__, e)
        return result

    common = (data.get("results") or {}).get("common") or {}
    if common.get("errorCode") not in ("0", 0):
        result["error"] = "%s %s" % (common.get("errorCode"), common.get("errorMessage"))
        return result

    juso = (data.get("results") or {}).get("juso") or []
    result["total"] = int(common.get("totalCount") or 0)
    result["zips"] = sorted({j.get("zipNo") for j in juso if j.get("zipNo")})
    result["candidates"] = [
        {"zipNo": j.get("zipNo"), "roadAddr": j.get("roadAddr"), "bdNm": j.get("bdNm")}
        for j in juso
    ]
    if juso:
        result["zipno"] = juso[0].get("zipNo")
        result["road_addr"] = juso[0].get("roadAddr")

    return result
