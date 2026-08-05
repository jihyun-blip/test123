# -*- coding: utf-8 -*-
"""
기능 B 챗봇 테스트 도구

구현 순서 1~2번: 구글 시트 로더 + 지침 DB 파서.
API 키가 없어도 화면이 뜨고 조작이 가능한 목 모드로 동작한다.
"""
import pandas as pd
import streamlit as st

from lib import policies as pol
from lib import sheets

st.set_page_config(page_title="기능 B 챗봇 테스트", page_icon="🧪", layout="wide")

TESTERS = ["이지현", "김경민"]

# ------------------------------------------------------------------ 상단
head = st.columns([2, 2, 2, 1.2])
tester = head[0].selectbox("테스터", TESTERS)
mode = head[1].radio("지식 수준", ["전체", "축소"], horizontal=True,
                     help="축소 모드는 외부 개발사가 실제로 갖게 될 수준을 재현합니다")
model = head[2].selectbox("모델", sheets.secret("MODELS", ["(목 모드)"]))
if head[3].button("DB 새로고침", use_container_width=True):
    sheets.clear_cache()
    st.rerun()

if sheets.is_mock():
    st.info("**목 모드** — 시트 ID가 설정되지 않아 로컬 CSV를 읽고 있습니다. "
            "Streamlit Secrets에 `SHEET_ID_MASTER`, `SHEET_ID_COUNTRY`, `SHEET_ID_POLICY`를 넣으면 시트를 직접 읽습니다.")

data, origins, errors = sheets.load_all()

# 최소 컬럼이 없으면 여기서 멈춘다. 무엇이 없는지 알리고 진행하지 않는다.
if errors:
    for name, err in errors.items():
        st.error("**%s** 를 읽지 못했습니다\n\n```\n%s\n```" % (name, err))
    st.stop()

st.divider()

# ------------------------------------------------------------------ 로드 상태
st.subheader("데이터 로드 상태")
cols = st.columns(len(data))
for col, (name, df) in zip(cols, data.items()):
    col.metric(name, "%d행" % len(df), origins[name])
    col.caption("컬럼: " + ", ".join(df.columns))

st.caption("컬럼은 하드코딩되어 있지 않습니다. 시트에 컬럼을 추가하고 **DB 새로고침**을 누르면 "
           "다음 대화부터 그 정보가 챗봇에게 전달됩니다.")

# ------------------------------------------------------------------ 지침
st.divider()
st.subheader("지침 DB")

P = pol.Policies(data["bot_policies"])

for w in P.validate():
    st.warning(w)

left, right = st.columns([3, 2])

with left:
    st.dataframe(P.summary(), use_container_width=True, hide_index=True, height=420)
    st.caption("**소비처** — 코드가 읽는 항목은 흐름과 계산을 바꾸고, 프롬프트 항목은 말투와 태도를 바꿉니다. "
               "**잠금** 행(배송정책·결제정보)은 실험 변수가 아니라 고정값이라 편집할 수 없습니다.")

with right:
    st.markdown("**견적 계산에 쓰이는 값**")
    st.write({
        "무료배송 기준": f"{P.get_int('FREE_SHIPPING_THRESHOLD'):,}원" if P.get("FREE_SHIPPING_THRESHOLD") else "미설정",
        "기본 배송비": f"{P.get_int('SHIPPING_FEE'):,}원" if P.get("SHIPPING_FEE") else "미설정",
    })

    flags = P.flags
    st.markdown("**플래그 %d개** — 결과 화면 체크리스트가 여기서 자동 생성됩니다" % len(flags))
    st.dataframe(
        pd.DataFrame([{"키": k, "값": r.get("값", "")} for k, r in flags.items()]),
        use_container_width=True, hide_index=True, height=260,
    )

# ------------------------------------------------------------------ 유사어 충돌
st.divider()
st.subheader("유사어 충돌 미리보기")
st.caption("2개 이상 상품에 걸리는 표현입니다. 대화에서 이 표현이 나오면 AMBIGUOUS_ALIAS 가 떠야 합니다.")

syn = data["synonyms"]
master = data["master_products"]
name_of = dict(zip(master["item_code"], master.get("canonical_name", master["item_code"])))

grouped = syn.groupby("synonym")["item_code"].apply(lambda s: sorted(set(s)))
collide = grouped[grouped.apply(len) > 1]

if len(collide):
    st.dataframe(
        pd.DataFrame([
            {"표현": a, "걸리는 상품": " ↔ ".join("%s %s" % (c, name_of.get(c, "")) for c in cs)}
            for a, cs in collide.items()
        ]),
        use_container_width=True, hide_index=True,
    )
else:
    st.write("충돌하는 유사어가 없습니다.")

# 정식명이면서 다른 상품의 유사어이기도 한 표현 — EXACT_NAME_PRIORITY 가 여기서 갈린다
overlap = []
for code, cname in name_of.items():
    others = [c for c in grouped.get(cname, []) if c != code]
    if others:
        overlap.append({
            "표현": cname,
            "정식명": "%s %s" % (code, cname),
            "유사어로도 걸림": ", ".join("%s %s" % (c, name_of.get(c, "")) for c in others),
        })

if overlap:
    st.markdown("**정식명이면서 다른 상품의 유사어이기도 한 표현** — "
                "`EXACT_NAME_PRIORITY = %s` 규칙이 여기서 갈립니다." % P.get("EXACT_NAME_PRIORITY", "미설정"))
    st.dataframe(pd.DataFrame(overlap), use_container_width=True, hide_index=True)

st.divider()
st.caption("테스터: %s · 지식 수준: %s 모드 · 모델: %s" % (tester, mode, model))
