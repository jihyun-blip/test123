# -*- coding: utf-8 -*-
"""
상품 매칭.

결정적 신호부터 순서대로 시도하고, 확정되면 이후 단계를 건너뛴다.
LLM 은 이해만 하고 매칭 확정은 코드가 한다.
"""
import difflib
import re

CONFIRMED = "확정"
AMBIGUOUS = "모호"
NOT_FOUND = "미발견"
CONFLICT = "충돌"


def normalize(s):
    """공백·대소문자·구분기호를 정리한다. 5단계 재시도에서만 쓴다."""
    return re.sub(r"[\s\-_·]", "", str(s or "")).lower()


class Catalog:
    """상품·유사어를 매칭에 쓰기 좋은 형태로 펼쳐둔다."""

    def __init__(self, master, country, synonyms):
        self.master = master
        self.country = country
        self.synonyms = synonyms

        self.items = {}          # item_code -> 병합된 행(마스터 + 국가별 전체 컬럼)
        for r in master.to_dict("records"):
            self.items[r["item_code"]] = dict(r)
        for r in country.to_dict("records"):
            self.items.setdefault(r["item_code"], {}).update(r)

        # 정식명 -> [item_code]
        self.by_canonical = {}
        for code, r in self.items.items():
            name = r.get("canonical_name") or r.get("display_name") or ""
            if name:
                self.by_canonical.setdefault(name, []).append(code)

        # 유사어 -> [item_code]
        self.by_synonym = {}
        for r in synonyms.to_dict("records"):
            self.by_synonym.setdefault(r["synonym"], []).append(r["item_code"])

        # 정규화 색인. 4단계까지 실패했을 때만 본다
        self.by_norm = {}
        for name, codes in list(self.by_canonical.items()) + list(self.by_synonym.items()):
            self.by_norm.setdefault(normalize(name), []).extend(codes)

    def display(self, code):
        r = self.items.get(code, {})
        return r.get("display_name") or r.get("canonical_name") or code

    def price(self, code):
        raw = str(self.items.get(code, {}).get("price", "")).replace(",", "").strip()
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return None

    def label_codes(self):
        return set(self.items.keys())

    def alternatives(self, code):
        """이 상품과 정식명·유사어를 공유하는 다른 상품들.

        정식명이 다른 상품의 유사어이기도 한 경우가 많고, 그럴 때 정식명 쪽으로
        확정하는 것이 정상 동작이다(거래명세서에 정식명을 넣기 때문).
        다만 고객이 "그 상품이 아니다"라고 하면 같은 표현을 공유하는 상품들을
        후보로 제시해 선택받아야 하므로, 그 후보 목록을 여기서 만든다."""
        r = self.items.get(code, {})
        exprs = {r.get("canonical_name"), r.get("display_name")}
        exprs |= {s for s, codes in self.by_synonym.items() if code in codes}

        out = []
        for e in exprs:
            if not e:
                continue
            for c in self.by_canonical.get(e, []) + self.by_synonym.get(e, []):
                if c != code and c not in out:
                    out.append(c)
        return out


class MatchResult:
    def __init__(self, status, code=None, candidates=None, rule=None, note=""):
        self.status = status
        self.code = code
        self.candidates = candidates or []
        self.rule = rule          # 어느 단계에서 결정됐는지. 결과 화면의 근거가 된다
        self.note = note

    def __repr__(self):
        return "<%s %s via %s>" % (self.status, self.code, self.rule)


def match(op, catalog, policies, mode="full"):
    """op 는 LLM 이 돌려준 item_ops 원소 하나."""
    hint = (op.get("name_hint") or op.get("raw_text") or "").strip()
    label = (op.get("label_code") or "").strip()
    printed = (op.get("printed_name") or "").strip()

    # 1. 라벨코드 — 유효 코드 목록에 존재하면 확정
    if label and label in catalog.label_codes():
        # 2. 인쇄 상품명이 함께 읽혔으면 대조한다
        if printed:
            printed_codes = catalog.by_canonical.get(printed) or catalog.by_synonym.get(printed) or []
            if printed_codes and label not in printed_codes:
                return MatchResult(
                    CONFLICT, label, printed_codes, "라벨코드-인쇄명 불일치",
                    "라벨 %s / 인쇄 '%s' → %s" % (label, printed, ", ".join(printed_codes)),
                )
        return MatchResult(CONFIRMED, label, rule="라벨코드")

    if not hint:
        return MatchResult(NOT_FOUND, rule="표현 없음")

    if mode == "reduced":
        return _match_reduced(hint, catalog)

    # 3. 정식 품목명 정확 일치
    canon = catalog.by_canonical.get(hint, [])
    if len(canon) == 1:
        if str(policies.get("EXACT_NAME_PRIORITY", "Y")).upper() == "Y":
            return MatchResult(CONFIRMED, canon[0], rule="정식명 정확일치")
        # N 이면 유사어까지 합쳐 모호 판단으로 넘긴다
        merged = sorted(set(canon + catalog.by_synonym.get(hint, [])))
        if len(merged) > 1:
            return MatchResult(AMBIGUOUS, candidates=merged, rule="정식명+유사어 중복")
        return MatchResult(CONFIRMED, canon[0], rule="정식명 정확일치")
    if len(canon) > 1:
        return MatchResult(AMBIGUOUS, candidates=sorted(canon), rule="정식명 중복")

    # 4. 유사어 정확 일치
    syn = catalog.by_synonym.get(hint, [])
    if len(syn) == 1:
        return MatchResult(CONFIRMED, syn[0], rule="유사어 정확일치")
    if len(syn) > 1:
        return MatchResult(AMBIGUOUS, candidates=sorted(set(syn)), rule="유사어 중복")

    # 5. 정규화 후 일치
    norm = catalog.by_norm.get(normalize(hint), [])
    norm = sorted(set(norm))
    if len(norm) == 1:
        return MatchResult(CONFIRMED, norm[0], rule="정규화 일치")
    if len(norm) > 1:
        return MatchResult(AMBIGUOUS, candidates=norm, rule="정규화 후 중복")

    # 6~7. 후보 없음
    return MatchResult(NOT_FOUND, rule="미발견")


def _match_reduced(hint, catalog, top_n=5):
    """축소 모드 — 외부 개발사가 실제로 갖게 될 수준의 재현.
    유사어 사전을 쓰지 않고 표시명 문자열 유사도 상위 N 개만 본다."""
    names = [(code, catalog.display(code)) for code in catalog.items]
    scored = sorted(
        ((difflib.SequenceMatcher(None, hint, name).ratio(), code) for code, name in names),
        reverse=True,
    )[:top_n]

    if not scored or scored[0][0] < 0.34:
        return MatchResult(NOT_FOUND, rule="유사도 미달(축소)",
                           note="최고 점수 %.2f" % (scored[0][0] if scored else 0))
    if scored[0][0] >= 0.9:
        return MatchResult(CONFIRMED, scored[0][1], rule="문자열 유사도(축소)")
    return MatchResult(AMBIGUOUS, candidates=[c for _, c in scored], rule="유사도 상위5(축소)",
                       note="최고 점수 %.2f" % scored[0][0])
