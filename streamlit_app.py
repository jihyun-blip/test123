# -*- coding: utf-8 -*-
"""
기능 B 챗봇 테스트 도구

이 도구의 산출물은 점수가 아니라 "이 컬럼이 필요하다", "이 지침이 필요하다"는 목록이다.
화면은 보고서 / 대화 / 판정 / 데이터 네 탭으로 나뉜다.
"""
import time
from datetime import datetime

import pandas as pd
import streamlit as st

from lib import flags as FL
from lib import juso
from lib import logs as LOG
from lib import llm as LLM
from lib import matching as M
from lib import policies as pol
from lib import reply as RP
from lib import sheets
from lib.order import OrderState

st.set_page_config(page_title="기능 B 챗봇 테스트", page_icon="🧪", layout="wide")

TESTERS = ["이지현", "김경민"]

# 테스터가 대화 끝에 통과·실패를 찍는 항목. 이게 이 도구의 핵심 산출물이다.
VERDICT_FIELDS = [
    ("invoice", "거래명세서 (품목·수량·가격)"),
    ("address", "주소"),
    ("receiver", "수령자명"),
    ("phone", "전화번호"),
]
CAUSE_TAGS = ["추출오류", "매칭오류", "단위오해", "DB에없음", "지침부족", "기타"]


def init():
    ss = st.session_state
    ss.setdefault("state", OrderState())
    ss.setdefault("history", [])      # [{turn, user, bot, out, diff, flags, detect, usage, model}]
    ss.setdefault("images", [])       # 누적 업로드 이미지
    ss.setdefault("ended", False)
    ss.setdefault("records", [])      # 로그 미설정 시 쓰는 세션 내 판정 기록
    ss.setdefault("conv_no", 1)
    ss.setdefault("started_at", now())
    ss.setdefault("log_msgs", [])


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def reset_conversation():
    ss = st.session_state
    ss.state = OrderState()
    ss.history = []
    ss.images = []
    ss.ended = False
    ss.started_at = now()


init()
ss = st.session_state

# ------------------------------------------------------------------ 상단
head = st.columns([1.6, 1.6, 2, 1.1, 1.1])
tester = head[0].selectbox("테스터", TESTERS)
mode_label = head[1].radio("지식 수준", ["전체", "축소"], horizontal=True,
                           help="축소 모드는 외부 개발사가 실제로 갖게 될 수준을 재현합니다")
mode = "full" if mode_label == "전체" else "reduced"
model = head[2].selectbox("모델", sheets.secret("MODELS", ["(목 모드)"]))
if head[3].button("DB 새로고침", use_container_width=True):
    sheets.clear_cache()
    st.rerun()
if head[4].button("대화 초기화", use_container_width=True):
    reset_conversation()
    st.rerun()

data, origins, errors = sheets.load_all()
if errors:
    for name, err in errors.items():
        st.error("**%s** 를 읽지 못했습니다\n\n```\n%s\n```" % (name, err))
    st.stop()

P = pol.Policies(data["bot_policies"])
CAT = M.Catalog(data["master_products"], data["country_products"], data["synonyms"])

API_KEY = sheets.secret("GEMINI_API_KEY")
JUSO_KEY = sheets.secret("JUSO_CONFM_KEY")

tab_report, tab_chat, tab_verdict, tab_data = st.tabs(
    ["📊 보고서", "💬 대화", "✅ 판정", "🗄 데이터"])


