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


class Flag:
    def __init__(self, key, value, evidence):
        self.key = key
        self.value = value          # 되물음 / 차단 / 미완료 / 상담원연결 / 검수필수
        self.evidence = evidence    # 왜 떴는지. 결과 화면에 그대로 표시된다

    def as_row(self):
        return {"플래그": self.key, "값": self.value, "근거": self.evidence}


def _val(policies, key, default="미완료"):
    return policies.get(key, default)


def evaluate(state, quote, catalog, policies, out, mode):
    """이번 턴 기준으로 떠야 할 플래그를 전부 모은다."""
    flags = []
    known = policies.flags

    def add(key, evidence):
        if key in known:
            flags.append(Flag(key, known[key].get("값", ""), evidence))

    # ---------------------------------------------------------- 품목
    for line in state.lines:
        m = line.match
        if not m:
            continue
        if line.rejected:
            alts = ", ".join("%s(%s원)" % (catalog.display(c), catalog.price(c) or "가격없음")
                             for c in line.alternatives) or "대체 후보 없음"
            add("ITEM_REJECTED",
                "고객이 '%s' → %s 이(가) 아니라고 함. 후보: %s"
                % (line.key, catalog.display(m.code), alts))
            continue

        if m.status == M.AMBIGUOUS:
            names = ", ".join("%s(%s원)" % (catalog.display(c), catalog.price(c) or "가격없음")
                              for c in m.candidates)
            add("AMBIGUOUS_ALIAS", "'%s' 이(가) %d개 상품에 걸림 → %s" % (line.key, len(m.candidates), names))
        elif m.status == M.NOT_FOUND:
            add("PRODUCT_NOT_FOUND", "'%s' 을(를) DB에서 찾지 못함 (%s)" % (line.key, m.rule))
        elif m.status == M.CONFLICT:
            add("PRODUCT_SIGNAL_CONFLICT", m.note)

    if quote["blocked"]:
        missing = [r["표현"] for r in quote["rows"] if r["단가"] is None]
        add("MISSING_PRICE", "단가 없는 항목: %s → 합계 확정 차단" % ", ".join(missing))

    # ---------------------------------------------------------- 수령 정보
    if not state.receiver:
        add("RECEIVER_MISSING", "수령인 이름 미확보")

    if state.phone:
        digits = re.sub(r"\D", "", str(state.phone.value))
        if not (10 <= len(digits) <= 11):
            add("PHONE_INVALID", "'%s' → 숫자 %d자리 (10~11자리 아님)" % (state.phone.value, len(digits)))

        # 형식이 유효해도 잘못 읽혔을 수 있다. 2차 판독과 대조한다.
        second = state.phone_second
        if second and re.sub(r"\D", "", str(second)) != digits:
            add("PHONE_MISMATCH", "1차 '%s' vs 2차 '%s' — 이미지 판독 결과 불일치"
                % (state.phone.value, second))

    if not state.address_base:
        add("ADDRESS_MISSING", "주소 자체가 없음")
    else:
        if not state.address_detail:
            add("ADDRESS_DETAIL_MISSING", "기본주소는 있으나 상세주소(동·호)가 없음")
        if state.address_base.source == "image":
            add("ADDRESS_IMAGE", "주소를 %s 에서 추출 — 육안 확인 필요" % (state.address_base.source_ref or "이미지"))

        api = state.addr_api or {}
        if api.get("done"):
            if api.get("total", 0) == 0:
                add("ADDRESS_UNVERIFIED", "'%s' 검색 결과 0건 — 우편번호 추출 실패" % api.get("clean", ""))
            elif len(api.get("zips") or []) > 1:
                add("ADDRESS_AMBIGUOUS",
                    "검색 %d건인데 우편번호가 %d종류 (%s) — 주소 불완전 가능성"
                    % (api["total"], len(api["zips"]), ", ".join(api["zips"])))

    # ---------------------------------------------------------- 대화 신호
    if out.get("handoff_request"):
        add("HANDOFF_REQUEST", "고객이 상담원 연결을 명시적으로 요청")
    if out.get("angry"):
        add("ANGRY_CUSTOMER", "고객 불만·화남 감지")
    if out.get("intent") == "payment_claim":
        add("PAYMENT_UNCONFIRMED", "고객이 입금을 주장 — 상담원이 은행에서 직접 확인 필요")

    return flags


