# -*- coding: utf-8 -*-
"""
플래그 판정과 자동 감지.

플래그는 프롬프트가 아니라 코드가 읽는다. 값(되물음/차단/미완료/상담원연결)이
흐름을 결정하므로, 판정과 그 근거를 함께 남긴다.
결과 화면에서 "왜 떴는지"를 못 보면 정탐·오탐을 가릴 수 없다.

자동 감지는 사람 눈이 놓치는 것을 코드가 매 턴 잡는 것이다.
상당수가 지침 DB 의 기존 규칙에서 직접 도출된다.
"""
import re

from . import matching as M
from . import messages as MSG

# 지침의 REQUIRED_FIELDS 를 상태 필드로 옮기는 표.
# reply 에도 같은 표가 있지만 여기서 그 모듈을 불러오면 순환 참조가 되고,
# Streamlit 이 모듈을 다시 읽을 때 임포트가 통째로 깨진다.
_FIELD_OF = {"수령인": "receiver", "전화": "phone", "주소": "address_base"}


def missing_required_any(state, policies):
    raw = str(policies.get("REQUIRED_FIELDS", "수령인,전화,주소") or "수령인,전화,주소")
    for token in raw.split(","):
        attr = _FIELD_OF.get(token.strip())
        if attr and not getattr(state, attr, None):
            return True
    return False


class Flag:
    def __init__(self, key, value, evidence):
        self.key = key
        self.value = value          # 되물음 / 차단 / 미완료 / 상담원연결 / 검수필수
        self.evidence = evidence    # 왜 떴는지. 결과 화면에 그대로 표시된다

    def as_row(self):
        return {"플래그": self.key, "값": self.value, "근거": self.evidence}


# 코드가 실제로 올리는 플래그 키. 시트에서 행을 지우면 add() 가 조용히 넘어가
# 아무 일도 일어나지 않는다. 에러도 경고도 없다. policies.validate() 가 이 목록과
# 시트를 대조해 없는 것을 알려준다.
CODE_FLAGS = frozenset({
    "SOLDOUT", "ITEM_REJECTED", "AMBIGUOUS_ALIAS", "PRODUCT_NOT_FOUND",
    "PRODUCT_SIGNAL_CONFLICT", "MISSING_PRICE", "RECEIVER_MISSING", "PHONE_INVALID",
    "PHONE_MISMATCH", "ADDRESS_MISSING", "ADDRESS_DETAIL_MISSING", "ADDRESS_IMAGE",
    "ADDRESS_UNVERIFIED", "ADDRESS_AMBIGUOUS", "HANDOFF_REQUEST", "ANGRY_CUSTOMER",
    "PAYMENT_PROOF_IMAGE", "PAYMENT_UNCONFIRMED", "AMOUNT_MISMATCH",
})


def _val(policies, key, default="미완료"):
    return policies.get(key, default)


def _priced(codes, catalog, T):
    """근거에 후보를 가격과 함께 적는다. 없으면 없다고 적는다."""
    return ", ".join("%s(%s)" % (catalog.display(c),
                                 T.money(catalog.price(c)) if catalog.price(c)
                                 else T.t("fl_no_price"))
                     for c in codes) or T.t("fl_no_alt")


