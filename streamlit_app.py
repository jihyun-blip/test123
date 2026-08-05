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

# 배포 반영 여부를 화면에서 바로 확인하기 위한 표시
APP_VERSION = "2026-08-05.11"

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
    ss.setdefault("pending", None)   # 화면에 먼저 띄우고 나서 처리할 발화
    ss.setdefault("saved", None)     # 방금 저장한 대화 id


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def reset_conversation():
    ss = st.session_state
    ss.state = OrderState()
    ss.history = []
    ss.images = []
    ss.ended = False
    ss.started_at = now()
    ss.pending = None


init()
ss = st.session_state

# ------------------------------------------------------------------ 상단
head = st.columns([1.6, 1.6, 2, 1.1, 1.1])
tester = head[0].selectbox("테스터", TESTERS)
mode_label = head[1].radio("지식 수준", ["전체", "축소"], horizontal=True,
                           help="축소 모드는 외부 개발사가 실제로 갖게 될 수준을 재현합니다")
mode = "full" if mode_label == "전체" else "reduced"
model = head[2].selectbox("모델", sheets.secret("MODELS", ["(목 모드)"]))
if head[3].button("DB 새로고침", width="stretch"):
    sheets.clear_cache()
    st.rerun()
if head[4].button("대화 초기화", width="stretch"):
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

