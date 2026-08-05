# -*- coding: utf-8 -*-
"""
결정적 응답 조립기.

거래명세서와 되물음 문장은 코드가 만든다. LLM 에게 맡기지 않는다.
금액을 LLM 이 문장으로 쓰는 순간 환각이 가능해지고, 그때부터 주문서를 믿을 수 없다.

이 파일은 대화의 흐름 자체를 붙잡는 역할도 한다.

    주문 수집 → 거래명세서 → 필수 정보 수집 → 입금 안내 → 완료

고객이 중간에 다른 질문으로 새더라도, 코드가 만드는 문장은 언제나
"지금 이 흐름에서 다음에 필요한 것"이다. LLM 이 딴 얘기에 답하더라도
그 뒤에 이 문장이 따라붙으므로 대화가 자연스럽게 제자리로 돌아온다.
"""
import difflib
import re

from . import matching as M

GREETING = "안녕하세요 고객님!"

# 지침의 REQUIRED_FIELDS 값을 상태 필드와 화면 문구로 옮기는 표.
# 흐름의 순서는 코드가 강제하고, 무엇을 필수로 볼지는 지침이 정한다.
FIELD_ALIASES = {
    "수령인": ("receiver", "받으실 분 성함"),
    "전화": ("phone", "연락처"),
    "주소": ("address_base", "배송지 주소"),
}
DEFAULT_REQUIRED = "수령인,전화,주소"

# 품목이 아직 확정되지 않아 코드가 되묻고 있는 단계.
# 이때는 다른 것을 함께 묻지 않는다. 한 턴에 질문은 하나여야 한다.
ASK_STAGES = {"order_ask", "ambiguous_ask", "reject_ask",
              "notfound_ask", "quantity_ask", "blocked"}


def won(n):
    return "%s원" % f"{int(n):,}"


def spoken(expr):
    """고객에게 되읊을 표현. 내부 품목코드는 지운다.
    코드는 우리 식별자일 뿐이고, 고객은 그게 무엇인지 모른다."""
    cleaned = re.sub(r"\b[A-Za-z]\d{3,}\b", "", str(expr or ""))
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,-")
    return cleaned or "말씀하신 상품"


def nearest(expr, catalog, top=3, floor=0.3):
    """DB 에 없는 표현에 대해 가장 가까운 상품을 고른다.
    후보를 지어내지 않는다. 반드시 실제 DB 행에서만 뽑는다."""
    scored = sorted(
        ((difflib.SequenceMatcher(None, expr, catalog.display(c)).ratio(), c)
         for c in catalog.items), reverse=True)
    return [c for r, c in scored[:top] if r >= floor]


def invoice_sig(quote):
    """거래명세서를 다시 보여줘야 하는지 판단하는 지문."""
    return tuple(sorted((r["매칭"], r["수량"]) for r in quote["rows"])) + (quote["total"],)


def required_fields(policies):
    raw = str(policies.get("REQUIRED_FIELDS", DEFAULT_REQUIRED) or DEFAULT_REQUIRED)
    out = []
    for token in raw.split(","):
        pair = FIELD_ALIASES.get(token.strip())
        if pair:
            out.append(pair)
    return out or [FIELD_ALIASES[k] for k in ("수령인", "전화", "주소")]


def missing_required(state, policies):
    """매 턴 다시 계산한다. 고객이 순서와 무관하게 정보를 주더라도
    이미 받은 것은 묻지 않고 아직 없는 것만 묻기 위해서다."""
    return [(k, label) for k, label in required_fields(policies) if not getattr(state, k)]


def build(state, quote, catalog, policies, history):
    """반환값은 (고정 문장, 종류). 인사는 여기서 붙이지 않는다.
    잡담 답변이 앞에 오는 경우가 있어, 조립이 끝난 뒤 맨 앞에 붙여야 한다."""
    return _body(state, quote, catalog, policies)


def stage(state, quote, catalog, policies):
    """지금이 어느 단계인지만 본다. 상태를 바꾸지 않는다.

    build() 는 거래명세서를 보여줬다는 표시를 남기므로,
    LLM 호출 전에 단계만 알고 싶을 때 이 함수를 쓴다."""
    keep = state.invoice_sig
    try:
        return _body(state, quote, catalog, policies)[1]
    finally:
        state.invoice_sig = keep


