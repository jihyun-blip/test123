# -*- coding: utf-8 -*-
"""상담원 인계 메모.

플래그만 던져주면 상담원은 그게 무슨 뜻인지, 무엇을 해야 하는지를 다시 찾아야 한다.
여기서는 "확인할 것"과 "할 일"을 문장으로 만들어 넘긴다.

지어내지 않는다. 상태·견적·플래그에 이미 있는 사실만 옮긴다.
쓸 말이 없으면 빈 목록을 돌려준다. 없는 걱정을 만들지 않는다.

문장은 판정 탭에서 테스터가 읽으므로 화면 언어를 따른다.
"""
from . import matching as M
from . import messages as MSG
from . import reply as R


def build(state, quote, catalog, policies, history):
    """[(구분, 문장), ...] 을 돌려준다. 구분은 화면에서 묶어 보여주기 위한 것이다.

    고객이 아니라 사람이 처리하려고 읽는 문장이고 notes 탭에 쌓이므로,
    세션 언어가 아니라 개발자 언어를 따른다."""
    T = MSG.for_dev()
    items = []

    def add(kind_key, text):
        items.append((T.t(kind_key), text))

    def q(expr):
        return T.quote_word(MSG.strip_code(expr, MSG.DEV_LANG))

    # ---------------------------------------------------------- 확인 필요
    for line in state.lines:
        if line.unavailable:
            if line.drop_reason == "soldout":
                add("ho_request", T.t("ho_drop_soldout", T.eun(q(line.key))))
            else:
                add("ho_request", T.t("ho_drop_notfound", T.eul(q(line.key))))
            continue
        m = line.match
        if line.soldout_alts:
            add("ho_check", T.t("ho_soldout", T.eun(q(line.key)),
                                ", ".join(catalog.display(c) for c in line.soldout_alts)))
        if m and m.status == M.AMBIGUOUS:
            names = ", ".join(catalog.display(c) for c in m.candidates)
            add("ho_check", T.t("ho_ambiguous", T.i_ga(q(line.key)), names))
        elif m and m.status == M.NOT_FOUND:
            add("ho_check", T.t("ho_notfound", T.eul(q(line.key))))

    if quote["blocked"]:
        miss = ", ".join(r["표현"] for r in quote["rows"] if r["단가"] is None)
        add("ho_check", T.t("ho_price", miss))

    # ---------------------------------------------------------- 미확보 정보
    for label in R.given_up(state, policies, T):
        add("ho_missing", T.t("ho_gaveup", T.eul(label)))

    for key, label in R.missing_required(state, policies, T):
        add("ho_missing", T.t("ho_empty", T.i_ga(label)))

    if state.address_base and not state.address_detail:
        add("ho_missing", T.t("ho_detail"))

    # ---------------------------------------------------------- 육안 확인
    for key, field in (("got_receiver", state.receiver), ("got_phone", state.phone),
                       ("got_address", state.address_base)):
        if field and getattr(field, "source", "") == "image":
            add("ho_eye", T.t("ho_image", T.eul(T.t(key)),
                              getattr(field, "source_ref", "") or "image"))

    api = state.addr_api or {}
    if api.get("done"):
        if api.get("total", 0) == 0:
            add("ho_eye", T.t("ho_zip_none"))
        elif len(api.get("zips") or []) > 1:
            add("ho_eye", T.t("ho_zip_many", len(api["zips"]), ", ".join(api["zips"])))

    # ---------------------------------------------------------- 입금
    if state.payment_proof:
        add("ho_payment", T.t("ho_proof", state.payment_proof))
    elif quote["total"] is not None:
        add("ho_payment", T.t("ho_no_proof"))

    # ---------------------------------------------------------- 대화 신호
    for h in history or []:
        out = h.get("out") or {}
        if out.get("handoff_request"):
            add("ho_talk", T.t("ho_handoff_req", h["turn"]))
        if out.get("angry"):
            add("ho_talk", T.t("ho_angry", h["turn"]))

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