def evaluate(state, quote, catalog, policies, out, mode, bot_text=""):
    """이번 턴 기준으로 떠야 할 플래그를 전부 모은다."""
    flags = []
    known = policies.flags
    # 근거는 개발자가 읽고 로그에 쌓인다. 세션 언어를 따르지 않는다
    T = MSG.for_dev()

    def add(key, evidence):
        if key in known:
            flags.append(Flag(key, known[key].get("값", ""), evidence))

    # ---------------------------------------------------------- 품목
    for line in state.lines:
        m = line.match
        if not m:
            continue
        # 품절 — 카탈로그에는 있지만 지금은 못 판다. 대체 후보를 함께 남긴다
        if line.soldout_alts or (m.status == M.CONFIRMED and m.code
                                 and catalog.soldout(m.code)):
            add("SOLDOUT", T.t("fl_soldout", line.key, catalog.display(m.code),
                               _priced(line.soldout_alts, catalog, T)))
            continue
        if line.rejected:
            add("ITEM_REJECTED", T.t("fl_rejected", line.key, catalog.display(m.code),
                                     _priced(line.alternatives, catalog, T)))
            continue

        if m.status == M.AMBIGUOUS:
            add("AMBIGUOUS_ALIAS", T.t("fl_ambiguous", line.key, len(m.candidates),
                                       _priced(m.candidates, catalog, T)))
        elif m.status == M.NOT_FOUND:
            add("PRODUCT_NOT_FOUND", T.t("fl_notfound", line.key, T.rule(m.rule)))
        elif m.status == M.CONFLICT:
            add("PRODUCT_SIGNAL_CONFLICT", m.note)

    if quote["blocked"]:
        # 품절·수량미정은 단가 문제가 아니다. 섞어 보고하면 상담원이 가격표를 뒤지게 된다
        missing = [r["표현"] for r in quote["rows"]
                   if r["단가"] is None and not r.get("품절") and not r.get("수량미정")]
        if missing:
            add("MISSING_PRICE", T.t("fl_missing_price", ", ".join(missing)))

    # ---------------------------------------------------------- 수령 정보
    if not state.receiver:
        add("RECEIVER_MISSING", T.t("fl_receiver_missing"))

    if state.phone:
        raw = str(state.phone.value)
        digits = re.sub(r"\D", "", raw)
        # 구글시트가 "01084770874" 를 숫자 1084770874.0 으로 저장하면 앞자리 0 이 사라지고
        # 소수점 ".0" 때문에 자릿수가 다시 11이 되어, 잘못된 번호가 정상 판정을 받는다.
        # 실제 로그에 이 값이 5건 있다. 자릿수만 세지 말고 형식을 본다.
        if "." in raw:
            add("PHONE_INVALID", T.t("fl_phone_dot", raw))
        elif not digits.startswith("0"):
            add("PHONE_INVALID", T.t("fl_phone_zero", raw))
        elif not (10 <= len(digits) <= 11):
            add("PHONE_INVALID", T.t("fl_phone_len", raw, len(digits)))

        # 형식이 유효해도 잘못 읽혔을 수 있다. 2차 판독과 대조한다.
        second = state.phone_second
        if second and re.sub(r"\D", "", str(second)) != digits:
            add("PHONE_MISMATCH", T.t("fl_phone_mismatch", state.phone.value, second))

    if not state.address_base:
        add("ADDRESS_MISSING", T.t("fl_address_missing"))
    else:
        if not state.address_detail:
            # 동·호를 전제하지 않는다. 고객은 기숙사·농장·컨테이너에 산다
            add("ADDRESS_DETAIL_MISSING", T.t("fl_address_detail"))
        if state.address_base.source == "image":
            add("ADDRESS_IMAGE", T.t("fl_address_image",
                                     state.address_base.source_ref or "image"))

        api = state.addr_api or {}
        if api.get("done"):
            if api.get("total", 0) == 0:
                add("ADDRESS_UNVERIFIED", T.t("fl_address_unverified", api.get("clean", "")))
            elif len(api.get("zips") or []) > 1:
                add("ADDRESS_AMBIGUOUS", T.t("fl_address_ambiguous", api["total"],
                                             len(api["zips"]), ", ".join(api["zips"])))

    # ---------------------------------------------------------- 대화 신호
    if out.get("handoff_request"):
        add("HANDOFF_REQUEST", T.t("fl_handoff"))
    if out.get("angry"):
        add("ANGRY_CUSTOMER", T.t("fl_angry"))
    # 입금증은 있어도 없어도 플래그가 뜬다. 있으면 대조 대상, 없으면 미완료다.
    # 어느 쪽이든 상담원이 은행 내역을 봐야 하므로 검수 목록에서 빠지면 안 된다.
    if state.payment_proof:
        add("PAYMENT_PROOF_IMAGE", T.t("fl_proof", state.payment_proof))
    elif out.get("intent") == "payment_claim":
        add("PAYMENT_UNCONFIRMED", T.t("fl_payment_claim"))
    elif state.lines and not missing_required_any(state, policies):
        add("PAYMENT_UNCONFIRMED", T.t("fl_payment_pending"))

    # ---------------------------------------------------------- 금액
    # 자동 감지에만 남기고 플래그를 올리지 않으면 아무것도 막지 못한다.
    # 실제 로그 196턴 중 16턴에서 감지됐는데 흐름은 그대로 진행됐다.
    # 흐름을 차단하기 시작하므로 지침에서 끌 수 있게 해둔다.
    if bot_text and str(policies.get("AMOUNT_MISMATCH_ENFORCE", "Y")).strip().upper() == "Y":
        bogus = amount_mismatch(bot_text, quote)
        if bogus:
            add("AMOUNT_MISMATCH", T.t("fl_amount", ", ".join(map(str, bogus))))

    return flags


def amount_mismatch(bot_text, quote):
    """응답 문장에 있는데 계산값에는 없는 금액. 돌려주는 것은 그 숫자들이다."""
    if quote.get("total") is None:
        return []
    nums = {int(n.replace(",", "")) for n in re.findall(r"[\d,]{3,}", bot_text or "")}
    legit = {quote["total"], quote["subtotal"], quote["shipping"]}
    legit |= {r["단가"] for r in quote["rows"] if r["단가"]}
    legit |= {r["소계"] for r in quote["rows"] if r["소계"]}
    for r in quote.get("shipping_rows") or []:
        legit |= {r.get("fee"), r.get("threshold"), r.get("amount")}
    legit.discard(None)
    return sorted(n for n in nums if n >= 100 and n not in legit)