def _body(state, quote, catalog, policies):
    """우선순위가 있다. 모호한 항목이 남아 있는데 거래명세서를 먼저 내밀면
    고객이 잘못된 상품으로 입금한다. 되물음이 항상 앞선다."""
    lines = state.lines

    # ---------------------------------------------------------- 1단계 주문 수집
    for l in lines:
        if l.rejected and l.alternatives:
            opts = " / ".join("%s %s" % (catalog.display(c), won(catalog.price(c) or 0))
                              for c in l.alternatives)
            return ("말씀하신 '%s'는 %s 중 어떤 것일까요?" % (spoken(l.key), opts), "reject_ask")

    if policies.get("AMBIGUOUS_ALIAS") == "되물음":
        for l in lines:
            if l.match and l.match.status == M.AMBIGUOUS:
                opts = " / ".join("%s %s" % (catalog.display(c), won(catalog.price(c) or 0))
                                  for c in l.match.candidates)
                return ("'%s'는 %s 중 어떤 것을 말씀하시는 걸까요?" % (spoken(l.key), opts), "ambiguous_ask")

    # DB 에 없는 표현 — "못 찾았다"로 끝내지 않고 가장 가까운 상품을 들이민다.
    # 되물음은 대화를 끝내는 것이 아니라 거래명세서를 완성하려고 정보를 채우는 과정이다.
    if policies.get("PRODUCT_NOT_FOUND") == "되물음":
        for l in lines:
            if l.match and l.match.status == M.NOT_FOUND:
                near = nearest(l.key, catalog)
                if near:
                    opts = " / ".join("%s %s" % (catalog.display(c), won(catalog.price(c) or 0))
                                      for c in near)
                    return ("'%s'는 이 중 어떤 것일까요? %s\n"
                            "이 중에 없으면 사진 보내주시면 찾아드릴게요."
                            % (spoken(l.key), opts), "notfound_ask")
                return ("'%s'가 어떤 상품인지 조금만 더 알려주시겠어요? 사진을 보내주셔도 좋아요."
                        % spoken(l.key), "notfound_ask")

    if not lines:
        return ("어떤 상품 찾으세요? 상품명을 말씀해주시거나 사진을 보내주시면 담아드릴게요.",
                "order_ask")

    no_qty = [l for l in lines if l.quantity is None]
    if no_qty:
        return ("%s는 몇 개 필요하신가요?"
                % ", ".join("'%s'" % spoken(l.key) for l in no_qty), "quantity_ask")

    if quote["blocked"]:
        miss = [r["표현"] for r in quote["rows"] if r["단가"] is None]
        return ("%s 가격을 확인하고 있어요. 확인되는 대로 총액 알려드릴게요."
                % ", ".join(miss), "blocked")

    # ---------------------------------------------------------- 2단계 거래명세서
    out = []
    sig = invoice_sig(quote)
    show_invoice = sig != state.invoice_sig
    if show_invoice:
        state.invoice_sig = sig
        out.append(_invoice_text(quote, policies))

    # ---------------------------------------------------------- 3단계 이후
    # 무엇이 비었는지는 코드가 계산해 pending() 으로 LLM 에 넘기고,
    # 그것을 묻는 문장은 LLM 이 만든다. 금액·상품 후보와 달리
    # 정보 요청은 환각 위험이 없어 자연스러운 표현을 맡기는 편이 낫다.
    if state.payment_proof:
        # 입금 확인은 사람이 은행에서 한다. 코드도 LLM 도 확인됐다고 말하지 않는다.
        out.append("입금증 받았습니다.")

    pend = pending(state, policies)
    if not (pend["missing"] or pend["detail"]):
        out.append("주문 감사합니다! 확인하는 대로 바로 보내드릴게요.")
        return ("\n\n".join(out), "complete")

    return ("\n\n".join(out), "invoice" if show_invoice else "collecting")


def pending(state, policies):
    """아직 채우지 못한 것. LLM 에게 넘길 목록이자, LLM 이 묻지 않았을 때 쓸 대체 문장의 근거."""
    missing = [label for _, label in missing_required(state, policies)]

    detail_rule = str(policies.get("ASK_ADDRESS_DETAIL", "권장") or "권장").strip()
    want_detail = (not state.address_detail) and detail_rule != "생략" and bool(state.address_base)

    # 입금증은 먼저 요구하지 않는다. 고객이 보내면 받아서 검수에 올릴 뿐이다.
    return {"missing": missing, "detail": want_detail, "detail_rule": detail_rule}


def fallback_ask(pend):
    """LLM 이 아무것도 묻지 않았을 때만 쓰는 안전망. 흐름이 멈추지 않게 한다."""
    if pend["missing"]:
        return "%s를 알려주시겠어요?" % ", ".join(pend["missing"])
    if pend["detail"]:
        return "동·호수도 알려주시면 배송이 더 정확해요."
    return ""


def _invoice_text(quote, policies):
    out = []
    for r in quote["rows"]:
        # 포장단위는 시트에 있으면 쓰고 없으면 넘어간다. 컬럼 존재를 전제하지 않는다.
        pack = (r.get("포장단위") or "").strip()
        name = "%s %s" % (r["매칭"], pack) if pack else r["매칭"]
        out.append("%s %d개 %s" % (name, r["수량"], won(r["소계"])))

    threshold = policies.get_int("FREE_SHIPPING_THRESHOLD", 0)
    if quote["shipping"]:
        out.append("배송비 %s" % won(quote["shipping"]))
    elif threshold:
        out.append("배송비 0원 (%s 이상 무료배송)" % won(threshold))

    out.append("총 %s을 아래 계좌로 입금주시면 감사하겠습니다." % won(quote["total"]))
    account = policies.get("ACCOUNT_INFO", "")
    if account:
        out.append(account)
    return "\n".join(out)


def upsell_context(quote, policies):
    """무료배송 기준에 못 미치면 얼마가 모자란지 알려준다.
    실제 제안 문장은 지침(RECIPE_SUGGEST 등)에 따라 LLM 이 만든다."""
    threshold = policies.get_int("FREE_SHIPPING_THRESHOLD", 0)
    if not threshold or quote["total"] is None or quote["shipping"] == 0:
        return None
    gap = threshold - quote["subtotal"]
    if gap <= 0:
        return None
    return {"threshold": threshold, "gap": gap}