st.caption("빌드 %s" % APP_VERSION)

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
                                      width="stretch")
            with st.chat_message("assistant"):
                # 마크다운은 단일 개행을 무시한다. 거래명세서가 한 줄로 붙지 않게 한다.
                st.markdown(h["bot"].replace("\n", "  \n"))
                if h.get("latency_ms"):
                    st.caption("%.1f초" % (h["latency_ms"] / 1000))
                if h.get("error"):
                    st.error("LLM 미사용 · 목 모드로 대체됨 — %s" % h["error"])
                    if h.get("raw"):
                        with st.expander("모델이 실제로 돌려준 원문"):
                            st.code(h["raw"][:3000])

        # 처리 전이라도 고객 발화는 즉시 보여준다. 전송됐는지 몰라 다시 누르는 일을 막는다.
        if ss.pending:
            with st.chat_message("user"):
                st.write(ss.pending["user"])
                if ss.pending["imgs"]:
                    st.caption("첨부: " + ", ".join(i["ref"] for i in ss.pending["imgs"]))
            with st.chat_message("assistant"):
                st.caption("답변 생성 중…")

        up, prompt = None, None
        if not ss.ended and not ss.pending:
            up = st.file_uploader("이미지 첨부 (여러 장 가능)", type=["png", "jpg", "jpeg", "webp"],
                                  accept_multiple_files=True, key="up_%d" % len(ss.history))
            prompt = st.chat_input("고객 발화를 입력하세요")

    if prompt:
        new_imgs = []
        for f in up or []:
            ref = "img_%d" % (len(ss.images) + len(new_imgs) + 1)
            new_imgs.append({"ref": ref, "name": f.name,
                             "bytes": f.getvalue(), "mime": f.type or "image/jpeg"})
        ss.pending = {"user": prompt, "imgs": new_imgs}
        st.rerun()

    if ss.pending:
        prompt = ss.pending["user"]
        new_imgs = ss.pending["imgs"]
        turn = len(ss.history) + 1
        ss.images.extend(new_imgs)

        cand = LLM.candidates_for(prompt, CAT, mode)
        system = LLM.build_system(P, mode)

        # 1차 호출 — 발화에서 구조화된 데이터만 뽑는다
        # 품목이 확정되기 전에는 비어 있는 정보 목록을 넘기지 않는다.
        # 넘기면 LLM 이 되물음과 정보 요청을 한꺼번에 해버린다.
        kind_before = RP.stage(ss.state, ss.state.quote(CAT, P), CAT, P)
        pend_before = None if kind_before in RP.ASK_STAGES else RP.pending(ss.state, P)
        # 추가 구매 권유는 정해진 횟수만. 고객이 받아들이든 아니든 다시 꺼내지 않는다.
        upsell = None
        if ss.state.upsell_shown < P.get_int("UPSELL_MAX_TIMES", 1):
            in_cart = {l.match.code for l in ss.state.lines if l.match and l.match.code}
            upsell = RP.upsell_context(ss.state.quote(CAT, P), P, CAT, exclude=in_cart)
            if upsell:
                ss.state.upsell_shown += 1
        user = LLM.build_user(prompt, ss.state, CAT, cand, mode,
                              history=ss.history, pending=pend_before, upsell=upsell)

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

        # 사진에서 읽은 라벨코드가 item_ops 에 빠져 있으면 코드가 살려낸다
        out = LLM.recover_from_images(out, CAT)

        # 그래도 상품을 하나도 못 건졌는데 상품 사진이 왔다면, 라벨만 따로 읽어본다.
        # 1차 응답의 구조에 기대지 않는 마지막 경로다.
        def _points_to_product(o):
            """이 항목이 실제 상품을 가리키는가. 고객 발화를 상품명으로 잘못 넣은 경우도 걸러낸다."""
            for v in (o.get("label_code"), o.get("name_hint"), o.get("raw_text")):
                if not v:
                    continue
                if M.match({"name_hint": v, "label_code": o.get("label_code")},
                           CAT, P, "full").status != M.NOT_FOUND:
                    return True
            return False

        if new_imgs and not any(_points_to_product(o) for o in (out.get("item_ops") or [])):
            extra = []
            for it in LLM.read_labels(API_KEY, model, new_imgs):
                code = LLM._code_of(it.get("label_code") or it.get("printed_name"), CAT)
                if code:
                    extra.append({"op": "add", "name_hint": CAT.display(code),
                                  "label_code": code, "quantity": None,
                                  "source": "image", "source_ref": it.get("ref")})
            if extra:
                out["item_ops"] = extra

        # LLM 이 판별한 이미지 종류를 보관한다. 고객은 종류를 알려주지 않는다.
        for meta in out.get("images") or []:
            ref = meta.get("ref")
            if not ref:
                continue
            ss.state.images = [i for i in ss.state.images if i.get("ref") != ref]
            ss.state.images.append(meta)
            if meta.get("kind") == "payment" and not ss.state.payment_proof:
                ss.state.payment_proof = ref

        latency_ms = int((time.time() - t0) * 1000)

        diff = ss.state.apply(out, turn, CAT, P)
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

        quote = ss.state.quote(CAT, P)

        # 거래명세서·되물음은 코드가 조립한다. LLM 에게 금액을 맡기지 않는다.
        fixed, kind = RP.build(ss.state, quote, CAT, P, ss.history)
        tail = (out.get("reply") or "").strip() if err is None else ""

        # 고객이 흐름에서 벗어난 말을 했으면 그 답은 살린다.
        # 지침의 SMALLTALK 이 짧은 잡담을 허용하고, SMALLTALK_RETURN 이 복귀를 요구한다.
        digression = out.get("intent") in ("smalltalk", "question", "complaint")

        # 코드가 품목을 되묻는 중이면 LLM 의 덧붙임을 버린다.
        # 한 턴에 질문이 둘이면 고객이 무엇에 답해야 할지 모르고,
        # 확정되지도 않았는데 "담아드렸어요" 같은 말이 섞인다.
        # 다만 잡담·질문에 대한 답은 예외다. 그걸 버리면 사람 말을 무시하는 봇이 된다.
        if not digression and (kind in RP.ASK_STAGES
                               or (kind_before in RP.ASK_STAGES and kind != kind_before)):
            tail = ""

        # 무엇이 비었는지는 코드가 알고, 묻는 문장은 LLM 이 만든다.
        # LLM 이 묻지 않고 넘어가면 흐름이 멈추므로 그때만 대체 문장을 붙인다.
        pend_after = RP.pending(ss.state, P)
        if kind in ("collecting", "invoice") and not tail:
            fb = RP.fallback_ask(pend_after)
            if fb:
                fixed = "\n\n".join(x for x in (fixed, fb) if x)

        # 아직 아무것도 못 받은 단계의 코드 문장은 "무엇을 찾으시냐"는 일반적인 물음이다.
        # LLM 이 이미 답하며 물었다면 같은 질문을 두 번 하는 셈이라 붙이지 않는다.
        if kind == "order_ask" and tail:
            fixed = ""

        # 고객이 흐름에서 벗어난 질문을 했다면 그 답이 먼저 오고,
        # 흐름을 되돌리는 코드 문장이 뒤에 붙어야 자연스럽다.
        order = (tail, fixed) if (digression and tail) else (fixed, tail)
        bot = "\n\n".join(x for x in order if x) or "(응답 없음)"

        # 인사는 봇의 첫 발화 맨 앞에. 무엇을 말하든 그 위에 온다.
        if not ss.history:
            bot = RP.GREETING + "\n" + bot

        fl = FL.evaluate(ss.state, quote, CAT, P, out, mode)
        prev_asked = bool(ss.history and "?" in (ss.history[-1]["bot"] or ""))
        det = FL.detect(bot, ss.state, quote, P, out, prev_asked)

        ss.history.append({
            "turn": turn, "user": prompt, "bot": bot, "fixed": fixed, "kind": kind,
            "img_refs": [i["ref"] for i in new_imgs], "out": out, "raw": raw, "error": err,
            "diff": diff, "flags": fl, "detect": det, "usage": usage, "model": model,
            "at": now(), "latency_ms": latency_ms, "addr_api": dict(ss.state.addr_api or {}),
        })
        ss.pending = None
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
            st.dataframe(pd.DataFrame(quote["rows"]), width="stretch", hide_index=True)
            st.markdown("소계 **%s원** + 배송비 **%s원** = 합계 **%s**" % (
                f"{quote['subtotal']:,}", f"{quote['shipping']:,}",
                f"{quote['total']:,}원" if quote["total"] is not None else "확정 차단"))
        else:
            st.caption("담긴 품목 없음")

        # 대화는 자연스럽게 끝나므로 LLM 은 종료를 알지 못한다. 테스터가 직접 끊는다.
        st.divider()
        if not ss.ended:
            if st.button("🧾 상담 완료 — 주문서 확정", type="primary", width="stretch",
                         disabled=not ss.history):
                # 우편번호 검증은 대화 중에 하지 않는다. 확정 시점에 한 번만 조회하고
                # 실패하면 주문서에 플래그로 남긴다.
                if ss.state.address_base:
                    ss.state.addr_api = juso.search(ss.state.address_base.value, JUSO_KEY)
                    ss.state.zipno = ss.state.addr_api.get("zipno")
                    ss.state.road_addr = ss.state.addr_api.get("road_addr")
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
                    st.dataframe(pd.DataFrame(h["detect"]), width="stretch",
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
                st.dataframe(pd.DataFrame(quote["rows"]), width="stretch",
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

        if ss.saved:
            st.success("판정을 저장했습니다 — %s" % ss.saved)
            for kind_, msg in ss.log_msgs:
                (st.caption if kind_ == "ok" else st.warning)(msg)
            if st.button("새 대화 시작", type="primary"):
                ss.saved = None
                reset_conversation()
                st.rerun()

        elif st.button("판정 저장", type="primary"):
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
                "latency_avg": int(sum(h.get("latency_ms") or 0 for h in ss.history)
                                   / max(1, len(ss.history))),
                "latency_max": max([h.get("latency_ms") or 0 for h in ss.history] or [0]),
                "verdicts": verdicts, "sources": sources, "note": note,
            }
            ss.records.append(rec)

            # 시트에도 남긴다. 실패해도 앱을 멈추지 않고 세션 기록은 그대로 유지된다.
            # 탭이 여섯 개라 수십 초 걸릴 수 있으므로 진행 상황을 그 자리에 보여준다.
            msgs = []
            if LOG.configured():
                try:
                    bundle = LOG.build_rows(
                        conv_id, tester, mode_label, model, state, quote, ss.history,
                        verdicts, sources, note,
                        policy_version=sheets.secret("POLICY_VERSION", "sheet-live"),
                        started_at=ss.started_at, ended_at=now(),
                        flag_settings={k: r.get("값") for k, r in P.flags.items()})
                    with st.status("시트에 기록하는 중…", expanded=True) as status:
                        for tab, rows in bundle.items():
                            st.write("%s 기록 중…" % tab)
                            ok, msg = LOG.write(tab, rows)
                            msgs.append(("ok" if ok else "err", "%s — %s" % (tab, msg)))
                        LOG.clear_cache()
                        status.update(label="기록 완료", state="complete", expanded=False)
                except Exception as e:
                    # 시트 기록이 실패해도 세션 기록은 남는다. 조용히 넘어가지 않는다.
                    msgs.append(("err", "로그 기록 실패 — %s: %s" % (type(e).__name__, e)))
            else:
                msgs.append(("err", "로그 미설정 — 이 세션 안에서만 집계됩니다"))
            ss.log_msgs = msgs

            ss.conv_no += 1
            ss.saved = conv_id

            # 여기서 다시 그리면 화면이 첫 탭으로 튀어 저장됐는지 알 수 없다.
            # 결과를 이 자리에 그대로 보여준다.
            st.success("판정을 저장했습니다 — %s" % conv_id)
            for kind_, msg in msgs:
                (st.caption if kind_ == "ok" else st.warning)(msg)
            st.info("이어서 새 대화를 하시려면 위의 **새 대화 시작** 버튼을 눌러주세요. "
                    "(이 화면을 벗어났다 돌아오면 보입니다)")


# ================================================================== 보고서
def _num(v):
    """시트에서 읽은 값은 모두 문자열이다. 숫자로 못 바꾸면 0으로 본다."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


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
            "tokens_in": num(c.get("tokens_in")), "tokens_out": num(c.get("tokens_out")),
            "latency_avg": num(c.get("latency_avg_ms")),
            "latency_max": num(c.get("latency_max_ms")),
            "verdicts": verdicts, "sources": sources, "note": c.get("note", ""),
        })
    return out, None


with tab_report:
    for kind, msg in ss.log_msgs:
        (st.success if kind == "ok" else st.warning)(msg)

    if LOG.configured():
        recs, log_err = records_from_sheet()
        if log_err or (not recs and ss.records):
            if log_err:
                st.warning("로그 시트를 읽지 못해 이 세션 기록만 보여줍니다 — %s" % log_err)
            else:
                st.warning("시트에 기록은 됐지만 아직 읽히지 않습니다. 이 세션 기록으로 보여줍니다.")
            recs = ss.records
        else:
            st.caption("로그 시트에서 읽었습니다. 두 테스터의 기록이 함께 집계됩니다.")
    else:
        recs = ss.records
        st.warning("Apps Script 로그가 설정되지 않아 **이 브라우저 세션 안에서만** 집계됩니다. "
                   "새로고침하면 사라지니 아래 CSV 다운로드로 받아두세요.")

    if not recs:
        st.info("아직 판정된 대화가 없습니다. **💬 대화** 탭에서 대화를 진행하고 "
                "**상담 완료 → 판정 저장** 을 하면 여기에 집계됩니다.")
    else:
        tin = sum(_num(r["tokens_in"]) for r in recs)
        tout = sum(_num(r["tokens_out"]) for r in recs)
        cost = sum(LLM.cost_usd(r["model"], _num(r["tokens_in"]), _num(r["tokens_out"]))
                   for r in recs)

        c = st.columns(5)
        c[0].metric("테스트한 대화", "%d건" % len(recs))
        c[1].metric("LLM 호출", "%d회" % sum(_num(r["turns"]) for r in recs))
        c[2].metric("업로드 이미지", "%d장" % sum(_num(r["images"]) for r in recs))
        c[3].metric("토큰 (추정)", f"{tin + tout:,}",
                    "입력 %s / 출력 %s" % (f"{tin:,}", f"{tout:,}"))
        c[4].metric("예상 비용", "$%.3f" % cost)

        lat = [_num(r.get("latency_avg")) for r in recs if _num(r.get("latency_avg"))]
        if lat:
            mx = max(_num(r.get("latency_max")) for r in recs)
            st.caption("응답 시간 — 평균 %.1f초 · 최대 %.1f초 (자체 구축 시 체감 속도의 실측값)"
                       % (sum(lat) / len(lat) / 1000, mx / 1000))

        st.divider()
        st.markdown("### 항목별 성공률")
        rows = []
        for key, label in VERDICT_FIELDS:
            ok = sum(1 for r in recs if r["verdicts"][key]["verdict"] == "통과")
            rows.append({"항목": label, "통과": ok, "실패": len(recs) - ok,
                         "성공률": "%.0f%%" % (100 * ok / len(recs))})
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

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
            st.dataframe(pd.DataFrame(cross), width="stretch", hide_index=True)
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
                    width="stretch", hide_index=True)
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
                st.dataframe(pd.DataFrame(mrows), width="stretch", hide_index=True)
            else:
                st.caption("데이터 없음")

        st.divider()
        st.markdown("### 남긴 메모")
        for r in recs:
            if r["note"]:
                # 세션 기록은 번호, 시트 기록은 대화 ID 라 형식을 숫자로 고정하면 안 된다
                st.markdown("- **%s · %s (%s)** — %s" %
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
        st.dataframe(P.summary(), width="stretch", hide_index=True)

    with st.expander("유사어 충돌 — AMBIGUOUS_ALIAS 가 떠야 할 지점", expanded=False):
        syn, master = data["synonyms"], data["master_products"]
        name_of = dict(zip(master["item_code"],
                           master.get("canonical_name", master["item_code"])))
        grouped = syn.groupby("synonym")["item_code"].apply(lambda s: sorted(set(s)))
        collide = grouped[grouped.apply(len) > 1]
        if len(collide):
            st.dataframe(pd.DataFrame([
                {"표현": a, "걸리는 상품": " ↔ ".join("%s %s" % (c, name_of.get(c, "")) for c in cs)}
                for a, cs in collide.items()]), width="stretch", hide_index=True)
        else:
            st.write("없음")
