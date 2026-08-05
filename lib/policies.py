# -*- coding: utf-8 -*-
"""
지침 DB 파서.

bot_policies 는 구분 / 키 / 값 / 적용대상 / 설명 의 키-값 테이블이고,
구분에 따라 성격이 달라 코드가 다르게 취급해야 한다.

  배송정책  사실   코드(견적 계산)   잠금
  결제정보  사실   프롬프트          잠금
  플래그    동작   코드(흐름 제어)   편집 가능 · 기록 필수
  응대규칙  전략   코드 + 프롬프트   편집 가능 · 기록 필수
"""
import pandas as pd

# 실험 변수가 아니라 고정값이다. 두 테스터가 이를 바꾸면 견적 결과를 비교할 수 없게 된다.
LOCKED_SECTIONS = {"배송정책", "결제정보"}

# 각 키를 어디로 보낼지 명시한다. 응대규칙은 프롬프트와 코드에 섞여 있어
# 매핑을 코드에 적어두지 않으면 구현 중에 흐트러진다.
#   prompt : 시스템 지시문으로 조립
#   code   : 매칭·계산·흐름 로직이 읽음
#   both   : 프롬프트로 보내고, 코드가 응답을 검사해 위반을 잡음
CONSUMER = {
    "FREE_SHIPPING_THRESHOLD": "code",
    "SHIPPING_FEE":            "code",
    "ACCOUNT_INFO":            "prompt",
    "EXACT_NAME_PRIORITY":     "code",
    "PERSONA":                 "prompt",
    "TONE":                    "prompt",
    "SMALLTALK":               "prompt",
    "RECIPE_SUGGEST":          "prompt",
    "UPSELL_FREE_SHIPPING":    "both",
    "UPSELL_MAX_TIMES":        "code",
    "SHOW_LINE_BASIS":         "both",
    "NO_PAYMENT_JUDGEMENT":    "both",
    "NO_REPEAT_QUESTION":      "both",
    "NO_PRODUCT_FACT_GUESS":   "both",
    "SMALLTALK_RETURN":        "both",
    "REQUIRED_FIELDS":         "code",
    "ASK_ADDRESS_DETAIL":      "code",
    "REQUEST_PAYMENT_PROOF":   "code",
}

# 플래그 값이 결정하는 흐름. 값을 바꾸면 대화 자체가 달라지므로
# 어떤 값으로 돌렸는지가 반드시 로그에 남아야 한다.
FLAG_ACTIONS = {"되물음", "차단", "미완료", "상담원연결", "검수필수"}


class Policies:
    def __init__(self, df):
        self.df = df
        self.rows = df.to_dict("records")

    # ---------------------------------------------------------------- 조회
    def get(self, key, default=None):
        for r in self.rows:
            if r.get("키") == key:
                return r.get("값", default)
        return default

    def get_int(self, key, default=0):
        try:
            return int(str(self.get(key, default)).replace(",", "").strip())
        except (TypeError, ValueError):
            return default

    def section(self, name):
        return [r for r in self.rows if r.get("구분") == name]

    # ---------------------------------------------------------------- 분류
    @property
    def flags(self):
        """플래그 행. 결과 화면의 체크리스트가 여기서 자동 생성된다.
        시트에 플래그를 추가하면 체크리스트도 자동으로 늘어난다."""
        return {r["키"]: r for r in self.section("플래그")}

    def consumer_of(self, key):
        """플래그는 전부 코드가 읽는다. 나머지는 매핑 표를 따른다."""
        if key in self.flags:
            return "code"
        return CONSUMER.get(key, "prompt")

    def is_locked(self, key):
        for r in self.rows:
            if r.get("키") == key:
                return r.get("구분") in LOCKED_SECTIONS
        return False

    @property
    def editable(self):
        """화면에서 편집을 허용할 행. 배송정책·결제정보는 제외한다."""
        return [r for r in self.rows if r.get("구분") not in LOCKED_SECTIONS]

    # ---------------------------------------------------------------- 조립
    def prompt_rules(self):
        """시스템 지시문으로 보낼 항목만 추린다."""
        out = []
        for r in self.rows:
            key = r.get("키", "")
            if self.consumer_of(key) in ("prompt", "both"):
                out.append(r)
        return out

    def validate(self):
        """돌리기 전에 드러나야 할 문제를 모은다. 앱을 멈추지는 않는다."""
        warnings = []

        keys = [r.get("키", "") for r in self.rows]
        dup = {k for k in keys if k and keys.count(k) > 1}
        if dup:
            warnings.append("키가 중복됩니다: %s" % ", ".join(sorted(dup)))

        for key, r in self.flags.items():
            val = str(r.get("값", "")).strip()
            if val not in FLAG_ACTIONS:
                warnings.append(
                    "플래그 %s 의 값 '%s' 이 흐름 제어에 쓰이지 않는 값입니다 (허용: %s)"
                    % (key, val, ", ".join(sorted(FLAG_ACTIONS)))
                )

        for key in ("FREE_SHIPPING_THRESHOLD", "SHIPPING_FEE"):
            if self.get(key) is None:
                warnings.append("견적 계산에 필요한 %s 행이 없습니다" % key)

        unmapped = [
            r.get("키") for r in self.rows
            if r.get("구분") == "응대규칙" and r.get("키") not in CONSUMER
        ]
        if unmapped:
            warnings.append(
                "매핑 표에 없는 응대규칙입니다. 프롬프트로 보냅니다: %s" % ", ".join(unmapped)
            )

        return warnings

    def summary(self):
        """화면 표시용. 각 행이 어디로 가는지, 잠겼는지를 한 표로 보여준다."""
        rows = []
        for r in self.rows:
            key = r.get("키", "")
            rows.append({
                "구분": r.get("구분", ""),
                "키": key,
                "값": r.get("값", ""),
                "소비처": {"code": "코드", "prompt": "프롬프트", "both": "코드+프롬프트"}[
                    self.consumer_of(key)
                ],
                "편집": "잠금" if self.is_locked(key) else "가능",
                "적용대상": r.get("적용대상", ""),
            })
        return pd.DataFrame(rows)
