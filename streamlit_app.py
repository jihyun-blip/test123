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
from lib import handoff as HO
from lib import images as IMG
from lib import juso
from lib import logs as LOG
from lib import llm as LLM
from lib import matching as M
from lib import messages as MSG
from lib import policies as pol
from lib import reply as RP
from lib import sheets
from lib.order import OrderState

# 배포 반영 여부를 화면에서 바로 확인하기 위한 표시
APP_VERSION = "2026-08-10.3"
KRW = 1400  # 비용을 체감 가능한 단위로 바꾸기 위한 환산 환율

# 태국 직원이 이 도구를 직접 쓴다. 이름 대신 A·B·C 로 구분한다.
TESTERS = ["A", "B", "C"]

# 세 축. 배송지·통화는 항상 한국이라 나라는 지금 하나뿐이다.
COUNTRY = "KR"
LANGS = MSG.LANGS          # 기본이 th. 화면과 고객 응답이 함께 이 값을 따른다
CHANNELS = ["facebook", "platform"]

# 화면 언어는 언어 선택 위젯이 정하는데, 그 위젯의 라벨도 번역해야 한다.
# 위젯을 그리기 전에 지난 선택을 먼저 읽어 문구표를 잡는다.
T = MSG.for_lang(st.session_state.get("lang_sel", MSG.DEFAULT_LANG))

st.set_page_config(page_title=T.t("ui_title"), page_icon="🧪", layout="wide")

# 테스터가 대화 끝에 통과·실패를 찍는 항목. 이게 이 도구의 핵심 산출물이다.
# 왼쪽 키와 아래 원인 코드는 로그에 그대로 쌓이므로 번역하지 않는다.
# 화면에 보이는 이름만 언어를 탄다. 안 그러면 언어마다 다른 값이 쌓여 집계가 갈린다.
VERDICT_KEYS = [("invoice", "vf_invoice"), ("address", "vf_address"),
                ("receiver", "vf_receiver"), ("phone", "vf_phone")]
CAUSE_TAGS = [("추출오류", "cause_extract"), ("매칭오류", "cause_match"),
              ("단위오해", "cause_unit"), ("DB에없음", "cause_nodb"),
              ("지침부족", "cause_policy"), ("언어품질", "cause_lang"),
              ("기타", "cause_etc")]
MODES = [("전체", "ui_mode_full"), ("축소", "ui_mode_reduced")]


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
head = st.columns([1.4, 1.4, 1.8, 0.9, 1.2, 1, 1])
tester = head[0].selectbox(T.t("ui_tester"), TESTERS)
mode_label = head[1].radio(T.t("ui_mode"), [c for c, _ in MODES], horizontal=True,
                           format_func=lambda c: T.t(dict(MODES)[c]),
                           help=T.t("ui_mode_help"))
mode = "full" if mode_label == "전체" else "reduced"
MODEL_LIST = sheets.secret("MODELS", ["(목 모드)"])
model = head[2].selectbox(T.t("ui_model"), MODEL_LIST)
# 언어는 이름·유사어·단위의 축, 채널은 판매가·배송비의 축이다. 둘은 서로 다른 축이라
# 함께 들고 있어야 한다. 상품 이름은 채널이 달라도 같다.
# 화면 문구도 이 값을 따른다. 태국 직원이 태국어 화면에서 태국어 응답을 보고 판정한다.
lang = head[3].selectbox(T.t("ui_lang"), LANGS, key="lang_sel",
                         help=T.t("ui_lang_help"))
channel = head[4].selectbox(T.t("ui_channel"), CHANNELS, help=T.t("ui_channel_help"))
T = MSG.for_lang(lang)

def overload_backup(current):
    """과부하 때 대신 부를 모델.

    설정된 목록에서 현재 모델보다 싼 것 중 가장 비싼 것을 고른다. 한 단계만
    내려가야 판독 능력을 덜 잃는다. 대체가 더 비싼 모델을 부르는 일은 없어야
    하므로, 이름이 아니라 단가로 판단한다."""
    def price(m):
        pin, pout = LLM.PRICING.get(m, LLM.DEFAULT_PRICE)
        return pin + pout

    cheaper = [m for m in MODEL_LIST if m != current and price(m) < price(current)]
    return max(cheaper, key=price) if cheaper else None
if head[5].button(T.t("ui_refresh"), width="stretch"):
    sheets.clear_cache()
    # 행 수가 그대로인 수정(가격 한 칸 변경 등)도 반영되게 카탈로그까지 버린다
    st.cache_resource.clear()
    st.rerun()
if head[6].button(T.t("ui_reset"), width="stretch"):
    reset_conversation()
    st.rerun()

