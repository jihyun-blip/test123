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


# 숫자를 소리 내어 읽었을 때 받침이 있는지. 영/일/삼/육/칠/팔 은 있고 이/사/오/구 는 없다.
_DIGIT_BATCHIM = {"0": True, "1": True, "3": True, "6": True, "7": True, "8": True,
                  "2": False, "4": False, "5": False, "9": False}


def _has_batchim(word):
    w = str(word or "").rstrip("'\"), ]}").strip()
    if not w:
        return False
    ch = w[-1]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    if ch.isdigit():
        return _DIGIT_BATCHIM.get(ch, False)
    if ch.isalpha():
        # 영문은 소리 기준. 자음으로 끝나면 받침이 있는 것으로 본다
        return ch.lower() not in "aeiou"
    return False


def josa(word, with_batchim, without):
    """'삼겹살는' 같은 어색한 조사를 막는다. 상품명은 시트에서 오므로 받침을 미리 알 수 없다."""
    return with_batchim if _has_batchim(word) else without


def eun(word):
    return word + josa(word, "은", "는")


def i_ga(word):
    return word + josa(word, "이", "가")


def eul(word):
    return word + josa(word, "을", "를")


def called(line, catalog):
    """고객에게 그 품목을 뭐라고 부를지.

    이미 어느 상품인지 확정됐으면 상품명으로 부른다.
    고객이 라벨코드를 적었거나 사진에서 코드를 읽어온 경우, 고객 표현을 그대로 쓰면
    코드가 노출되거나 코드를 지운 빈 문자열이 남는다."""
    if line.match and line.match.status == M.CONFIRMED and line.match.code:
        return catalog.display(line.match.code)
    return spoken(line.key)


def spoken(expr):
    """고객에게 되읊을 표현. 내부 품목코드는 지운다.
    코드는 우리 식별자일 뿐이고, 고객은 그게 무엇인지 모른다."""
    cleaned = re.sub(r"\b[A-Za-z]\d{3,}\b", "", str(expr or ""))
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,-")
    return cleaned or "말씀하신 상품"


def nearest(expr, catalog, top=3):
    """오타로 보이는 표현에 가장 가까운 실제 상품. 판정 기준은 matching 에 있다."""
    return M.near_candidates(expr, catalog, top)


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
    return _body(state, quote, catalog, policies, history)


def stage(state, quote, catalog, policies):
    """지금이 어느 단계인지만 본다. 상태를 바꾸지 않는다.

    build() 는 거래명세서를 보여줬다는 표시를 남기므로,
    LLM 호출 전에 단계만 알고 싶을 때 이 함수를 쓴다."""
    keep = state.invoice_sig
    try:
        return _body(state, quote, catalog, policies)[1]
    finally:
        state.invoice_sig = keep


def _asked_before(history, phrase):
    return any(phrase in (h.get("bot") or "") for h in (history or []))