# ================================================================== 대화
with tab_chat:
    if ss.ended:
        st.success("대화가 종료되었습니다. **✅ 판정** 탭에서 주문서를 확인하고 통과·실패를 찍어주세요.")

    left, right = st.columns([3, 2])

    with left:
        for h in ss.history:
            with st.chat_message("user"):
                st.write(h["user"])
                refs = h.get("img_refs") or []
                if refs:
                    kinds = {i["ref"]: i.get("kind", "") for i in ss.state.images}
                    thumbs = st.columns(min(len(refs), 6))
                    for col, ref in zip(thumbs, refs):
                        img = next((i for i in ss.images if i["ref"] == ref), None)
                        if img:
                            col.image(img["bytes"], caption="%s %s" % (ref, kinds.get(ref, "")),
                                      use_container_width=True)
            with st.chat_message("assistant"):
                st.write(h["bot"])
                if h.get("latency_ms"):
                    st.caption("%.1f초" % (h["latency_ms"] / 1000))
                if h.get("error"):
                    st.error("LLM 미사용 · 목 모드로 대체됨 — %s" % h["error"])
                    if h.get("raw"):
                        with st.expander("모델이 실제로 돌려준 원문"):
                            st.code(h["raw"][:3000])

        up, prompt = None, None
        if not ss.ended:
            up = st.file_uploader("이미지 첨부 (여러 장 가능)", type=["png", "jpg", "jpeg", "webp"],
                                  accept_multiple_files=True, key="up_%d" % len(ss.history))
            prompt = st.chat_input("고객 발화를 입력하세요")

    if prompt:
        turn = len(ss.history) + 1

        new_imgs = []
        for f in up or []:
            ref = "img_%d" % (len(ss.images) + len(new_imgs) + 1)
            new_imgs.append({"ref": ref, "name": f.name,
                             "bytes": f.getvalue(), "mime": f.type or "image/jpeg"})
        ss.images.extend(new_imgs)

        cand = LLM.candidates_for(prompt, CAT, mode)
        system = LLM.build_system(P, mode)

        # 1차 호출 — 발화에서 구조화된 데이터만 뽑는다
        user = LLM.build_user(prompt, ss.state, CAT, cand, mode, history=ss.history)

        t0 = time.time()
        usage, raw, out, err = {}, "", None, None
        if API_KEY:
            try:
                out, raw, usage = LLM.call(API_KEY, model, system, user, new_imgs)
                if out is None:
                    err = "응답을 JSON 으로 읽지 못했습니다 (finish_reason=%s, %d자)" % (
                        usage.get("finish_reason", "?"), len(raw or ""))
            except Exception as e:
                err = "%s: %s" % (type(e).__name__, e)
        else:
            err = "GEMINI_API_KEY 가 설정되지 않았습니다"

        if out is None:
            out = LLM.mock(prompt, CAT, turn)
            usage = usage or {"input": LLM.estimate_tokens(system + user),
                              "output": 0, "estimated": True}

        # LLM 이 판별한 이미지 종류를 보관한다. 고객은 종류를 알려주지 않는다.
        for meta in out.get("images") or []:
            ref = meta.get("ref")
            if not ref:
                continue
            ss.state.images = [i for i in ss.state.images if i.get("ref") != ref]
            ss.state.images.append(meta)

        latency_ms = int((time.time() - t0) * 1000)

        diff = ss.state.apply(out, turn)
        ss.state.rematch(CAT, P, mode)

        # 전화번호를 이미지에서 뽑았다면 같은 이미지를 한 번 더 읽어 대조한다.
        # 잘못 읽혀도 형식이 유효하면 어떤 검증에도 걸리지 않기 때문이다.
        if API_KEY and new_imgs and ss.state.phone and ss.state.phone.source == "image":
            again = LLM.recheck_phone(API_KEY, model, new_imgs)
            if again and again["values"]:
                ss.state.phone_second = again["values"][0]
                u2 = again.get("usage") or {}
                usage["input"] = (usage.get("input") or 0) + (u2.get("input") or 0)
                usage["output"] = (usage.get("output") or 0) + (u2.get("output") or 0)

        # 주소가 새로 들어왔으면 검증한다. base 만 보내고 detail 은 사람이 확인한다.
        if ss.state.address_base and not ss.state.addr_api.get("done"):
            ss.state.addr_api = juso.search(ss.state.address_base.value, JUSO_KEY)
            ss.state.zipno = ss.state.addr_api.get("zipno")
            ss.state.road_addr = ss.state.addr_api.get("road_addr")

        quote = ss.state.quote(CAT, P)

        # 거래명세서·되물음은 코드가 조립한다. LLM 에게 금액을 맡기지 않는다.
        fixed, kind = RP.build(ss.state, quote, CAT, P, ss.history)
        tail = (out.get("reply") or "").strip() if err is None else ""
        bot = "\n\n".join(x for x in (fixed, tail) if x) or "(응답 없음)"

        fl = FL.evaluate(ss.state, quote, CAT, P, out, mode)
        prev_asked = bool(ss.history and "?" in (ss.history[-1]["bot"] or ""))
        det = FL.detect(bot, ss.state, quote, P, out, prev_asked)

        ss.history.append({
            "turn": turn, "user": prompt, "bot": bot, "fixed": fixed, "kind": kind,
            "img_refs": [i["ref"] for i in new_imgs], "out": out, "raw": raw, "error": err,
            "diff": diff, "flags": fl, "detect": det, "usage": usage, "model": model,
            "at": now(), "latency_ms": latency_ms, "addr_api": dict(ss.state.addr_api or {}),
        })
        st.rerun()

    # ---------------------------------------------------------- 주문 현황
    with right:
        state = ss.state
        quote = state.quote(CAT, P)
        fl = FL.evaluate(state, quote, CAT, P, ss.history[-1]["out"] if ss.history else {}, mode)

        st.markdown("#### 주문 현황")

        if fl:
            for f in fl:
                icon = {"차단": "🛑", "상담원연결": "🙋", "되물음": "❓",
                        "검수필수": "🔍"}.get(f.value, "⚠️")
                st.markdown("%s **%s** · `%s`  \n<small>%s</small>" %
                            (icon, f.key, f.value, f.evidence), unsafe_allow_html=True)
        else:
            st.caption("발생한 플래그 없음")

        st.divider()
        for label, f in (("수령자명", state.receiver), ("전화번호", state.phone)):
            st.markdown("**%s** %s <small>%s</small>" %
                        (label, f.value or "—", f.origin), unsafe_allow_html=True)

        st.markdown("**주소**")
        st.markdown("추출: %s <small>%s</small>" %
                    (state.address_base.value or "—", state.address_base.origin),
                    unsafe_allow_html=True)
        st.markdown("상세: %s" % (state.address_detail.value or "—"))
        if state.road_addr:
            st.markdown("API: %s  \n우편번호: **%s**" % (state.road_addr, state.zipno))
        elif state.addr_api.get("done"):
            st.markdown("API: 검색 결과 없음")

        st.divider()
        if quote["rows"]:
            st.dataframe(pd.DataFrame(quote["rows"]), use_container_width=True, hide_index=True)
            st.markdown("소계 **%s원** + 배송비 **%s원** = 합계 **%s**" % (
                f"{quote['subtotal']:,}", f"{quote['shipping']:,}",
                f"{quote['total']:,}원" if quote["total"] is not None else "확정 차단"))
        else:
            st.caption("담긴 품목 없음")

        # 대화는 자연스럽게 끝나므로 LLM 은 종료를 알지 못한다. 테스터가 직접 끊는다.
        st.divider()
        if not ss.ended:
            if st.button("🧾 상담 완료 — 주문서 확정", type="primary", use_container_width=True,
                         disabled=not ss.history):
                ss.ended = True
                st.rerun()
        else:
            st.info("종료됨 · 판정 탭으로 이동")

    # ---------------------------------------------------------- 관찰 패널
    if ss.history:
        h = ss.history[-1]
        with st.expander("이번 턴 관찰 패널", expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                st.caption("누적 상태 변화")
                st.write(h["diff"] or "변화 없음")
                st.caption("자동 감지")
                if h["detect"]:
                    st.dataframe(pd.DataFrame(h["detect"]), use_container_width=True,
                                 hide_index=True)
                else:
                    st.write("없음")
                st.caption("결핍 로그 (missing_info)")
                st.write(h["out"].get("missing_info") or "없음")
            with c2:
                st.caption("LLM 원본 응답")
                st.json(h["out"], expanded=False)
                st.caption("참조한 데이터 (used_refs)")
                st.write(h["out"].get("used_refs") or "없음")
                st.caption("주소 API")
                st.write(ss.state.addr_api or "미호출")
                st.caption("이미지 판별")
                st.write(ss.state.images or "업로드 없음")
                if ss.state.phone_second:
                    st.caption("전화번호 2차 판독: %s" % ss.state.phone_second)
            u = h["usage"]
            st.caption("토큰 입력 %s / 출력 %s %s · 모델 %s · 지식수준 %s" % (
                u.get("input"), u.get("output"),
                "(추정)" if u.get("estimated") else "", h["model"], mode_label))


# ================================================================== 판정
with tab_verdict:
    if not ss.ended:
        st.info("대화 탭에서 **상담 완료** 를 눌러야 판정할 수 있습니다.")
    else:
        state = ss.state
        quote = state.quote(CAT, P)
        st.markdown("### 최종 주문서")

        c1, c2 = st.columns([2, 1])
        with c1:
            if quote["rows"]:
                st.dataframe(pd.DataFrame(quote["rows"]), use_container_width=True,
                             hide_index=True)
            else:
                st.caption("품목 없음")
        with c2:
            st.write({
                "수령자명": state.receiver.value or "—",
                "전화번호": state.phone.value or "—",
                "주소": state.address_base.value or "—",
                "상세주소": state.address_detail.value or "—",
                "우편번호": state.zipno or "—",
                "합계": f"{quote['total']:,}원" if quote["total"] is not None else "확정 차단",
            })

        st.divider()
        st.markdown("### 항목별 판정")
        st.caption("주문서가 자동으로 제대로 입력되었는지 항목별로 찍어주세요. "
                   "실패라면 원인까지 골라야 무엇을 고쳐야 하는지가 남습니다.")

        verdicts = {}
        for key, label in VERDICT_FIELDS:
            col = st.columns([2, 1.4, 2])
            col[0].markdown("**%s**" % label)
            v = col[1].radio(label, ["통과", "실패"], horizontal=True,
                             key="v_%s" % key, label_visibility="collapsed")
            cause = col[2].selectbox("원인", CAUSE_TAGS, key="c_%s" % key,
                                     label_visibility="collapsed",
                                     disabled=(v == "통과"))
            verdicts[key] = {"verdict": v, "cause": None if v == "통과" else cause}

        note = st.text_area("관찰 메모", placeholder="무엇이 부족했는지, 어떤 지침이 필요한지")

        if st.button("판정 저장", type="primary"):
            sources = {
                "invoice": "image" if any(l.source == "image" for l in state.lines) else "text",
                "address": state.address_base.source or "text",
                "receiver": state.receiver.source or "text",
                "phone": state.phone.source or "text",
            }
            finals = {
                "invoice": "; ".join("%s×%s" % (r["매칭"], r["수량"]) for r in quote["rows"]),
                "address": "%s %s" % (state.address_base.value or "",
                                      state.address_detail.value or ""),
                "receiver": state.receiver.value or "",
                "phone": state.phone.value or "",
            }
            for k in verdicts:
                verdicts[k]["final_value"] = finals.get(k, "")

            conv_id = "%s-%s-%03d" % (tester, ss.started_at.replace(" ", "_").replace(":", ""),
                                      ss.conv_no)

            rec = {
                "conversation_id": conv_id, "conv_no": ss.conv_no, "tester": tester,
                "mode": mode_label, "model": model,
                "turns": len(ss.history), "images": len(ss.images),
                "tokens_in": sum(h["usage"].get("input", 0) or 0 for h in ss.history),
                "tokens_out": sum(h["usage"].get("output", 0) or 0 for h in ss.history),
                "verdicts": verdicts, "sources": sources, "note": note,
            }
            ss.records.append(rec)

            # 시트에도 남긴다. 실패해도 앱을 멈추지 않고 세션 기록은 그대로 유지된다.
            msgs = []
            if LOG.configured():
                bundle = LOG.build_rows(
                    conv_id, tester, mode_label, model, state, quote, ss.history,
                    verdicts, sources, note,
                    policy_version=sheets.secret("POLICY_VERSION", "sheet-live"),
                    started_at=ss.started_at, ended_at=now(),
                    flag_settings={k: r.get("값") for k, r in P.flags.items()})
                for tab, rows in bundle.items():
                    ok, msg = LOG.write(tab, rows)
                    msgs.append(("ok" if ok else "err", "%s — %s" % (tab, msg)))
                LOG.clear_cache()
            else:
                msgs.append(("err", "로그 미설정 — 이 세션 안에서만 집계됩니다"))
            ss.log_msgs = msgs

            ss.conv_no += 1
            reset_conversation()
            st.rerun()


# ================================================================== 보고서
def records_from_sheet():
    """로그 시트에서 두 테스터의 기록을 함께 읽어 보고서용 형태로 맞춘다."""
    convs, e1 = LOG.read("conversations")
    fvs, e2 = LOG.read("field_verdicts")
    if e1 or e2 or convs.empty:
        return [], (e1 or e2)

    by_conv = {}
    if not fvs.empty:
        for r in fvs.to_dict("records"):
            by_conv.setdefault(r.get("conversation_id"), []).append(r)

    def num(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0

    out = []
    for c in convs.to_dict("records"):
        cid = c.get("conversation_id")
        verdicts, sources = {}, {}
        for r in by_conv.get(cid, []):
            k = r.get("field_type")
            verdicts[k] = {"verdict": r.get("verdict") or "",
                           "cause": r.get("cause_tag") or None}
            sources[k] = r.get("source") or "text"
        if not verdicts:
            continue
        out.append({
            "conv_no": cid, "tester": c.get("tester_name", ""),
            "mode": c.get("knowledge_mode", ""), "model": c.get("model", ""),
            "turns": num(c.get("turn_count")), "images": num(c.get("image_count")),
            "tokens_in": 0, "tokens_out": 0,
            "verdicts": verdicts, "sources": sources, "note": c.get("note", ""),
        })
    return out, None


with tab_report:
    for kind, msg in ss.log_msgs:
        (st.success if kind == "ok" else st.warning)(msg)

    if LOG.configured():
        recs, log_err = records_from_sheet()
        if log_err:
            st.warning("로그 시트를 읽지 못해 이 세션 기록만 보여줍니다 — %s" % log_err)
            recs = ss.records
        else:
            st.caption("로그 시트에서 읽었습니다. 두 테스터의 기록이 함께 집계됩니다.")
            # 토큰은 시트에 대화 단위로 남기지 않으므로 이 세션 값으로 보정한다
            tok = {r["conversation_id"]: r for r in ss.records}
            for r in recs:
                m = tok.get(r["conv_no"])
                if m:
                    r["tokens_in"], r["tokens_out"] = m["tokens_in"], m["tokens_out"]
    else:
        recs = ss.records
        st.warning("Apps Script 로그가 설정되지 않아 **이 브라우저 세션 안에서만** 집계됩니다. "
                   "새로고침하면 사라지니 아래 CSV 다운로드로 받아두세요.")

    if not recs:
        st.info("아직 판정된 대화가 없습니다. **💬 대화** 탭에서 대화를 진행하고 "
                "**상담 완료 → 판정 저장** 을 하면 여기에 집계됩니다.")
    else:
        tin = sum(r["tokens_in"] for r in recs)
        tout = sum(r["tokens_out"] for r in recs)
        cost = sum(LLM.cost_usd(r["model"], r["tokens_in"], r["tokens_out"]) for r in recs)

        c = st.columns(5)
        c[0].metric("테스트한 대화", "%d건" % len(recs))
        c[1].metric("LLM 호출", "%d회" % sum(r["turns"] for r in recs))
        c[2].metric("업로드 이미지", "%d장" % sum(r["images"] for r in recs))
        c[3].metric("토큰 (추정)", f"{tin + tout:,}",
                    "입력 %s / 출력 %s" % (f"{tin:,}", f"{tout:,}"))
        c[4].metric("예상 비용", "$%.3f" % cost)

        st.divider()
        st.markdown("### 항목별 성공률")
        rows = []
        for key, label in VERDICT_FIELDS:
            ok = sum(1 for r in recs if r["verdicts"][key]["verdict"] == "통과")
            rows.append({"항목": label, "통과": ok, "실패": len(recs) - ok,
                         "성공률": "%.0f%%" % (100 * ok / len(recs))})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.divider()
        st.markdown("### 입력 유형별 성공률")
        st.caption("같은 항목이라도 텍스트에서 왔는지 이미지에서 왔는지에 따라 난이도가 다릅니다.")
        cross = []
        for key, label in VERDICT_FIELDS:
            for src, src_label in (("text", "텍스트"), ("image", "이미지")):
                sub = [r for r in recs if r["sources"][key] == src]
                if not sub:
                    continue
                ok = sum(1 for r in sub if r["verdicts"][key]["verdict"] == "통과")
                cross.append({"항목": label, "입력 유형": src_label, "건수": len(sub),
                              "통과": ok, "실패": len(sub) - ok,
                              "성공률": "%.0f%%" % (100 * ok / len(sub))})
        if cross:
            st.dataframe(pd.DataFrame(cross), use_container_width=True, hide_index=True)
        else:
            st.caption("데이터 없음")

        st.divider()
        cc = st.columns(2)
        with cc[0]:
            st.markdown("### 실패 원인 순위")
            causes = {}
            for r in recs:
                for key, _ in VERDICT_FIELDS:
                    cz = r["verdicts"][key]["cause"]
                    if cz:
                        causes[cz] = causes.get(cz, 0) + 1
            if causes:
                st.dataframe(pd.DataFrame(
                    [{"원인": k, "건수": v} for k, v in sorted(causes.items(), key=lambda x: -x[1])]),
                    use_container_width=True, hide_index=True)
            else:
                st.caption("실패 없음")

        with cc[1]:
            st.markdown("### 지식 수준 모드별 비교")
            mrows = []
            for md in ("전체", "축소"):
                sub = [r for r in recs if r["mode"] == md]
                if not sub:
                    continue
                total = len(sub) * len(VERDICT_FIELDS)
                ok = sum(1 for r in sub for k, _ in VERDICT_FIELDS
                         if r["verdicts"][k]["verdict"] == "통과")
                mrows.append({"모드": md, "대화": len(sub),
                              "전체 성공률": "%.0f%%" % (100 * ok / total)})
            if mrows:
                st.dataframe(pd.DataFrame(mrows), use_container_width=True, hide_index=True)
            else:
                st.caption("데이터 없음")

        st.divider()
        st.markdown("### 남긴 메모")
        for r in recs:
            if r["note"]:
                st.markdown("- **#%d %s (%s)** — %s" %
                            (r["conv_no"], r["tester"], r["mode"], r["note"]))

        st.download_button("결과 CSV 다운로드",
                           pd.json_normalize(recs).to_csv(index=False).encode("utf-8-sig"),
                           "momo_test_results.csv", "text/csv")


# ================================================================== 데이터
with tab_data:
    st.caption("시트 수정이 반영됐는지 확인하는 화면입니다. 실험 결과와는 무관합니다.")

    cols = st.columns(len(data))
    for col, (name, df) in zip(cols, data.items()):
        col.metric(name, "%d행" % len(df), origins[name])

    with st.expander("지침 DB", expanded=False):
        for w in P.validate():
            st.warning(w)
        st.dataframe(P.summary(), use_container_width=True, hide_index=True)

    with st.expander("유사어 충돌 — AMBIGUOUS_ALIAS 가 떠야 할 지점", expanded=False):
        syn, master = data["synonyms"], data["master_products"]
        name_of = dict(zip(master["item_code"],
                           master.get("canonical_name", master["item_code"])))
        grouped = syn.groupby("synonym")["item_code"].apply(lambda s: sorted(set(s)))
        collide = grouped[grouped.apply(len) > 1]
        if len(collide):
            st.dataframe(pd.DataFrame([
                {"표현": a, "걸리는 상품": " ↔ ".join("%s %s" % (c, name_of.get(c, "")) for c in cs)}
                for a, cs in collide.items()]), use_container_width=True, hide_index=True)
        else:
            st.write("없음")