# ------------------------------------------------------------------ 자동 감지
def detect(bot_text, state, quote, policies, out, prev_asked):
    """사람이 눈으로 보면 놓치는 것을 코드가 잡는다. (설계서 11장)"""
    hits = []

    def hit(name, rule, detail):
        hits.append({"감지": name, "근거 규칙": rule, "내용": detail})

    # 금액 환각 — 응답 문장의 숫자를 계산값과 대조
    if quote["total"] is not None:
        nums = {int(n.replace(",", "")) for n in re.findall(r"[\d,]{3,}", bot_text or "")}
        legit = {quote["total"], quote["subtotal"], quote["shipping"]}
        legit |= {r["단가"] for r in quote["rows"] if r["단가"]}
        legit |= {r["소계"] for r in quote["rows"] if r["소계"]}
        bogus = {n for n in nums if n >= 100 and n not in legit}
        if bogus:
            hit("금액 환각", "SHOW_LINE_BASIS",
                "응답의 %s 이(가) 계산값과 불일치 → AMOUNT_MISMATCH" % ", ".join(map(str, sorted(bogus))))

    # 상태 유실 — 이미 확보한 정보를 다시 물으면 표시
    have = {"수령": bool(state.receiver), "전화": bool(state.phone), "주소": bool(state.address_base)}
    for word, ok in have.items():
        if ok and re.search(word + r"[^.?!]{0,20}(알려|뭐|무엇|어떻게|어디)", bot_text or ""):
            hit("상태 유실", "NO_REPEAT_QUESTION", "이미 확보한 '%s' 정보를 다시 물음" % word)

    # 스키마 결핍 — 답할 수 없었던 질문
    for mi in out.get("missing_info") or []:
        if not mi.get("found", False):
            hit("스키마 결핍", "NO_PRODUCT_FACT_GUESS",
                "'%s' → 필요한 정보: %s" % (mi.get("asked", ""), mi.get("needed", "")))

    # 입금 단정
    if re.search(r"입금(이)?\s*(확인|완료)", bot_text or ""):
        hit("입금 단정", "NO_PAYMENT_JUDGEMENT", "응답에 입금 확인 취지 문구가 있음")

    # 되물음 누락 — 모호 항목이 있는데 후보 제시 없이 진행
    ambiguous = [l for l in state.lines if l.match and l.match.status == M.AMBIGUOUS]
    if ambiguous and _val(policies, "AMBIGUOUS_ALIAS") == "되물음":
        if "?" not in (bot_text or ""):
            hit("되물음 누락", "AMBIGUOUS_ALIAS = 되물음",
                "모호 항목 %s 이(가) 있는데 후보 제시 없이 진행" % ", ".join(l.key for l in ambiguous))

    # 미확인 통과 — DB에 없는 표현을 무시하고 진행
    notfound = [l for l in state.lines if l.match and l.match.status == M.NOT_FOUND]
    if notfound and "?" not in (bot_text or ""):
        hit("미확인 통과", "PRODUCT_NOT_FOUND = 되물음",
            "DB에 없는 표현 %s 을(를) 확인 없이 진행" % ", ".join(l.key for l in notfound))

    # 잡담 미복귀
    if out.get("intent") == "smalltalk" and prev_asked and "?" not in (bot_text or ""):
        hit("잡담 미복귀", "SMALLTALK_RETURN", "잡담·추천 후 진행 중이던 되물음으로 복귀하지 않음")

    # 추출 실패 — 명백한 주문 발화인데 item_ops 가 비어 있음
    if out.get("intent") == "order" and not (out.get("item_ops") or []):
        hit("추출 실패", "—", "주문 의도로 분류됐는데 item_ops 가 비어 있음")

    return hits