def _body(state, quote, catalog, policies, history=None):
    """우선순위가 있다. 모호한 항목이 남아 있는데 거래명세서를 먼저 내밀면
    고객이 잘못된 상품으로 입금한다. 되물음이 항상 앞선다."""
    # 취급하지 않는다고 판정된 줄은 되묻지 않는다. 알릴 문구는 앱이 앞에 붙인다.
    lines = [l for l in state.lines if not l.unavailable]

    # ---------------------------------------------------------- 1단계 주문 수집
    for l in lines:
        if l.rejected and l.alternatives:
            opts = " / ".join("%s %s" % (catalog.display(c), won(catalog.price(c) or 0))
                              for c in l.alternatives)
            return ("말씀하신 %s %s 중 어떤 것일까요?"
                    % (eun("'%s'" % spoken(l.key)), opts), "reject_ask")

    if policies.get("AMBIGUOUS_ALIAS") == "되물음":
        for l in lines:
            if l.match and l.match.status == M.AMBIGUOUS:
                opts = " / ".join("%s %s" % (catalog.display(c), won(catalog.price(c) or 0))
                                  for c in l.match.candidates)
                return ("%s %s 중 어떤 것을 말씀하시는 걸까요?"
                        % (eun("'%s'" % spoken(l.key)), opts), "ambiguous_ask")

    # DB 에 없는 표현 — "못 찾았다"로 끝내지 않고 가장 가까운 상품을 들이민다.
    # 되물음은 대화를 끝내는 것이 아니라 거래명세서를 완성하려고 정보를 채우는 과정이다.
    if policies.get("PRODUCT_NOT_FOUND") == "되물음":
        for l in lines:
            if l.match and l.match.status == M.NOT_FOUND:
                near = nearest(l.key, catalog)
                if near:
                    opts = " / ".join("%s %s" % (catalog.display(c), won(catalog.price(c) or 0))
                                      for c in near)
                    return ("%s 이 중 어떤 것일까요? %s\n"
                            "이 중에 없으면 사진 보내주시면 찾아드릴게요."
                            % (eun("'%s'" % spoken(l.key)), opts), "notfound_ask")
                return ("%s 어떤 상품인지 조금만 더 알려주시겠어요? 사진을 보내주셔도 좋아요."
                        % i_ga("'%s'" % spoken(l.key)), "notfound_ask")

    if not lines:
        return ("어떤 상품 찾으세요? 상품명을 말씀해주시거나 사진을 보내주시면 담아드릴게요.",
                "order_ask")

    no_qty = [l for l in lines if l.quantity is None]
    if no_qty:
        names = ", ".join(called(l, catalog) for l in no_qty)
        if len(no_qty) > 1:
            ask = "%s 각각 몇 개씩 필요하신가요?" % names
            # 같은 질문을 이미 했는데 또 물어야 한다면 답하는 법을 예시로 보여준다.
            # 숫자 하나만 답하면 어느 품목인지 알 수 없어 계속 되묻게 된다.
            if _asked_before(history, "몇 개"):
                ask += "\n(예: %s 처럼 알려주세요)" % ", ".join(
                    "%s %d개" % (called(l, catalog), i + 1) for i, l in enumerate(no_qty))
            return (ask, "quantity_ask")
        return ("%s 몇 개 필요하신가요?" % eun(names), "quantity_ask")

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
        return "%s 알려주시겠어요?" % eul(", ".join(pend["missing"]))
    if pend["detail"]:
        return "동·호수도 알려주시면 배송이 더 정확해요."
    return ""


def _invoice_text(quote, policies):
    out = []
    for r in quote["rows"]:
        # 포장단위는 시트에 있으면 쓰고 없으면 넘어간다. 컬럼 존재를 전제하지 않는다.
        pack = (r.get("포장단위") or "").strip()
        name = "%s %s" % (r["매칭"], pack) if pack else r["매칭"]
        line = "%s %d개 %s" % (name, r["수량"], won(r["소계"]))
        note = (r.get("요청") or "").strip()
        if note and "→" in note:
            # 요청한 무게와 실제 나가는 양이 다르면 숨기지 않고 적는다
            line += "  (%s)" % note
        out.append(line)

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


def upsell_context(quote, policies, catalog, exclude=(), top=3):
    """무료배송까지 얼마가 모자란지, 무엇을 더 담으면 넘기는지 계산한다.

    후보는 반드시 실제 DB 행에서 뽑는다. 이걸 안 주면 LLM 이 없는 상품을 지어낸다.
    권유 문장 자체는 지침(UPSELL_FREE_SHIPPING, RECIPE_SUGGEST)에 따라 LLM 이 만든다."""
    if str(policies.get("UPSELL_FREE_SHIPPING", "허용")).strip() != "허용":
        return None

    threshold = policies.get_int("FREE_SHIPPING_THRESHOLD", 0)
    if not threshold or quote["total"] is None or quote["shipping"] == 0:
        return None

    gap = threshold - quote["subtotal"]
    if gap <= 0:
        return None

    priced = [(c, catalog.price(c)) for c in catalog.items
              if catalog.price(c) and c not in exclude]
    # 하나만 더 담아도 기준을 넘기는 상품을 싼 순으로. 없으면 비싼 순으로 보여준다.
    over = sorted([x for x in priced if x[1] >= gap], key=lambda x: x[1])[:top]
    if not over:
        over = sorted(priced, key=lambda x: -x[1])[:top]

    return {
        "threshold": threshold, "gap": gap,
        "suggestions": [{"name": catalog.display(c), "price": p} for c, p in over],
    }
