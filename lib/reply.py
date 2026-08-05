# -*- coding: utf-8 -*-
"""
결정적 응답 조립기.

거래명세서와 되물음 문장은 코드가 만든다. LLM 에게 맡기지 않는다.
금액을 LLM 이 문장으로 쓰는 순간 환각이 가능해지고, 그때부터 주문서를 믿을 수 없다.

LLM 이 맡는 것은 이 고정 블록 뒤에 붙는 확장(추가 구매 제안, 어조, 잡담 복귀)뿐이다.
그 확장은 지침 DB 가 결정한다.
"""
import difflib

from . import matching as M


def won(n):
    return "%s원" % f"{int(n):,}"


def _greeted(history):
    """이미 인사를 나눈 상태면 인사말을 생략한다."""
    return any("안녕" in (h.get("bot") or "") or "안녕" in (h.get("user") or "")
               for h in history)


def nearest(expr, catalog, top=3, floor=0.3):
    """DB 에 없는 표현에 대해 가장 가까운 상품을 고른다.
    후보를 지어내지 않는다. 반드시 실제 DB 행에서만 뽑는다."""
    scored = sorted(
        ((difflib.SequenceMatcher(None, expr, catalog.display(c)).ratio(), c)
         for c in catalog.items), reverse=True)
    return [c for r, c in scored[:top] if r >= floor]


def build(state, quote, catalog, policies, history):
    """반환값은 (고정 문장, 종류). 종류는 관찰·자동감지에서 쓴다.

    우선순위가 있다. 모호한 항목이 남아 있는데 거래명세서를 먼저 내밀면
    고객이 잘못된 상품으로 입금한다. 되물음이 항상 앞선다.
    """
    lines = state.lines

    # 1. 고객이 아니라고 한 품목 — 같은 표현을 공유하는 상품을 후보로 제시
    for l in lines:
        if l.rejected and l.alternatives:
            opts = " / ".join("%s %s" % (catalog.display(c), won(catalog.price(c) or 0))
                              for c in l.alternatives)
            return ("말씀하신 '%s'는 %s 중 어떤 것일까요?" % (l.key, opts), "reject_ask")

    # 2. 유사어가 여러 상품에 걸린 항목 — 임의 선택 금지
    if policies.get("AMBIGUOUS_ALIAS") == "되물음":
        for l in lines:
            if l.match and l.match.status == M.AMBIGUOUS:
                opts = " / ".join("%s %s" % (catalog.display(c), won(catalog.price(c) or 0))
                                  for c in l.match.candidates)
                return ("'%s'는 %s 중 어떤 것을 말씀하시는 걸까요?" % (l.key, opts), "ambiguous_ask")

    # 3. DB 에 없는 표현 — "못 찾았다"로 끝내지 않는다.
    #    가장 가까운 상품을 후보로 들이밀어 고객이 고르게 한다. 되물음은 대화를 끝내는 것이
    #    아니라 거래명세서를 완성하려고 부족한 정보를 채우는 과정이다.
    if policies.get("PRODUCT_NOT_FOUND") == "되물음":
        for l in lines:
            if l.match and l.match.status == M.NOT_FOUND:
                near = nearest(l.key, catalog)
                if near:
                    opts = " / ".join("%s %s" % (catalog.display(c), won(catalog.price(c) or 0))
                                      for c in near)
                    return ("'%s'는 이 중 어떤 것일까요? %s\n"
                            "이 중에 없으면 상품 사진을 보내주시면 찾아드릴게요."
                            % (l.key, opts), "notfound_ask")
                return ("'%s'가 어떤 상품인지 조금만 더 알려주시겠어요? 사진을 보내주셔도 좋아요."
                        % l.key, "notfound_ask")

    if not lines:
        return ("", "none")

    # 4. 수량을 말하지 않은 품목 — 거래명세서를 만들 수 없으므로 되묻는다
    no_qty = [l for l in lines if l.quantity is None]
    if no_qty:
        return ("%s는 몇 개 필요하신가요?"
                % ", ".join("'%s'" % l.key for l in no_qty), "quantity_ask")

    # 5. 단가 없는 항목이 있으면 합계를 확정하지 않는다
    if quote["blocked"]:
        miss = [r["표현"] for r in quote["rows"] if r["단가"] is None]
        return ("%s의 가격을 확인 중이에요. 확인되는 대로 총액을 안내드릴게요."
                % ", ".join(miss), "blocked")

    # 6. 필요한 정보가 다 모였다 — 거래명세서를 조립한다
    out = []
    if not _greeted(history):
        out.append("안녕하세요 고객님!")

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

    account = policies.get("ACCOUNT_INFO", "")
    out.append("총 %s을 아래 계좌로 입금주시면 감사하겠습니다." % won(quote["total"]))
    if account:
        out.append(account)

    return ("\n".join(out), "invoice")


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