# ------------------------------------------------------------------ 자동 감지
def detect(bot_text, state, quote, policies, out, prev_asked, catalog=None, asking=None):
    """사람이 눈으로 보면 놓치는 것을 코드가 잡는다. (설계서 11장)

    asking 은 "이번 턴에 코드가 무언가를 되묻고 있는가" 다. 앱이 stage 로 계산해 넘긴다.
    예전에는 '"?" 가 응답에 있는가' 로 봤는데, 태국어는 물음표 없이 ไหม/มั้ย 로 묻는다.
    그러면 챗봇이 제대로 되물어도 오류로 잡히고, 테스터가 정상 동작을 오류로 보고한다."""
    hits = []
    # 감지 내용도 개발자용이다. 로그 컬럼에 언어가 섞이면 비교가 불가능해진다
    T = MSG.for_dev()

    def hit(name_key, rule, detail):
        # code 는 로그에 쌓이는 값이라 언어를 타지 않는다. 나머지는 화면용이다
        hits.append({"code": name_key, "감지": T.t(name_key),
                     "근거 규칙": rule, "내용": detail})

    # 되물었는지 판정. 넘겨받은 값이 없을 때만 예전 방식으로 폴백한다
    asked_now = asking if asking is not None else ("?" in (bot_text or ""))

    # 금액 환각 — 응답 문장의 숫자를 계산값과 대조
    bogus = amount_mismatch(bot_text, quote)
    if bogus:
        hit("dt_amount", "SHOW_LINE_BASIS",
            T.t("dt_amount_body", ", ".join(map(str, bogus))))

    # 상태 유실 — 이미 확보한 정보를 다시 물으면 표시
    # (한국어 정규식이다. 다른 언어에서는 아직 못 잡는다)
    have = {"수령": bool(state.receiver), "전화": bool(state.phone), "주소": bool(state.address_base)}
    for word, ok in have.items():
        if ok and re.search(word + r"[^.?!]{0,20}(알려|뭐|무엇|어떻게|어디)", bot_text or ""):
            hit("dt_state_lost", "NO_REPEAT_QUESTION", T.t("dt_state_lost_body", word))

    # 스키마 결핍 — 답할 수 없었던 질문
    for mi in out.get("missing_info") or []:
        if not mi.get("found", False):
            hit("dt_schema", "NO_PRODUCT_FACT_GUESS",
                T.t("dt_schema_body", mi.get("asked", ""), mi.get("needed", "")))

    # 입금 단정 (한국어 정규식이다. 다른 언어에서는 아직 못 잡는다)
    if re.search(r"입금(이)?\s*(확인|완료)", bot_text or ""):
        hit("dt_payment", "NO_PAYMENT_JUDGEMENT", T.t("dt_payment_body"))

    # 고객 질문 무시 — 물었는데 답하지 않음. 판정은 LLM 이 인용한 질문으로 한다
    q = str(out.get("customer_question") or "").strip()
    if q and out.get("question_answered") is False:
        hit("dt_question", "ANSWER_CUSTOMER_QUESTION = 필수",
            T.t("dt_question_body", q))

    # 취급 여부 오안내 — 파는 상품을 없다고 하거나, 없다고 해놓고 배송을 약속함
    claim = str(out.get("unavailable_claim") or "").strip()
    if claim and catalog is not None:
        codes = M.codes_for(claim, catalog) or M.near_candidates(claim, catalog, top=1)
        if codes:
            hit("dt_unavailable", "NO_UNAVAILABLE_SUGGEST = 금지",
                T.t("dt_unavailable_body", claim,
                    ", ".join(catalog.display(c) for c in codes)))
    if claim:
        # 없다고 해놓고 같은 상품을 주문에 남겨두면 마지막에 "보내드릴게요" 가 된다.
        # 실제 테스터 노트에 그 대화가 있다
        live = [l for l in state.lines if not l.unavailable]
        keep = [l.key for l in live
                if claim in (l.key or "")
                or (catalog is not None and l.match and l.match.code
                    and claim in catalog.display(l.match.code))]
        if keep:
            hit("dt_unavailable", "NO_UNAVAILABLE_SUGGEST = 금지",
                T.t("dt_unavailable_kept", claim, ", ".join(keep)))

    # 되물음 누락 — 모호 항목이 있는데 후보 제시 없이 진행
    ambiguous = [l for l in state.lines if l.match and l.match.status == M.AMBIGUOUS]
    if ambiguous and _val(policies, "AMBIGUOUS_ALIAS") == "되물음":
        if not asked_now:
            hit("dt_ask_missing", "AMBIGUOUS_ALIAS = 되물음",
                T.t("dt_ask_missing_body", ", ".join(l.key for l in ambiguous)))

    # 미확인 통과 — DB에 없는 표현을 무시하고 진행
    notfound = [l for l in state.lines
                if l.match and l.match.status == M.NOT_FOUND and not l.unavailable]
    if notfound and not asked_now:
        hit("dt_unchecked", "PRODUCT_NOT_FOUND = 되물음",
            T.t("dt_unchecked_body", ", ".join(l.key for l in notfound)))

    # 잡담 미복귀
    if out.get("intent") == "smalltalk" and prev_asked and not asked_now:
        hit("dt_smalltalk", "SMALLTALK_RETURN", T.t("dt_smalltalk_body"))

    # 추출 실패 — 명백한 주문 발화인데 item_ops 가 비어 있음
    if out.get("intent") == "order" and not (out.get("item_ops") or []):
        hit("dt_extract", "—", T.t("dt_extract_body"))

    return hits
