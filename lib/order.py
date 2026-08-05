# -*- coding: utf-8 -*-
"""
주문 상태 누적과 견적 계산.

누적은 코드가 담당한다. LLM 은 매 턴 "이번 턴에서 파악한 변경"만 돌려주고,
이전까지 쌓인 상태는 여기가 보관한다. LLM 응답으로 상태 전체를 덮어쓰지 않는다.
대화가 길어질수록 모델이 앞 항목을 빠뜨릴 확률이 올라가고,
그때마다 주문이 조용히 훼손되기 때문이다.

빈 값과 미언급을 구분한다. LLM 출력의 null 은 "이번 턴에 언급 없음"이며
코드는 기존 값을 유지한다. 빈 문자열을 미언급의 표현으로 쓰지 않는다.
"""
import copy

from . import matching as M


class Field:
    """값 하나와 그 근거를 함께 들고 다닌다."""

    def __init__(self, value=None, source=None, source_ref=None, turn=None):
        self.value = value
        self.source = source
        self.source_ref = source_ref
        self.turn = turn

    def apply(self, payload, turn):
        """payload 가 None 이면 미언급이므로 기존 값을 유지한다."""
        if payload is None:
            return
        if isinstance(payload, dict):
            v = payload.get("value")
            if v is None:
                return
            self.value = v
            self.source = payload.get("source")
            self.source_ref = payload.get("source_ref")
        else:
            self.value = payload
        self.turn = turn

    @property
    def origin(self):
        if self.value is None:
            return ""
        if self.source == "image":
            return "%s턴 · %s" % (self.turn, self.source_ref or "이미지")
        return "%s턴 · 텍스트" % self.turn

    def __bool__(self):
        return bool(self.value)


class Line:
    """주문 품목 한 줄. 고객 표현과 매칭 결과를 분리해서 들고 있는다.
    표현을 정확히 뽑았는데 엉뚱한 상품에 붙는 경우와,
    표현을 잘못 읽었는데 우연히 맞는 상품으로 가는 경우는 대응이 다르다."""

    def __init__(self, raw_text, name_hint, quantity, unit_expr, source, source_ref, turn):
        self.raw_text = raw_text
        self.name_hint = name_hint
        self.quantity = quantity or 1
        self.unit_expr = unit_expr or ""
        self.source = source
        self.source_ref = source_ref
        self.turn = turn
        self.match = None       # MatchResult
        self.rejected = False   # 고객이 이 품목이 아니라고 함
        self.chosen = None      # 후보 중 고객이 고른 item_code
        self.alternatives = []  # 되물을 대체 후보

    @property
    def key(self):
        return (self.name_hint or self.raw_text or "").strip()

    @property
    def origin(self):
        if self.source == "image":
            return "%s턴 · %s" % (self.turn, self.source_ref or "이미지")
        return "%s턴 · 텍스트" % self.turn