data, origins, errors, warns = sheets.load_all()
for name, err in warns.items():
    # 선택 소스는 없어도 폴백으로 돈다. 다만 조용히 넘어가지는 않는다
    st.warning(T.t("dt_sheet_warn", name, err))
if errors:
    for name, err in errors.items():
        st.error(T.t("dt_sheet_fail", name) + "\n\n```\n%s\n```" % err)
        # 401/403 은 앱이나 버튼 문제가 아니라 시트 공유 설정이 풀린 것이다.
        # 안내가 없으면 테스터가 자기가 무언가를 망가뜨렸다고 생각한다.
        if "401" in str(err) or "403" in str(err):
            st.warning(T.t("dt_share_fix"))
    st.stop()

@st.cache_resource(show_spinner=False)
def build_catalog(_data, lang, country_code, channel, fingerprint):
    """카탈로그는 축(언어·나라·채널)마다 하나다. 화면이 다시 그려질 때마다
    10만 행을 다시 펼치면 조작할 때마다 그만큼 기다리게 된다.
    fingerprint 는 시트가 바뀌었는지 보는 값이고, 앞의 _data 는 해시하지 않는다."""
    return M.Catalog(_data["master_products"], _data["prices"], _data["product_names"],
                     _data["synonyms"], lang=lang, country_code=country_code,
                     channel=channel, shipping=_data["shipping"], units=_data["units"])


P = pol.Policies(data["bot_policies"], lang=lang)
CAT = build_catalog(data, lang, COUNTRY, channel,
                    tuple(len(data[k]) for k in sorted(data)))
MAX_OPTIONS = P.get_int("AMBIGUOUS_MAX_OPTIONS", 5) or 5

API_KEY = sheets.secret("GEMINI_API_KEY")
JUSO_KEY = sheets.secret("JUSO_CONFM_KEY")

st.caption(T.t("ui_build", APP_VERSION))

# 화면 표시용 이름은 언어를 타고, 저장되는 키는 그대로 둔다
VERDICT_FIELDS = [(k, T.t(label_key)) for k, label_key in VERDICT_KEYS]

tab_report, tab_chat, tab_verdict, tab_data = st.tabs(
    [T.t("tab_report"), T.t("tab_chat"), T.t("tab_verdict"), T.t("tab_data")])


