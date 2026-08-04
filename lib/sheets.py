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

import pandas as pd
import requests
import streamlit as st

CSV_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={tab}"

# 논리 이름 -> (Secrets 키, 탭 이름)
SOURCES = {
    "master_products":  ("SHEET_ID_MASTER",  "master_products"),
    "country_products": ("SHEET_ID_COUNTRY", "country_products"),
    "synonyms":         ("SHEET_ID_COUNTRY", "synonyms"),
    "bot_policies":     ("SHEET_ID_POLICY",  "bot_policies"),
}

# 캐시 수명(초). 지침은 실험 중 자주 바뀌므로 짧게, 상품 마스터는 길게.
TTL = {
    "bot_policies":     60,
    "synonyms":         300,
    "country_products": 300,
    "master_products":  1800,
}

# 시스템 동작에 반드시 필요한 최소 컬럼. 없으면 실행을 중단하고 무엇이 없는지 알린다.
# 나머지 컬럼은 있으면 쓰고 없으면 넘어간다.
REQUIRED = {
    "master_products":  ["item_code"],
    "country_products": ["item_code", "display_name", "price"],
    "synonyms":         ["item_code", "synonym"],
    "bot_policies":     ["구분", "키", "값"],
}

# 목 모드에서 읽을 로컬 CSV. 시트 ID 가 없어도 화면이 뜨게 한다.
LOCAL_FALLBACK = {
    "master_products":  "sheets/momo_master_products/master_products.csv",
    "country_products": "sheets/momo_country_products/country_products.csv",
    "synonyms":         "sheets/momo_country_products/synonyms.csv",
    "bot_policies":     "sheets/momo_bot_policies/bot_policies.csv",
}


class SchemaError(Exception):
    """최소 컬럼이 없을 때. 실행을 멈추고 사용자에게 무엇이 없는지 보여준다."""


def is_mock():
    """시트 ID 가 하나라도 비어 있으면 목 모드로 본다."""
    for key, _ in SOURCES.values():
        if not st.secrets.get(key):
            return True
    return False


@st.cache_data(show_spinner=False)
def _fetch_remote(sheet_id, tab, _ttl_bucket):
    """_ttl_bucket 은 캐시 키를 소스별로 갈라놓기 위한 값이다."""
    url = CSV_URL.format(sheet_id=sheet_id, tab=tab)
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.content.decode("utf-8")), dtype=str).fillna("")


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
    sheet_id = st.secrets.get(secret_key)

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
    """모든 소스를 읽는다. 실패한 소스는 예외를 담아 돌려주고 앱을 죽이지 않는다."""
    data, origins, errors = {}, {}, {}
    for name in SOURCES:
        try:
            data[name], origins[name] = load(name)
        except Exception as e:
            errors[name] = e
    return data, origins, errors


def clear_cache():
    """DB 새로고침 버튼이 호출한다."""
    _fetch_remote.clear()
    _fetch_local.clear()
