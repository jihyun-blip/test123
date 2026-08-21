# -*- coding: utf-8 -*-
"""
구글 시트 로더.

코드는 논리 이름으로만 데이터를 요청하고 실제 위치는 SOURCES 가 결정한다.
나중에 파일을 합치거나 더 쪼개도 이 표만 고치면 된다.

컬럼을 하드코딩하지 않는다. 시트에 있는 컬럼을 그대로 읽어 넘긴다.
이 도구의 목적이 "어떤 스키마가 필요한지 발견하는 것"이므로,
컬럼을 하나 추가할 때마다 코드를 고쳐야 한다면 실험 주기가 무너진다.
"""
import io
import os
import time

import pandas as pd
import requests
import streamlit as st

CSV_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={tab}"

# 논리 이름 -> (Secrets 키, 탭 이름)
# 세 축(나라·채널·언어)을 각각 다른 탭이 담당한다.
#   prices    나라 × 채널   판매가·rank
#   shipping  나라 × 채널   배송유형별 배송비
#   product_names / synonyms / units   언어
# 상품 이름과 유사어는 채널이 달라도 같으므로 그 탭에는 channel 이 없다.
SOURCES = {
    "master_products": ("SHEET_ID_MASTER",  "master_products"),
    "prices":          ("SHEET_ID_COUNTRY", "prices"),
    "shipping":        ("SHEET_ID_COUNTRY", "shipping"),
    "product_names":   ("SHEET_ID_COUNTRY", "product_names"),
    "synonyms":        ("SHEET_ID_COUNTRY", "synonyms"),
    "units":           ("SHEET_ID_COUNTRY", "units"),
    "bot_policies":    ("SHEET_ID_POLICY",  "bot_policies"),
}

# 캐시 수명(초). 지침은 실험 중 자주 바뀌므로 짧게, 상품 마스터는 길게.
TTL = {
    "bot_policies":    60,
    "synonyms":        300,
    "product_names":   300,
    "prices":          300,
    "shipping":        300,
    "units":           600,
    "master_products": 1800,
}

# 시스템 동작에 반드시 필요한 최소 컬럼. 없으면 실행을 중단하고 무엇이 없는지 알린다.
# 나머지 컬럼은 있으면 쓰고 없으면 넘어간다.
REQUIRED = {
    "master_products": ["item_code"],
    "prices":          ["country_code", "item_code", "price"],
    "shipping":        ["ship_type", "fee"],
    "product_names":   ["lang", "item_code", "display_name"],
    "synonyms":        ["lang", "item_code", "synonym"],
    "units":           ["lang", "expr", "type"],
    "bot_policies":    ["구분", "키", "값"],
}

# 없거나 비어 있어도 앱이 돌아가는 소스. 폴백 경로가 코드에 있다.
#   shipping       없으면 SHIPPING_FEE / FREE_SHIPPING_THRESHOLD 로 계산
#   product_names  없으면 canonical_name 으로 표시
#   units          없으면 한국어 기본 단위표
# 탭을 아직 안 만든 상태에서 앱이 통째로 멈추면 아무것도 관찰할 수 없다.
OPTIONAL = {"shipping", "product_names", "units"}

# 목 모드에서 읽을 로컬 CSV. 시트 ID 가 없어도 화면이 뜨게 한다.
LOCAL_FALLBACK = {
    "master_products": "sheets/momo_master_products/master_products.csv",
    "prices":          "sheets/momo_country_products/prices.csv",
    "shipping":        "sheets/momo_country_products/shipping.csv",
    "product_names":   "sheets/momo_country_products/product_names.csv",
    "synonyms":        "sheets/momo_country_products/synonyms.csv",
    "units":           "sheets/momo_country_products/units.csv",
    "bot_policies":    "sheets/momo_bot_policies/bot_policies.csv",
}


class SchemaError(Exception):
    """최소 컬럼이 없을 때. 실행을 멈추고 사용자에게 무엇이 없는지 보여준다."""


def secret(key, default=None):
    """Secrets 가 아예 설정되지 않은 환경에서도 앱이 죽지 않게 한다.
    실험 중 앱이 멈추면 관찰 흐름이 끊기므로, 없으면 조용히 기본값으로 넘어간다."""
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


def is_mock():
    """시트 ID 가 하나라도 비어 있으면 목 모드로 본다."""
    for key, _ in SOURCES.values():
        if not secret(key):
            return True
    return False


@st.cache_data(show_spinner=False)
def _fetch_remote(sheet_id, tab, _ttl_bucket):
    """_ttl_bucket 은 캐시 키를 소스별로 갈라놓기 위한 값이다.

    구글 시트는 이따금 응답이 늦다. 한 번 늦었다고 앱이 멈추면 테스터는 아무것도
    못 하고, 자기가 뭘 잘못했는지 알 방법도 없다. 잠깐 쉬고 다시 물어본다.
    권한 문제(4xx)는 다시 물어도 같은 답이므로 바로 올린다."""
    url = CSV_URL.format(sheet_id=sheet_id, tab=tab)
    last = None
    for attempt in range(3):
        try:
            # 첫 연결이 20초를 넘겨 멈췄다가 다음 시도에 0.7초로 오는 일이 잦다
            r = requests.get(url, timeout=45)
            r.raise_for_status()
            return pd.read_csv(io.StringIO(r.content.decode("utf-8")),
                               dtype=str).fillna("")
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", 0)
            if 400 <= code < 500:
                raise
            last = e
        except Exception as e:
            last = e
        if attempt < 2:
            time.sleep(0.8 * (attempt + 1))
    raise last


@st.cache_data(show_spinner=False)
def _fetch_local(path):
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str).fillna("")


def load(name):
    """논리 이름으로 한 소스를 읽는다. 반환값은 (DataFrame, 출처 문자열)."""
    if name not in SOURCES:
        raise KeyError("알 수 없는 소스: %s" % name)

    secret_key, tab = SOURCES[name]
    sheet_id = secret(secret_key)

    if sheet_id:
        df = _fetch_remote(sheet_id, tab, _ttl_bucket=TTL.get(name, 300))
        origin = "시트 · %s" % tab
    else:
        df = _fetch_local(LOCAL_FALLBACK[name])
        origin = "로컬 CSV (목 모드)"

    missing = [c for c in REQUIRED[name] if c not in df.columns]
    if missing:
        raise SchemaError(
            "%s 에 필수 컬럼이 없습니다: %s\n현재 컬럼: %s"
            % (name, ", ".join(missing), ", ".join(df.columns) or "(없음)")
        )

    return df, origin


def load_all():
    """모든 소스를 읽는다. 실패한 소스는 예외를 담아 돌려주고 앱을 죽이지 않는다.

    선택 소스(OPTIONAL)는 빈 표로 대체하고 경고만 남긴다. 탭을 아직 안 만들었다는
    이유로 앱 전체가 멈추면 나머지를 관찰할 방법이 없어진다."""
    data, origins, errors, warnings = {}, {}, {}, {}
    for name in SOURCES:
        try:
            data[name], origins[name] = load(name)
        except Exception as e:
            if name in OPTIONAL:
                data[name] = pd.DataFrame()
                origins[name] = "없음 (폴백)"
                warnings[name] = e
            else:
                errors[name] = e
    return data, origins, errors, warnings


def clear_cache():
    """DB 새로고침 버튼이 호출한다."""
    _fetch_remote.clear()
    _fetch_local.clear()