# ================================================================== 대화
with tab_chat:
    if ss.ended:
        st.success(T.t("chat_ended"))

    left, right = st.columns([3, 2])

    with left:
        for h in ss.history:
            with st.chat_message("user"):
                st.write(h["user"] or T.t("chat_image_only"))
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
                    u = h.get("usage") or {}
                    if u.get("fallback_model"):
                        st.caption(T.t("chat_fallback", u["fallback_from"],
                                       u["fallback_model"]))
                    retries = u.get("retries")
                    st.caption(T.t("chat_latency", h["latency_ms"] / 1000,
                                   T.t("chat_retried", retries) if retries else ""))
                if h.get("error"):
                    st.error(T.t("chat_mock", h["error"]))
                    if h.get("raw"):
                        with st.expander(T.t("chat_raw")):
                            st.code(h["raw"][:3000])

        # 처리 전이라도 고객 발화는 즉시 보여준다. 전송됐는지 몰라 다시 누르는 일을 막는다.
        if ss.pending:
            with st.chat_message("user"):
                st.write(ss.pending["user"] or T.t("chat_image_only"))
                if ss.pending["imgs"]:
                    st.caption(T.t("chat_attached")
                               + ", ".join(i["ref"] for i in ss.pending["imgs"]))
            with st.chat_message("assistant"):
                st.caption(T.t("chat_thinking"))

        # 첨부를 입력창 안에 둔다. 따로 있으면 텍스트가 비었을 때 전송이 안 돼서,
        # 사진만 보내는 고객(실제로 흔하다)을 재현할 수 없다.
        up, prompt = None, None
        if not ss.ended and not ss.pending:
            sub = st.chat_input(T.t("chat_input"), accept_file="multiple",
                                file_type=["png", "jpg", "jpeg", "webp"])
            if sub:
                prompt = (sub.text or "").strip()
                up = sub.files

    if prompt or up:
        new_imgs = []
        for f in up or []:
            ref = "img_%d" % (len(ss.images) + len(new_imgs) + 1)
            # 바깥의 data(시트 묶음)를 가리지 않도록 이름을 따로 쓴다
            img_bytes, mime, note = IMG.prepare(f.getvalue(), f.type or "image/jpeg")
            new_imgs.append({"ref": ref, "name": f.name, "bytes": img_bytes,
                             "mime": mime, "resize": note})
        ss.pending = {"user": prompt, "imgs": new_imgs}
        st.rerun()

    if ss.pending:
        prompt = ss.pending["user"]
        new_imgs = ss.pending["imgs"]
        turn = len(ss.history) + 1
        ss.images.extend(new_imgs)

        in_order = [l.match.code for l in ss.state.lines
                    if l.match and l.match.code and not l.unavailable]
        cand = LLM.candidates_for(prompt, CAT, mode, always=in_order)
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
                              history=ss.history, pending=pend_before, upsell=upsell,
                              options_limit=MAX_OPTIONS, image_count=len(new_imgs))

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
                # 과부하는 Pro·프리뷰에서 특히 잦다. 목 모드로 떨어뜨리면 이해 자체가
                # 사라지므로, 같은 발화를 Flash 로 한 번 더 보낸다. 어느 모델이
                # 실제로 답했는지는 아래 fallback_model 로 남겨 비교를 오염시키지 않는다.
                backup = overload_backup(model)
                if backup and any(k in str(e).lower() for k in LLM.RETRYABLE):
                    try:
                        out, raw, usage = LLM.call(API_KEY, backup, system, user, new_imgs)
                        usage["fallback_model"] = backup
                        usage["fallback_from"] = model
                        err = None
                    except Exception:
                        pass
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

        label_read = None
        if new_imgs and not any(_points_to_product(o) for o in (out.get("item_ops") or [])):
            items, label_err = LLM.read_labels(API_KEY, model, new_imgs)
            # 이 경로가 돌았는지, 무엇을 읽었는지, 왜 실패했는지를 관찰 패널에 남긴다.
            # 조용히 빈손으로 끝나면 사진을 보내도 품목이 안 잡히는 이유를 알 수 없다
            label_read = {"읽은 것": items, "실패": label_err}
            extra = []
            for it in items:
                code = LLM._code_of(it.get("label_code") or it.get("printed_name"), CAT)
                if code:
                    extra.append({"op": "add", "name_hint": CAT.display(code),
                                  "label_code": code, "quantity": None,
                                  "source": "image", "source_ref": it.get("ref")})
            label_read["살린 품목"] = [e["label_code"] for e in extra]
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

        # 직전에 "각각 몇 개씩" 이라고 물었으면 숫자 하나만 와도 각 품목에 적용한다
        each_hint = bool(ss.history
                         and T.t("qty_each_marker") in (ss.history[-1]["bot"] or ""))
        diff = ss.state.apply(out, turn, CAT, P, each_hint, prompt)
        ss.state.rematch(CAT, P, mode)
        # 지난 턴에 물었는데 이번에도 못 받은 항목을 센다. 무한 되물음을 끊는 근거다.
        # 고객이 되묻거나 딴 얘기를 한 턴은 답할 기회가 없었던 것이다. 세지 않는다.
        # 그렇지 않으면 두어 번 대화가 오가기만 해도 묻기를 포기해버린다.
        if pend_before and not RP.asked_question(prompt):
            ss.state.count_unanswered(pend_before.get("keys") or [])

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

        # DB 에 없는 상품은 되묻기를 멈추고 없다고 알린 뒤, 나머지 주문을 계속한다.
        gone = ss.state.take_unavailable_notice()
        if gone:
            notes = []
            miss = [k for k, why in gone if why not in ("rejected", "soldout")]
            sold = [k for k, why in gone if why == "soldout"]
            drop = [k for k, why in gone if why == "rejected"]
            def _names(keys):
                return ", ".join(T.quote_word(RP.spoken(k, P)) for k in keys)

            if miss:
                notes.append(T.t("drop_not_found", T.eun(_names(miss))))
            if sold:
                notes.append(T.t("drop_soldout", T.eun(_names(sold))))
            if drop:
                notes.append(T.t("drop_rejected", T.eul(_names(drop))))
            notes.append(T.t("drop_rest"))
            fixed = (" ".join(notes) + "\n" + fixed).strip()
        tail = (out.get("reply") or "").strip() if err is None else ""

        # 고객이 주소나 연락처를 먼저 준 경우, 받았다는 말 없이 다음 질문만 하면
        # 보냈는지 아닌지를 알 수 없어 같은 것을 또 보내게 된다.
        # 이번 턴에 새로 채워진 것만 짚는다. 이미 말한 것을 매 턴 반복하지 않기 위해서다.
        got = [T.t(key) for key, f in (("got_receiver", ss.state.receiver),
                                       ("got_phone", ss.state.phone),
                                       ("got_address", ss.state.address_base))
               if f and f.turn == turn]

        # 고객이 흐름에서 벗어난 말을 했으면 그 답은 살린다.
        # 지침의 SMALLTALK 이 짧은 잡담을 허용하고, SMALLTALK_RETURN 이 복귀를 요구한다.
        # intent 만 보면 답과 질문이 섞인 발화에서 질문을 놓친다. 고객이 친 문장도 같이 본다.
        # 고객이 글을 한 줄도 안 썼으면 "딴 얘기" 라는 것이 성립하지 않는다.
        # 사진만 온 턴을 모델이 smalltalk 으로 분류하면 그 인사가 예외로 살아남아,
        # 코드가 만든 "각각 몇 개씩 필요하신가요?" 앞에 "어떤 상품으로 준비해
        # 드릴까요?" 가 서서 서로 모순된 문장이 나간다.
        digression = bool(str(prompt or "").strip()) and (
            out.get("intent") in ("smalltalk", "question", "complaint")
            or RP.asked_question(prompt))

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
            fb = RP.fallback_ask(pend_after, P)
            if fb:
                fixed = "\n\n".join(x for x in (fixed, fb) if x)

        # 확인 인사는 tail 이 최종 확정된 뒤에 판단한다. 먼저 보면, 곧 지워질 tail 때문에
        # 인사를 걸렀다가 결국 받았다는 말을 아무도 하지 않는 턴이 된다.
        if got and not tail:
            fixed = (T.t("got_fields", T.eul(", ".join(got))) + "\n" + fixed).strip()

        # 코드가 붙이는 물음이 "무엇을 찾으시냐", "몇 개 필요하냐" 처럼 일반적인 단계에서는,
        # LLM 이 이미 답하며 물었다면 같은 질문이 두 번 나간다. 그때는 코드 문장을 뺀다.
        # 후보 목록을 내미는 단계는 제외한다. 거기 실린 상품명과 가격은 LLM 이 대신 쓸 수 없다.
        # 다만 이번 턴에 사진이 왔으면 코드 문장을 남긴다. 사진에서 상품을 못 읽었을 때
        # LLM 의 일반적인 인사("어떤 상품으로 준비해 드릴까요?")가 코드가 만든
        # "보내주신 사진에서 상품을 확인하지 못했어요" 를 덮어버리면,
        # 고객은 사진이 읽혔는지조차 모른 채 같은 사진을 다시 보낸다.
        if kind in ("order_ask", "quantity_ask") and tail and "?" in tail and not new_imgs:
            fixed = ""

        # 고객이 흐름에서 벗어난 질문을 했다면 그 답이 먼저 오고,
        # 흐름을 되돌리는 코드 문장이 뒤에 붙어야 자연스럽다.
        order = (tail, fixed) if (digression and tail) else (fixed, tail)
        bot = "\n\n".join(x for x in order if x) or T.t("no_reply")

        # 인사는 봇의 첫 발화 맨 앞에. 무엇을 말하든 그 위에 온다.
        if not ss.history:
            bot = T.t("greeting") + "\n" + bot

        asking = kind in RP.ASK_STAGES or bool(pend_after["missing"] or pend_after["detail"])
        fl = FL.evaluate(ss.state, quote, CAT, P, out, mode, bot_text=bot, asking=asking)
        # 되물었는지는 물음표가 아니라 단계로 본다. 태국어는 물음표 없이 묻는다.
        prev_asked = bool(ss.history and ss.history[-1].get("asking"))
        det = FL.detect(bot, ss.state, quote, P, out, prev_asked, catalog=CAT, asking=asking)

        ss.history.append({
            "turn": turn, "user": prompt, "bot": bot, "fixed": fixed, "kind": kind,
            "asking": asking, "lang": lang, "channel": channel,
            "label_read": label_read,
            "img_resize": [i["resize"] for i in new_imgs if i.get("resize")],
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
        fl = FL.evaluate(state, quote, CAT, P,
                         ss.history[-1]["out"] if ss.history else {}, mode,
                         bot_text=ss.history[-1]["bot"] if ss.history else "",
                         asking=bool(ss.history and ss.history[-1].get("asking")))

        st.markdown(T.t("panel_order"))

        if fl:
            for f in fl:
                # 값은 코드가 비교하는 제어값이라 그대로 두고, 화면 표기만 옮긴다
                icon = {"차단": "🛑", "상담원연결": "🙋", "되물음": "❓",
                        "검수필수": "🔍"}.get(f.value, "⚠️")
                st.markdown("%s **%s** · `%s`  \n<small>%s</small>" %
                            (icon, f.key, T.action(f.value), f.evidence),
                            unsafe_allow_html=True)
        else:
            st.caption(T.t("panel_no_flags"))

        st.divider()
        for key, f in (("panel_receiver", state.receiver), ("panel_phone", state.phone)):
            st.markdown("**%s** %s <small>%s</small>" %
                        (T.t(key), f.value or "—", f.origin), unsafe_allow_html=True)

        st.markdown(T.t("panel_address"))
        st.markdown(T.t("panel_addr_read", state.address_base.value or "—",
                        state.address_base.origin), unsafe_allow_html=True)
        st.markdown(T.t("panel_addr_detail", state.address_detail.value or "—"))
        if state.road_addr:
            st.markdown(T.t("panel_addr_api", state.road_addr, state.zipno))
        elif state.addr_api.get("done"):
            st.markdown(T.t("panel_addr_none"))

        st.divider()
        if quote["rows"]:
            # 계산에 쓰는 열 이름은 그대로 두고 화면에 보여줄 때만 언어를 입힌다
            st.dataframe(pd.DataFrame(MSG.display_quote(quote["rows"], lang)),
                         width="stretch", hide_index=True)
            st.markdown(T.t("panel_sum", f"{quote['subtotal']:,}", f"{quote['shipping']:,}",
                            T.money(quote["total"]) if quote["total"] is not None
                            else T.t("panel_blocked")))
        else:
            st.caption(T.t("panel_no_items"))

        # 대화는 자연스럽게 끝나므로 LLM 은 종료를 알지 못한다. 테스터가 직접 끊는다.
        st.divider()
        if not ss.ended:
            if st.button(T.t("panel_finish"), type="primary", width="stretch",
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
            st.info(T.t("panel_ended"))

    # ---------------------------------------------------------- 관찰 패널
    if ss.history:
        h = ss.history[-1]
        with st.expander(T.t("panel_observe"), expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                st.caption(T.t("panel_diff"))
                st.write(h["diff"] or T.t("panel_no_diff"))
                st.caption(T.t("panel_detect"))
                if h["detect"]:
                    # code 는 로그용이라 화면에서는 뺀다
                    st.dataframe(
                        pd.DataFrame([{T.t("dt_col_hit"): d["감지"],
                                       T.t("dt_col_rule"): d["근거 규칙"],
                                       T.t("dt_col_body"): d["내용"]}
                                      for d in h["detect"]]),
                        width="stretch", hide_index=True)
                else:
                    st.write(T.t("panel_none"))
                st.caption(T.t("panel_gaps"))
                st.write(h["out"].get("missing_info") or T.t("panel_none"))
            with c2:
                st.caption(T.t("panel_llm_raw"))
                st.json(h["out"], expanded=False)
                st.caption(T.t("panel_used_refs"))
                st.write(h["out"].get("used_refs") or T.t("panel_none"))
                st.caption(T.t("panel_addr_api_cap"))
                st.write(ss.state.addr_api or T.t("panel_addr_api_none"))
                st.caption(T.t("panel_images"))
                st.write(ss.state.images or T.t("panel_no_images"))
                if h.get("label_read"):
                    # 1차에서 품목을 못 건져 라벨만 다시 읽은 턴
                    st.caption(T.t("panel_label_read"))
                    st.write(h["label_read"])
                if ss.state.phone_second:
                    st.caption(T.t("panel_phone2", ss.state.phone_second))
            u = h["usage"]
            st.caption(T.t("panel_tokens", u.get("input"), u.get("output"),
                           T.t("panel_estimated") if u.get("estimated") else "",
                           h["model"], T.t(dict(MODES)[mode_label])))


# ================================================================== 판정
with tab_verdict:
    if not ss.ended:
        st.info(T.t("vd_need_finish"))
    else:
        state = ss.state
        quote = state.quote(CAT, P)
        # 저장 시점이 아니라 대화가 시작될 때 이미 정해지는 값이다.
        # 테스터가 시트에 옮겨 적어야 하므로 저장 전에도 보여준다.
        conv_id = "%s-%s-%03d" % (tester, ss.started_at.replace(" ", "_").replace(":", ""),
                                  ss.conv_no)
        st.markdown(T.t("vd_title"))

        c1, c2 = st.columns([2, 1])
        with c1:
            if quote["rows"]:
                st.dataframe(pd.DataFrame(MSG.display_quote(quote["rows"], lang)),
                             width="stretch", hide_index=True)
            else:
                st.caption(T.t("vd_no_items"))
        with c2:
            st.write({
                T.t("vd_receiver"): state.receiver.value or "—",
                T.t("vd_phone"): state.phone.value or "—",
                T.t("vd_address"): state.address_base.value or "—",
                T.t("vd_address_detail"): state.address_detail.value or "—",
                T.t("vd_zip"): state.zipno or "—",
                T.t("vd_total"): (T.money(quote["total"]) if quote["total"] is not None
                                  else T.t("panel_blocked")),
            })

        hand = HO.build(state, quote, CAT, P, ss.history)
        st.markdown(T.t("vd_handoff"))
        if hand:
            st.caption(T.t("vd_handoff_help"))
            groups = {}
            for kind_, text in hand:
                groups.setdefault(kind_, []).append(text)
            for kind_, texts in groups.items():
                st.markdown("**%s**" % kind_)
                for t in texts:
                    st.markdown("- %s" % t)
        else:
            st.success(T.t("vd_handoff_none"))

        st.divider()
        st.markdown(T.t("vd_fields"))
        st.caption(T.t("vd_fields_help"))

        # 판정 값과 원인 코드는 로그에 그대로 쌓인다. 화면에만 언어를 입힌다
        verdicts = {}
        for key, label in VERDICT_FIELDS:
            col = st.columns([2, 1.4, 2])
            col[0].markdown("**%s**" % label)
            v = col[1].radio(label, ["통과", "실패"], horizontal=True,
                             format_func=lambda x: T.t("vd_pass" if x == "통과" else "vd_fail"),
                             key="v_%s" % key, label_visibility="collapsed")
            cause = col[2].selectbox(T.t("vd_cause"), [c for c, _ in CAUSE_TAGS],
                                     format_func=lambda c: T.t(dict(CAUSE_TAGS)[c]),
                                     key="c_%s" % key, label_visibility="collapsed",
                                     disabled=(v == "통과"))
            verdicts[key] = {"verdict": v, "cause": None if v == "통과" else cause}

        # ---------------------------------------------------------- 플래그 판정
        # 시트에는 정탐·오탐을 찍는 칸이 있는데 화면에 없었다. 그래서 빈 채로 저장되고,
        # 나중에 시트를 열어 키와 근거만 보고 판단해야 했다. 그때는 대화가 눈앞에 없다.
        # 테스트 시트와 로그를 잇는 열쇠. 없으면 실패율은 보이는데 그 이유를 못 찾는다.
        st.divider()
        st.markdown(T.t("vd_convid"))
        st.caption(T.t("vd_convid_help"))
        st.code(conv_id, language=None)

        st.divider()
        st.markdown(T.t("vd_flags"))
        st.caption(T.t("vd_flags_help"))

        raised = {}
        for h in ss.history:
            for f in h.get("flags") or []:
                raised.setdefault(f.key, (f.value, f.evidence, h["turn"]))

        FLAG_VERDICTS = [("정탐", "fv_true"), ("오탐", "fv_false"), ("판단보류", "fv_hold")]
        flag_verdicts = {}
        if raised:
            for key, (value, evidence, at) in raised.items():
                icon = {"차단": "🛑", "상담원연결": "🙋", "되물음": "❓",
                        "검수필수": "🔍"}.get(value, "⚠️")
                st.markdown("%s **%s** · `%s` · %s  \n<small>%s</small>"
                            % (icon, key, T.action(value), T.t("vd_flag_turn", at),
                               evidence), unsafe_allow_html=True)
                v = st.radio(key, [c for c, _ in FLAG_VERDICTS], horizontal=True,
                             index=2, format_func=lambda c: T.t(dict(FLAG_VERDICTS)[c]),
                             key="fv_%s" % key, label_visibility="collapsed")
                if v != "판단보류":
                    flag_verdicts[key] = v
        else:
            st.caption(T.t("vd_no_flags"))

        # 오탐보다 미탐이 찾기 어렵다. 대화를 본 사람만 지목할 수 있다.
        missed = st.multiselect(
            T.t("vd_missed"), sorted(k for k in P.flags if k not in raised),
            help=T.t("vd_missed_help"), placeholder=T.t("vd_missed_hint"),
            format_func=lambda k: "%s — %s" % (k, (P.flags[k].get("설명") or "")[:40]))

        st.divider()
        note = st.text_area(T.t("vd_note"), placeholder=T.t("vd_note_hint"))

        if ss.saved:
            st.success(T.t("vd_saved", ss.saved))
            for kind_, msg in ss.log_msgs:
                (st.caption if kind_ == "ok" else st.warning)(msg)
            if st.button(T.t("vd_new"), type="primary"):
                ss.saved = None
                reset_conversation()
                st.rerun()

        elif st.button(T.t("vd_save"), type="primary"):
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

            rec = {
                "conversation_id": conv_id, "conv_no": ss.conv_no, "tester": tester,
                "mode": mode_label, "model": model, "lang": lang, "channel": channel,
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
                        verdicts, sources, note, handoff=HO.as_text(hand),
                        policy_version=sheets.secret("POLICY_VERSION", "sheet-live"),
                        started_at=ss.started_at, ended_at=now(),
                        flag_settings={k: r.get("값") for k, r in P.flags.items()},
                        lang=lang, channel=channel, app_version=APP_VERSION,
                        flag_verdicts=flag_verdicts, missed_flags=missed)
                    with st.status(T.t("vd_writing"), expanded=True) as status:
                        for tab, rows in bundle.items():
                            st.write(T.t("vd_writing_tab", tab))
                            ok, msg = LOG.write(tab, rows)
                            msgs.append(("ok" if ok else "err", "%s — %s" % (tab, msg)))
                        LOG.clear_cache()
                        status.update(label=T.t("vd_written"), state="complete",
                                      expanded=False)
                except Exception as e:
                    # 시트 기록이 실패해도 세션 기록은 남는다. 조용히 넘어가지 않는다.
                    msgs.append(("err", T.t("vd_log_fail", type(e).__name__, e)))
            else:
                msgs.append(("err", T.t("vd_log_off")))
            ss.log_msgs = msgs

            ss.conv_no += 1
            ss.saved = conv_id

            # 여기서 다시 그리면 화면이 첫 탭으로 튀어 저장됐는지 알 수 없다.
            # 결과를 이 자리에 그대로 보여준다.
            st.success(T.t("vd_saved", conv_id))
            for kind_, msg in msgs:
                (st.caption if kind_ == "ok" else st.warning)(msg)
            st.info(T.t("vd_after_save"))


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

    # 저장이 시간 초과로 실패한 것처럼 보이면 테스터가 다시 누른다. 그때 Apps Script 쪽에는
    # 이미 기록돼 있어 같은 대화가 여러 줄 쌓인다. 그대로 세면 대화 수·토큰·비용이 전부
    # 부풀려지므로, 읽을 때 대화 하나당 마지막 행만 남긴다.
    convs = convs.drop_duplicates(subset=["conversation_id"], keep="last")

    by_conv = {}
    if not fvs.empty:
        fvs = fvs.drop_duplicates(subset=["conversation_id", "field_type"], keep="last")
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
                st.warning(T.t("rp_sheet_fail", log_err))
            else:
                st.warning(T.t("rp_sheet_lag"))
            recs = ss.records
        else:
            st.caption(T.t("rp_from_sheet"))
    else:
        recs = ss.records
        st.warning(T.t("rp_no_log"))

    if not recs:
        st.info(T.t("rp_empty"))
    else:
        tin = sum(_num(r["tokens_in"]) for r in recs)
        tout = sum(_num(r["tokens_out"]) for r in recs)
        cost = sum(LLM.cost_usd(r["model"], _num(r["tokens_in"]), _num(r["tokens_out"]))
                   for r in recs)

        c = st.columns(5)
        c[0].metric(T.t("rp_conversations"), "%d" % len(recs))
        c[1].metric(T.t("rp_calls"), "%d" % sum(_num(r["turns"]) for r in recs))
        c[2].metric(T.t("rp_images"), "%d" % sum(_num(r["images"]) for r in recs))
        c[3].metric(T.t("rp_tokens"), f"{tin + tout:,}",
                    T.t("rp_tokens_delta", f"{tin:,}", f"{tout:,}"))
        # 총액보다 "대화 1건당 얼마"가 자체 구축 판단의 실제 근거다.
        per = cost / len(recs)
        c[4].metric(T.t("rp_cost"), T.money(int(cost * KRW)),
                    T.t("rp_cost_delta", f"{int(per * KRW):,}"))
        st.caption(T.t("rp_cost_note", f"{KRW:,}", f"{int(per * KRW * 10000):,}"))

        lat = [_num(r.get("latency_avg")) for r in recs if _num(r.get("latency_avg"))]
        if lat:
            mx = max(_num(r.get("latency_max")) for r in recs)
            st.caption(T.t("rp_latency", sum(lat) / len(lat) / 1000, mx / 1000))

        st.divider()
        st.markdown(T.t("rp_by_field"))
        rows = []
        for key, label in VERDICT_FIELDS:
            ok = sum(1 for r in recs if r["verdicts"][key]["verdict"] == "통과")
            rows.append({T.t("rp_col_field"): label, T.t("rp_col_pass"): ok,
                         T.t("rp_col_fail"): len(recs) - ok,
                         T.t("rp_col_rate"): "%.0f%%" % (100 * ok / len(recs))})
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        st.divider()
        st.markdown(T.t("rp_by_source"))
        st.caption(T.t("rp_by_source_help"))
        cross = []
        for key, label in VERDICT_FIELDS:
            for src, src_key in (("text", "rp_src_text"), ("image", "rp_src_image")):
                sub = [r for r in recs if r["sources"][key] == src]
                if not sub:
                    continue
                ok = sum(1 for r in sub if r["verdicts"][key]["verdict"] == "통과")
                cross.append({T.t("rp_col_field"): label,
                              T.t("rp_col_source"): T.t(src_key),
                              T.t("rp_col_count"): len(sub),
                              T.t("rp_col_pass"): ok, T.t("rp_col_fail"): len(sub) - ok,
                              T.t("rp_col_rate"): "%.0f%%" % (100 * ok / len(sub))})
        if cross:
            st.dataframe(pd.DataFrame(cross), width="stretch", hide_index=True)
        else:
            st.caption(T.t("rp_no_data"))

        st.divider()
        cc = st.columns(2)
        with cc[0]:
            st.markdown(T.t("rp_causes"))
            causes = {}
            for r in recs:
                for key, _ in VERDICT_FIELDS:
                    cz = r["verdicts"][key]["cause"]
                    if cz:
                        causes[cz] = causes.get(cz, 0) + 1
            if causes:
                # 저장된 값은 원인 코드다. 화면에서만 언어를 입힌다
                label_of = dict(CAUSE_TAGS)
                st.dataframe(pd.DataFrame(
                    [{T.t("rp_col_cause"): T.t(label_of[k]) if k in label_of else k,
                      T.t("rp_col_count"): v}
                     for k, v in sorted(causes.items(), key=lambda x: -x[1])]),
                    width="stretch", hide_index=True)
            else:
                st.caption(T.t("rp_no_fail"))

        with cc[1]:
            st.markdown(T.t("rp_by_mode"))
            mrows = []
            for md, md_key in MODES:
                sub = [r for r in recs if r["mode"] == md]
                if not sub:
                    continue
                total = len(sub) * len(VERDICT_FIELDS)
                ok = sum(1 for r in sub for k, _ in VERDICT_FIELDS
                         if r["verdicts"][k]["verdict"] == "통과")
                mrows.append({T.t("rp_col_mode"): T.t(md_key),
                              T.t("rp_col_conv"): len(sub),
                              T.t("rp_col_all_rate"): "%.0f%%" % (100 * ok / total)})
            if mrows:
                st.dataframe(pd.DataFrame(mrows), width="stretch", hide_index=True)
            else:
                st.caption(T.t("rp_no_data"))

        st.divider()
        st.markdown(T.t("rp_notes"))
        for r in recs:
            if r["note"]:
                # 세션 기록은 번호, 시트 기록은 대화 ID 라 형식을 숫자로 고정하면 안 된다
                st.markdown("- **%s · %s (%s)** — %s" %
                            (r["conv_no"], r["tester"], r["mode"], r["note"]))

        st.download_button(T.t("rp_download"),
                           pd.json_normalize(recs).to_csv(index=False).encode("utf-8-sig"),
                           "momo_test_results.csv", "text/csv")


# ================================================================== 데이터
with tab_data:
    st.caption(T.t("dt_caption"))

    cols = st.columns(len(data))
    for col, (name, df) in zip(cols, data.items()):
        col.metric(name, "%d" % len(df), origins[name])

    with st.expander(T.t("dt_policies"), expanded=False):
        for w in P.validate():
            st.warning(w)
        st.dataframe(P.summary(), width="stretch", hide_index=True)

    with st.expander(T.t("dt_collision"), expanded=False):
        syn, master = data["synonyms"], data["master_products"]
        # 유사어는 언어 축이다. 언어를 섞어 세면 없는 충돌이 보인다
        if "lang" in syn.columns:
            syn = syn[syn["lang"].astype(str).str.strip().str.lower() == lang]
        name_of = dict(zip(master["item_code"],
                           master.get("canonical_name", master["item_code"])))
        grouped = syn.groupby("synonym")["item_code"].apply(lambda s: sorted(set(s)))
        collide = grouped[grouped.apply(len) > 1]
        if len(collide):
            st.dataframe(pd.DataFrame([
                {T.t("dt_col_expr"): a,
                 T.t("dt_col_items"): " ↔ ".join("%s %s" % (c, name_of.get(c, "")) for c in cs)}
                for a, cs in collide.items()]), width="stretch", hide_index=True)
        else:
            st.write(T.t("dt_none"))
