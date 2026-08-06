# -*- coding: utf-8 -*-
"""상담원 인계 메모.

플래그만 던져주면 상담원은 그게 무슨 뜻인지, 무엇을 해야 하는지를 다시 찾아야 한다.
여기서는 "확인할 것"과 "할 일"을 문장으로 만들어 넘긴다.

지어내지 않는다. 상태·견적·플래그에 이미 있는 사실만 옮긴다.
쓸 말이 없으면 빈 목록을 돌려준다. 없는 걱정을 만들지 않는다.
"""
from . import matching as M
from . import reply as R


def build(state, quote, catalog, policies, history):
    """[(구분, 문장), ...] 을 돌려준다. 구분은 화면에서 묶어 보여주기 위한 것이다."""
    items = []

    def add(kind, text):
        items.append((kind, text))

    # ---------------------------------------------------------- 확인 필요
    for line in state.lines:
        if line.unavailable:
            add("고객 요청", "%s 찾으셨으나 취급하지 않아 주문에서 뺐습니다. "
                            "취급 예정이면 안내가 필요합니다."
                            % R.eul("'%s'" % R.spoken(line.key)))
            continue
        m = line.match
        if m and m.status == M.AMBIGUOUS:
            names = ", ".join(catalog.display(c) for c in m.candidates)
            add("확인 필요", "%s %s 중 무엇인지 확정되지 않았습니다."
                            % (R.i_ga("'%s'" % R.spoken(line.key)), names))
        elif m and m.status == M.NOT_FOUND:
            add("확인 필요", "%s 상품 DB 에서 찾지 못했습니다."
                            % R.eul("'%s'" % R.spoken(line.key)))
        if line.unit_note:
            add("확인 필요", "%s — 요청량과 실제 발송량이 다를 수 있습니다."
                            % line.unit_note)

    if quote["blocked"]:
        miss = ", ".join(r["표현"] for r in quote["rows"] if r["단가"] is None)
        add("확인 필요", "%s 의 단가가 없어 총액을 확정하지 못했습니다." % miss)

    # ---------------------------------------------------------- 미확보 정보
    for label in R.given_up(state, policies):
        add("미확보", "%s 여러 번 여쭈었으나 받지 못했습니다. 직접 확인이 필요합니다."
                     % R.eul(label))

    for key, label in R.missing_required(state, policies):
        add("미확보", "%s 아직 비어 있습니다." % R.i_ga(label))

    if state.address_base and not state.address_detail:
        add("미확보", "상세주소(동·호)가 없습니다.")

    # ---------------------------------------------------------- 육안 확인
    for label, field in (("수령인", state.receiver), ("연락처", state.phone),
                         ("주소", state.address_base)):
        if field and getattr(field, "source", "") == "image":
            add("육안 확인", "%s 사진에서 읽었습니다(%s). 원본과 대조해주세요."
                            % (R.eul(label), getattr(field, "source_ref", "") or "이미지"))

    api = state.addr_api or {}
    if api.get("done"):
        if api.get("total", 0) == 0:
            add("육안 확인", "주소 검색 결과가 0건이라 우편번호를 얻지 못했습니다.")
        elif len(api.get("zips") or []) > 1:
            add("육안 확인", "주소 검색에서 우편번호가 %d종류 나왔습니다(%s). 주소가 불완전할 수 있습니다."
                            % (len(api["zips"]), ", ".join(api["zips"])))

    # ---------------------------------------------------------- 입금
    if state.payment_proof:
        add("입금", "입금증 이미지를 받았습니다(%s). 은행 내역과 대조해주세요." % state.payment_proof)
    elif quote["total"] is not None:
        add("입금", "입금 확인이 되지 않았습니다. 은행 내역에서 직접 확인해주세요.")

    # ---------------------------------------------------------- 대화 신호
    for h in history or []:
        out = h.get("out") or {}
        if out.get("handoff_request"):
            add("대화", "%d번째 턴에서 상담원 연결을 요청하셨습니다." % h["turn"])
        if out.get("angry"):
            add("대화", "%d번째 턴에서 불만이 감지되었습니다." % h["turn"])

    # 같은 문장이 두 번 들어가는 일이 있다. 순서는 유지하고 중복만 지운다.
    seen, uniq = set(), []
    for kind, text in items:
        if text not in seen:
            seen.add(text)
            uniq.append((kind, text))
    return uniq


def as_text(items):
    """시트에 한 칸으로 남길 형태."""
    return "\n".join("[%s] %s" % (kind, text) for kind, text in items)
