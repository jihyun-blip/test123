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

문장 자체는 messages.py 의 언어별 표에서 가져온다. 세션 언어는 Policies 가 들고 있다.
"""
import re

from . import matching as M
from . import messages as MSG

# 지침의 REQUIRED_FIELDS 값을 상태 필드와 화면 문구로 옮기는 표.
# 흐름의 순서는 코드가 강제하고, 무엇을 필수로 볼지는 지침이 정한다.
# 왼쪽 토큰은 제어값이라 번역하지 않는다. 오른쪽은 문구표의 키다.
FIELD_ALIASES = {
    "수령인": ("receiver", "field_receiver"),
    "전화": ("phone", "field_phone"),
    "주소": ("address_base", "field_address"),
}
DEFAULT_REQUIRED = "수령인,전화,주소"

# 품목이 아직 확정되지 않아 코드가 되묻고 있는 단계.
# 이때는 다른 것을 함께 묻지 않는다. 한 턴에 질문은 하나여야 한다.
ASK_STAGES = {"order_ask", "ambiguous_ask", "reject_ask", "soldout_ask",
              "notfound_ask", "quantity_ask", "blocked"}


def msg(policies):
    return MSG.for_lang(getattr(policies, "lang", MSG.DEFAULT_LANG))


def won(n, policies=None):
    return msg(policies).money(n)


def spoken(expr, policies=None):
    """고객에게 되읊을 표현. 내부 품목코드는 지운다.
    코드는 우리 식별자일 뿐이고, 고객은 그게 무엇인지 모른다."""
    return MSG.strip_code(expr, getattr(policies, "lang", MSG.DEFAULT_LANG))


def called(line, catalog, policies=None):
    """고객에게 그 품목을 뭐라고 부를지.

    이미 어느 상품인지 확정됐으면 상품명으로 부른다.
    고객이 라벨코드를 적었거나 사진에서 코드를 읽어온 경우, 고객 표현을 그대로 쓰면
    코드가 노출되거나 코드를 지운 빈 문자열이 남는다."""
    if line.match and line.match.status == M.CONFIRMED and line.match.code:
        return catalog.display(line.match.code)
    return spoken(line.key, policies)


def nearest(expr, catalog, top=3):
    """오타로 보이는 표현에 가장 가까운 실제 상품. 판정 기준은 matching 에 있다."""
    return M.near_candidates(expr, catalog, top)


def invoice_sig(quote):
    """거래명세서를 다시 보여줘야 하는지 판단하는 지문."""
    return tuple(sorted((r["매칭"], r["수량"]) for r in quote["rows"])) + (quote["total"],)


def required_fields(policies, T=None):
    """T 를 넘기면 그 언어의 항목 이름을 쓴다. 인계 메모는 개발자 언어를 쓰기 때문이다."""
    raw = str(policies.get("REQUIRED_FIELDS", DEFAULT_REQUIRED) or DEFAULT_REQUIRED)
    T = T or msg(policies)
    out = []
    for token in raw.split(","):
        pair = FIELD_ALIASES.get(token.strip())
        if pair:
            out.append((pair[0], T.t(pair[1])))
    return out or [(a, T.t(k)) for a, k in
                   (FIELD_ALIASES[x] for x in ("수령인", "전화", "주소"))]


def missing_required(state, policies, T=None):
    """매 턴 다시 계산한다. 고객이 순서와 무관하게 정보를 주더라도
    이미 받은 것은 묻지 않고 아직 없는 것만 묻기 위해서다.

    다만 고객이 줄 수 없는 정보도 있다. 몇 번을 물어도 못 받으면 묻기를 멈춘다.
    계속 물으면 대화가 끝나지 않고, 고객은 같은 질문만 듣는다.
    빠진 채로 남는 것은 플래그로 드러나 상담원이 처리한다."""
    limit = policies.get_int("ASK_RETRY_LIMIT", 2)
    return [(k, label) for k, label in required_fields(policies, T)
            if not getattr(state, k) and state.ask_rounds.get(k, 0) < limit]


def given_up(state, policies, T=None):
    """묻기를 포기한 필수 항목. 상담원이 채워야 하는 목록이다."""
    limit = policies.get_int("ASK_RETRY_LIMIT", 2)
    return [label for k, label in required_fields(policies, T)
            if not getattr(state, k) and state.ask_rounds.get(k, 0) >= limit]


def build(state, quote, catalog, policies, history=None):
    """반환값은 (고정 문장, 종류). 인사는 여기서 붙이지 않는다.
    잡담 답변이 앞에 오는 경우가 있어, 조립이 끝난 뒤 맨 앞에 붙여야 한다."""
    return _body(state, quote, catalog, policies, history)


def stage(state, quote, catalog, policies):
    """지금이 어느 단계인지만 본다. 상태를 바꾸지 않는다.

    build() 는 거래명세서를 보여줬다는 표시를 남기므로,
    LLM 호출 전에 단계만 알고 싶을 때 이 함수를 쓴다."""
    keep = state.invoice_sig
    keep_done = state.done_shown
    try:
        return _body(state, quote, catalog, policies)[1]
    finally:
        state.invoice_sig = keep
        state.done_shown = keep_done


# 고객이 물음표 없이 묻는 일이 잦아, 어미도 같이 본다.
# 태국어는 물음표를 거의 쓰지 않고 ไหม/มั้ย/หรือ/คะ 로 묻는다.
_QUESTION_TAIL = re.compile(
    r"(없나요|없어요|없을까요|있나요|있을까요|되나요|될까요|맞나요|"
    r"어때요|어떤가요|뭐예요|뭔가요|무엇인가요|알려주세요|궁금|"
    r"ไหม|มั้ย|หรือเปล่า|รึเปล่า|อะไร|เท่าไหร่|เท่าไร|ยังไง|อย่างไร|กี่|"
    r"ได้ไหม|มีไหม|คะ$)")


def asked_question(text):
    """고객이 이번 발화에서 무언가를 물었는가.

    intent 만 믿으면 안 된다. '삼겹살 맞아요. 메기랑 비슷한 거 없어요?' 처럼
    답과 질문이 한 문장에 섞이면 모델은 order 로만 분류하고, 그러면 질문에 대한
    답이 조립 과정에서 통째로 버려진다. 고객 말을 무시하는 봇이 되는 자리다."""
    t = str(text or "").strip()
    if not t:
        return False
    return "?" in t or bool(_QUESTION_TAIL.search(t))


def _asked_before(history, phrase):
    return any(phrase in (h.get("bot") or "") for h in (history or []))


def options_text(codes, catalog, policies):
    """후보를 가격과 함께 늘어놓는다. 후보 자체는 부르는 쪽에서 이미 잘라 왔다."""
    T = msg(policies)
    return " / ".join("%s %s" % (catalog.display(c), T.money(catalog.price(c) or 0))
                      for c in codes)


def _attr_question(codes, catalog, policies, limit=5):
    """후보가 너무 많을 때 한 단계 먼저 좁히는 질문.

    종류(species)와 부위(part)는 마스터에 이미 있는데 코드가 안 쓰고 있었다.
    후보 100개를 가격과 함께 늘어놓는 것보다 "돼지, 소, 닭 중 어느 쪽일까요?" 가
    고객에게도 우리에게도 빠르다."""
    T = msg(policies)
    for col, key in (("species", "attr_species"), ("part", "attr_part")):
        vals = []
        for c in codes:
            v = str(catalog.items.get(c, {}).get(col) or "").strip()
            if v and v not in vals:
                vals.append(v)
        if len(vals) > 1:
            return T.t(key, ", ".join(vals[:limit]))
    return None


def ambiguous_ask(line, catalog, policies):
    """모호한 품목을 되묻는 문장. 후보 수에 따라 묻는 방식이 달라진다.

    상한이 없으면 후보 20개에서 353자짜리 가격표가 나간다.
    품목 1,000개에서는 대화 자체가 불가능해진다."""
    T = msg(policies)
    max_opts = policies.get_int("AMBIGUOUS_MAX_OPTIONS", 5) or 5
    attr_th = policies.get_int("AMBIGUOUS_ATTR_THRESHOLD", 20) or 20
    codes = catalog.by_rank(line.match.candidates)
    name = T.eun(T.quote_word(spoken(line.key, policies)))

    if len(codes) > attr_th:
        q = _attr_question(codes, catalog, policies)
        if q:
            return T.t("ambiguous_attr", name, q)

    # 후보가 상한을 넘으면 나열하지 않고 제일 많이 나가는 하나를 권한다.
    # 고객이 아니라고 하면 그때 상위 N 개를 보여준다.
    if len(codes) > max_opts and not line.top_offer_declined:
        top = codes[0]
        return T.t("ambiguous_top", name, catalog.display(top),
                   T.money(catalog.price(top) or 0))

    return T.t("ambiguous_list", name, options_text(codes[:max_opts], catalog, policies))


def pack_ask(line, catalog, policies):
    """포장단위로 딱 떨어지지 않는 수량을 되묻는 문장.

    "몇 개 필요하신가요?" 로는 고객이 왜 다시 답해야 하는지 모른다.
    포장단위가 얼마인지와 고를 수 있는 개수를 함께 알려준다."""
    T = msg(policies)
    code = line.match.code if (line.match and line.match.status == M.CONFIRMED) else None
    pack = catalog.unit(code) if code else ""
    name = T.eun(called(line, catalog, policies))
    opts = line.pack_options

    def label(n):
        return catalog.units.pack_label(n, pack) or "%d" % n

    if len(opts) > 1:
        return T.t("pack_ask_two", name, pack, opts[0], label(opts[0]),
                   opts[1], label(opts[1]))
    return T.t("pack_ask_one", name, pack, opts[0], label(opts[0]))


def _body(state, quote, catalog, policies, history=None):
    """우선순위가 있다. 모호한 항목이 남아 있는데 거래명세서를 먼저 내밀면
    고객이 잘못된 상품으로 입금한다. 되물음이 항상 앞선다."""
    T = msg(policies)
    # 취급하지 않는다고 판정된 줄은 되묻지 않는다. 알릴 문구는 앱이 앞에 붙인다.
    lines = [l for l in state.lines if not l.unavailable]

    # ---------------------------------------------------------- 1단계 주문 수집
    for l in lines:
        if l.rejected and l.alternatives:
            return (T.t("reject_ask", T.eun(T.quote_word(spoken(l.key, policies))),
                        options_text(l.alternatives, catalog, policies)), "reject_ask")

    # 품절은 재고 문제라 다시 들어온다. 없다고 끝내지 말고 같은 부위의 대체를 권한다
    for l in lines:
        if l.soldout_alts:
            return (T.t("soldout_ask", T.eun(called(l, catalog, policies)),
                        options_text(l.soldout_alts, catalog, policies)), "soldout_ask")

    if policies.get("AMBIGUOUS_ALIAS") == "되물음":
        for l in lines:
            if l.match and l.match.status == M.AMBIGUOUS:
                return (ambiguous_ask(l, catalog, policies), "ambiguous_ask")

    # DB 에 없는 표현 — "못 찾았다"로 끝내지 않고 가장 가까운 상품을 들이민다.
    # 되물음은 대화를 끝내는 것이 아니라 거래명세서를 완성하려고 정보를 채우는 과정이다.
    if policies.get("PRODUCT_NOT_FOUND") == "되물음":
        for l in lines:
            if l.match and l.match.status == M.NOT_FOUND:
                near = nearest(l.key, catalog)
                name = T.quote_word(spoken(l.key, policies))
                if near:
                    return (T.t("notfound_near", T.eun(name),
                                options_text(near, catalog, policies)), "notfound_ask")
                return (T.t("notfound_bare", T.i_ga(name)), "notfound_ask")

    if not lines:
        # 이미 사진을 보낸 고객에게 사진을 보내라고 하면 대화가 막힌다.
        # 못 읽었다는 사실을 밝히고 다른 길을 제시해야 한다.
        # 다만 주소 사진을 보낸 고객에게 "상품을 못 찾았다"고 하면 엉뚱한 말이 된다.
        # 상품 사진을 보냈는데 못 읽은 경우에만 그렇게 말한다.
        if any(i.get("kind") == "product" for i in state.images or []):
            return (T.t("order_ask_image"), "order_ask")
        return (T.t("order_ask"), "order_ask")

    # 포장단위에 안 맞는 수량도 결국 수량 미확정이다. 새 단계를 만들지 않고
    # 지금 있는 되물음 자리로 보낸다. 올려서 더 청구하는 대신 고객이 고르게 한다.
    for l in lines:
        if l.pack_options:
            return (pack_ask(l, catalog, policies), "quantity_ask")

    no_qty = [l for l in lines if l.quantity is None]
    if no_qty:
        names = ", ".join(called(l, catalog, policies) for l in no_qty)
        if len(no_qty) > 1:
            ask = T.t("qty_ask_each", names)
            # 같은 질문을 이미 했는데 또 물어야 한다면 답하는 법을 예시로 보여준다.
            # 숫자 하나만 답하면 어느 품목인지 알 수 없어 계속 되묻게 된다.
            if _asked_before(history, T.t("qty_marker")):
                ask += T.t("qty_ask_example", ", ".join(
                    T.t("qty_example_item", called(l, catalog, policies), i + 1)
                    for i, l in enumerate(no_qty)))
            return (ask, "quantity_ask")
        return (T.t("qty_ask_one", T.eun(names)), "quantity_ask")

    if quote["blocked"]:
        miss = [r["표현"] for r in quote["rows"] if r["단가"] is None]
        return (T.t("blocked", ", ".join(miss)), "blocked")

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
        out.append(T.t("payment_proof"))

    pend = pending(state, policies)
    if not (pend["missing"] or pend["detail"]):
        # 마무리 인사는 한 번이면 된다. 매 턴 반복하면 고객이 같은 말을 계속 듣는다.
        if not state.done_shown:
            state.done_shown = True
            gave = given_up(state, policies)
            if gave:
                out.append(T.t("given_up", T.eun(", ".join(gave))))
            out.append(T.t("done"))
        return ("\n\n".join(out), "complete")

    return ("\n\n".join(out), "invoice" if show_invoice else "collecting")


def pending(state, policies):
    """아직 채우지 못한 것. LLM 에게 넘길 목록이자, LLM 이 묻지 않았을 때 쓸 대체 문장의 근거."""
    missing = [label for _, label in missing_required(state, policies)]

    detail_rule = str(policies.get("ASK_ADDRESS_DETAIL", "권장") or "권장").strip()
    want_detail = (not state.address_detail) and detail_rule != "생략" and bool(state.address_base)

    # 입금증은 먼저 요구하지 않는다. 고객이 보내면 받아서 검수에 올릴 뿐이다.
    return {"missing": missing, "detail": want_detail, "detail_rule": detail_rule,
            # 이번 턴에 무엇을 물었는지 앱이 세어 둘 수 있게 상태 필드명도 같이 넘긴다
            "keys": [k for k, _ in missing_required(state, policies)]}


def fallback_ask(pend, policies=None):
    """LLM 이 아무것도 묻지 않았을 때만 쓰는 안전망. 흐름이 멈추지 않게 한다."""
    T = msg(policies)
    if pend["missing"]:
        return T.t("ask_missing", T.eul(", ".join(pend["missing"])))
    if pend["detail"]:
        # 동·호를 전제하지 않는다. 실제 배송지 10건 중 동·호 형식은 1건뿐이고
        # 나머지는 기숙사·농장·비닐하우스·컨테이너다. 동·호를 물으면 그 고객은
        # 영원히 답할 수 없고, 실제로 그 실패가 결핍 로그에 남아 있다.
        return T.t("ask_detail")
    return ""


def _invoice_text(quote, policies):
    T = msg(policies)
    out = []
    for r in quote["rows"]:
        # 무게로 포장된 상품은 총 중량 한 가지로만 적는다.
        # '1kg 3개' 처럼 포장단위와 개수를 나란히 두면 단위가 두 번 나와,
        # 3kg 인지 1kg 인지 고객이 알 수 없다.
        weight = (r.get("총중량") or "").strip()
        if weight:
            line = T.t("invoice_line_weight", r["매칭"], weight, T.money(r["소계"]))
        else:
            # 포장단위는 시트에 있으면 쓰고 없으면 넘어간다. 컬럼 존재를 전제하지 않는다.
            pack = (r.get("포장단위") or "").strip()
            name = "%s %s" % (r["매칭"], pack) if pack else r["매칭"]
            line = T.t("invoice_line", name, r["수량"], T.money(r["소계"]))
        note = (r.get("요청") or "").strip()
        if note and "→" in note:
            # 요청한 무게와 실제 나가는 양이 다르면 숨기지 않고 적는다
            line += "  (%s)" % note
        out.append(line)

    # 배송유형이 갈리면 유형별로 나눠 적는다. 총액만 맞으면 고객은 왜 비싼지 모른다
    ship_rows = [r for r in (quote.get("shipping_rows") or []) if r.get("ship_type")]
    if len(ship_rows) > 1:
        for r in ship_rows:
            st = T.ship_type(r["ship_type"])
            if r["fee"]:
                out.append(T.t("ship_fee_typed", st, T.money(r["fee"])))
            elif r["threshold"]:
                out.append(T.t("ship_free_typed", st, T.money(r["threshold"])))
        if quote.get("shipping_rule") == "최대" and \
                sum(r["fee"] for r in ship_rows) != quote["shipping"]:
            out.append(T.t("ship_max_only", T.money(quote["shipping"])))
    elif quote["shipping"]:
        out.append(T.t("ship_fee", T.money(quote["shipping"])))
    else:
        threshold = (ship_rows[0]["threshold"] if ship_rows
                     else policies.get_int("FREE_SHIPPING_THRESHOLD", 0))
        if threshold:
            out.append(T.t("ship_free", T.money(threshold)))

    # 문구에 이미 조사가 들어 있다. 여기서 또 붙이면 "32,000원을을" 이 된다.
    # 금액은 언제나 '원' 으로 끝나므로 문구 쪽 조사로 충분하다
    out.append(T.t("invoice_total", T.money(quote["total"])))
    account = policies.get("ACCOUNT_INFO", "")
    if account:
        # 계좌번호는 고객이 은행 앱에 그대로 입력한다. 어느 언어에서도 그대로 둔다
        out.append(str(account).strip())
    return "\n".join(out)


def upsell_context(quote, policies, catalog, exclude=(), top=3):
    """무료배송까지 얼마가 모자란지, 무엇을 더 담으면 넘기는지 계산한다.

    후보는 반드시 실제 DB 행에서 뽑는다. 이걸 안 주면 LLM 이 없는 상품을 지어낸다.
    권유 문장 자체는 지침(UPSELL_FREE_SHIPPING, RECIPE_SUGGEST)에 따라 LLM 이 만든다."""
    if str(policies.get("UPSELL_FREE_SHIPPING", "허용")).strip() != "허용":
        return None

    # 담긴 것이 없으면 권할 근거가 없다. 무엇을 사려는지도 모르는데 상품을 들이미는 것은
    # 추천이 아니라 그냥 밀어내기다.
    if not quote["rows"] or not quote["subtotal"]:
        return None

    # 배송유형이 하나뿐이면 그 유형의 기준을 쓴다. 유형이 섞였으면 어느 쪽 기준으로
    # 권하는 것인지 말할 수 없으므로 지침의 공통 기준으로 둔다.
    threshold = policies.get_int("FREE_SHIPPING_THRESHOLD", 0)
    srows = quote.get("shipping_rows") or []
    if len(srows) == 1 and srows[0].get("threshold"):
        threshold = srows[0]["threshold"]
    if not threshold or quote["total"] is None or quote["shipping"] == 0:
        return None

    gap = threshold - quote["subtotal"]
    if gap <= 0:
        return None

    priced = [(c, catalog.price(c)) for c in catalog.items
              if catalog.price(c) and c not in exclude and not catalog.soldout(c)]
    # 하나만 더 담아도 기준을 넘기는 상품을 싼 순으로. 없으면 비싼 순으로 보여준다.
    over = sorted([x for x in priced if x[1] >= gap], key=lambda x: x[1])[:top]
    if not over:
        over = sorted(priced, key=lambda x: -x[1])[:top]

    return {
        "threshold": threshold, "gap": gap,
        "suggestions": [{"name": catalog.display(c), "price": p} for c, p in over],
    }