class OrderState:
    def __init__(self):
        self.lines = []
        self.receiver = Field()
        self.phone = Field()
        self.address_base = Field()
        self.address_detail = Field()
        self.zipno = None
        self.road_addr = None
        self.addr_api = {}       # 정제 전/후, 응답 요약. 관찰 패널이 그대로 보여준다

    # ---------------------------------------------------------------- 누적
    def apply(self, out, turn):
        """LLM 출력 한 턴치를 누적 상태에 반영한다. 반환값은 변화 요약."""
        before = self.snapshot()

        for op in out.get("item_ops") or []:
            self._apply_op(op, turn)

        self.receiver.apply(out.get("receiver"), turn)
        self.phone.apply(out.get("phone"), turn)

        addr = out.get("address")
        if addr:
            # base 와 detail 은 반드시 분리해서 다룬다.
            # 주소 검색 API 는 상세주소가 붙은 문자열을 통째로 던지면 실패율이 올라간다.
            if addr.get("base"):
                self.address_base.apply(
                    {"value": addr["base"], "source": addr.get("source"),
                     "source_ref": addr.get("source_ref")}, turn)
            if addr.get("detail"):
                self.address_detail.apply(
                    {"value": addr["detail"], "source": addr.get("source"),
                     "source_ref": addr.get("source_ref")}, turn)

        return self.diff(before)

    def _apply_op(self, op, turn):
        act = (op.get("op") or "add").lower()
        hint = (op.get("name_hint") or op.get("raw_text") or "").strip()

        if act == "remove":
            self.lines = [l for l in self.lines if l.key != hint]
            return

        existing = next((l for l in self.lines if l.key == hint), None)

        # 고객이 "그 상품이 아니다" — 확정을 풀고 대체 후보를 받을 상태로 둔다
        if act == "reject" and existing:
            existing.rejected = True
            existing.chosen = None
            return

        # 제시한 후보 중 고객이 하나를 고름 — 그 상품으로 확정 교체
        if act == "choose" and existing:
            code = (op.get("chosen_code") or "").strip()
            if code:
                existing.chosen = code
                existing.rejected = False
            return

        if act == "update" and existing:
            if op.get("quantity") is not None:
                existing.quantity = op["quantity"]
            if op.get("unit_expr"):
                existing.unit_expr = op["unit_expr"]
            return

        if existing:
            # 같은 표현이 다시 나오면 수량을 더한다
            existing.quantity += op.get("quantity") or 1
            return

        self.lines.append(Line(
            raw_text=op.get("raw_text", ""),
            name_hint=hint,
            quantity=op.get("quantity"),
            unit_expr=op.get("unit_expr"),
            source=op.get("source", "text"),
            source_ref=op.get("source_ref"),
            turn=turn,
        ))

    # ---------------------------------------------------------------- 매칭
    def rematch(self, catalog, policies, mode):
        for line in self.lines:
            # 고객이 직접 고른 상품이 있으면 매칭 로직보다 우선한다
            if line.chosen:
                line.match = M.MatchResult(M.CONFIRMED, line.chosen, rule="고객 선택")
                line.alternatives = []
                continue

            line.match = M.match(
                {"name_hint": line.name_hint, "raw_text": line.raw_text}, catalog, policies, mode)

            # 확정됐지만 고객이 아니라고 한 건은, 같은 표현을 공유하는 상품을 후보로 제시한다
            if line.rejected and line.match.code:
                line.alternatives = catalog.alternatives(line.match.code)
            else:
                line.alternatives = []

    # ---------------------------------------------------------------- 견적
    def quote(self, catalog, policies):
        """수량 × 단가로 소계, 무료배송 기준 초과 시 배송비 0원,
        단가 없는 항목이 있으면 합계 확정을 차단한다."""
        rows, subtotal, blocked = [], 0, False

        for line in self.lines:
            confirmed = line.match and line.match.status == M.CONFIRMED and not line.rejected
            code = line.match.code if confirmed else None
            unit = catalog.price(code) if code else None
            amount = unit * line.quantity if unit is not None else None
            if amount is None:
                blocked = True
            else:
                subtotal += amount
            rows.append({
                "표현": line.key,
                "매칭": catalog.display(code) if code else (line.match.status if line.match else "-"),
                "수량": line.quantity,
                "단가": unit,
                "소계": amount,
                "근거": line.origin,
            })

        threshold = policies.get_int("FREE_SHIPPING_THRESHOLD", 0)
        fee = policies.get_int("SHIPPING_FEE", 0)
        shipping = 0 if (threshold and subtotal >= threshold) else fee

        return {
            "rows": rows,
            "subtotal": subtotal,
            "shipping": shipping,
            "total": None if blocked else subtotal + shipping,
            "blocked": blocked,
        }

    # ---------------------------------------------------------------- 관찰
    def snapshot(self):
        return copy.deepcopy({
            "lines": [(l.key, l.quantity) for l in self.lines],
            "receiver": self.receiver.value,
            "phone": self.phone.value,
            "address_base": self.address_base.value,
            "address_detail": self.address_detail.value,
        })

    def diff(self, before):
        after = self.snapshot()
        changes = []

        b, a = dict(before["lines"]), dict(after["lines"])
        for k in a:
            if k not in b:
                changes.append("+ 품목 %s ×%s" % (k, a[k]))
            elif a[k] != b[k]:
                changes.append("~ 품목 %s ×%s → ×%s" % (k, b[k], a[k]))
        for k in b:
            if k not in a:
                changes.append("- 품목 %s" % k)

        for f in ("receiver", "phone", "address_base", "address_detail"):
            if before[f] != after[f]:
                changes.append("~ %s: %s → %s" % (f, before[f] or "(없음)", after[f]))

        return changes
