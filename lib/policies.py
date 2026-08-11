# -*- coding: utf-8 -*-
"""
지침 DB 파서.

bot_policies 는 구분 / 키 / 값 / 값유형 / 적용대상 / lang / 설명 의 키-값 테이블이고,
구분에 따라 성격이 달라 코드가 다르게 취급해야 한다.

  배송정책  사실   코드(견적 계산)   잠금
  결제정보  사실   프롬프트          잠금
  플래그    동작   코드(흐름 제어)   편집 가능 · 기록 필수
  응대규칙  전략   코드 + 프롬프트   편집 가능 · 기록 필수

lang 이 비면 전 언어 공통이고, 값이 있으면 그 언어에만 적용되며 같은 키의 공통 행을
덮어쓴다. 컬럼이 없는 예전 시트도 그대로 돈다(전부 공통으로 본다).
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
    "SHIPPING_MIX_RULE":       "code",
    "ACCOUNT_INFO":            "prompt",
    "EXACT_NAME_PRIORITY":     "code",
    "PERSONA":                 "prompt",
    "TONE":                    "prompt",
    "SMALLTALK":               "prompt",
    "RECIPE_SUGGEST":          "prompt",
    # 어투·호칭처럼 한국어 규칙으로는 정할 수 없어 언어별 행으로 두는 것들.
    # 코드가 판단에 쓰지 않고 프롬프트로만 보낸다
    "SPEECH_STYLE":            "prompt",
    "CUSTOMER_ADDRESS":        "prompt",
    "TONE_EXAMPLE":            "prompt",
    "UPSELL_FREE_SHIPPING":    "both",
    "UPSELL_MAX_TIMES":        "code",
    "SHOW_LINE_BASIS":         "both",
    "NO_PAYMENT_JUDGEMENT":    "both",
    "NO_REPEAT_QUESTION":      "both",
    "NO_PRODUCT_FACT_GUESS":   "both",
    "SMALLTALK_RETURN":        "both",
    # 프롬프트로만 가고 코드가 위반을 안 보던 두 개. 지키는지 확인할 방법이 없었다
    "ANSWER_CUSTOMER_QUESTION": "both",
    "NO_UNAVAILABLE_SUGGEST":   "both",
    "REQUIRED_FIELDS":         "code",
    "ASK_ADDRESS_DETAIL":      "code",
    "REQUEST_PAYMENT_PROOF":   "code",
    "ASK_RETRY_LIMIT":         "code",
    "AMBIGUOUS_MAX_OPTIONS":   "code",
    "AMBIGUOUS_ATTR_THRESHOLD": "code",
    "AMOUNT_MISMATCH_ENFORCE": "code",
}

# 플래그 값이 결정하는 흐름. 값을 바꾸면 대화 자체가 달라지므로
# 어떤 값으로 돌렸는지가 반드시 로그에 남아야 한다.
FLAG_ACTIONS = {"되물음", "차단", "미완료", "상담원연결", "검수필수"}

# 코드가 문자열로 직접 비교하는 값들. 번역하면 조건문이 통째로 빗나가는데
# 에러는 나지 않는다. 값유형이 "제어값" 이면 이 집합에 있는지 검사한다.
CONTROL_VALUES = FLAG_ACTIONS | {
    "허용", "금지", "필수", "권장", "생략",
    "Y", "N", "합산", "최대",
}
# REQUIRED_FIELDS 처럼 제어값을 쉼표로 나열하는 자리
CONTROL_TOKENS = {"수령인", "전화", "주소"}


def _lang_of(row):
    return str(row.get("lang") or "").strip().lower()


class Policies:
    def __init__(self, df, lang="ko"):
        self.df = df
        self.lang = str(lang or "").strip().lower()
        self.rows = df.to_dict("records") if hasattr(df, "to_dict") else list(df)

        # 이 언어에 적용될 행만 남긴 목록. 같은 키의 공통 행은 언어 행이 덮어쓴다
        self._map, order = {}, []
        for r in self.rows:
            key = r.get("키")
            if not key:
                continue
            lg = _lang_of(r)
            if lg and lg != self.lang:
                continue
            cur = self._map.get(key)
            if cur is None:
                order.append(key)
                self._map[key] = r
            elif lg and not _lang_of(cur):
                self._map[key] = r
        self.active = [self._map[k] for k in order]

    # ---------------------------------------------------------------- 조회
    def get(self, key, default=None):
        r = self._map.get(key)
        if r is None:
            return default
        return r.get("값", default)

    def get_int(self, key, default=0):
        try:
            return int(str(self.get(key, default)).replace(",", "").strip())
        except (TypeError, ValueError):
            return default

    def section(self, name):
        return [r for r in self.active if r.get("구분") == name]

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
        r = self._map.get(key)
        return bool(r) and r.get("구분") in LOCKED_SECTIONS

    @property
    def editable(self):
        """화면에서 편집을 허용할 행. 배송정책·결제정보는 제외한다."""
        return [r for r in self.rows if r.get("구분") not in LOCKED_SECTIONS]

    # ---------------------------------------------------------------- 조립
    def prompt_rules(self):
        """시스템 지시문으로 보낼 항목만 추린다."""
        out = []
        for r in self.active:
            key = r.get("키", "")
            if self.consumer_of(key) in ("prompt", "both"):
                out.append(r)
        return out

    def validate(self):
        """돌리기 전에 드러나야 할 문제를 모은다. 앱을 멈추지는 않는다."""
        warnings = []

        # 같은 키가 두 번 나오는 것은 언어가 다를 때만 정상이다
        seen = {}
        for r in self.rows:
            k = r.get("키", "")
            if not k:
                continue
            seen.setdefault(k, []).append(_lang_of(r))
        dup = [k for k, langs in seen.items() if len(langs) != len(set(langs))]
        if dup:
            warnings.append("키가 중복됩니다: %s" % ", ".join(sorted(dup)))

        for key, r in self.flags.items():
            val = str(r.get("값", "")).strip()
            if val not in FLAG_ACTIONS:
                warnings.append(
                    "플래그 %s 의 값 '%s' 이 흐름 제어에 쓰이지 않는 값입니다 (허용: %s)"
                    % (key, val, ", ".join(sorted(FLAG_ACTIONS)))
                )

        # 제어값을 번역하면 코드의 문자열 비교가 통째로 빗나간다. 에러는 안 난다.
        for r in self.rows:
            if str(r.get("값유형", "")).strip() != "제어값":
                continue
            val = str(r.get("값", "")).strip()
            if val in CONTROL_VALUES:
                continue
            tokens = [t.strip() for t in val.split(",") if t.strip()]
            if tokens and all(t in CONTROL_TOKENS for t in tokens):
                continue
            warnings.append(
                "%s 의 값 '%s' 은 값유형이 제어값인데 코드가 아는 값이 아닙니다. "
                "제어값은 번역하면 안 됩니다 (허용: %s)"
                % (r.get("키", ""), val, ", ".join(sorted(CONTROL_VALUES)))
            )

        # 코드가 올리는 플래그인데 시트에 행이 없으면, 그 플래그는 영원히 안 뜬다.
        # add() 가 조용히 넘어가기 때문에 에러도 경고도 나지 않는다.
        # 시트에서 행 하나를 지웠을 때 무엇이 죽는지 여기서 드러나야 한다.
        from . import flags as FL       # 모듈 로딩 순서에 얽매이지 않게 함수 안에서 부른다
        absent = sorted(k for k in FL.CODE_FLAGS if k not in self.flags)
        if absent:
            warnings.append(
                "코드가 올리는 플래그인데 시트에 행이 없습니다. 그 플래그는 뜨지 않습니다: %s"
                % ", ".join(absent))

        # 반대쪽. 시트에만 있고 코드가 안 올리는 플래그는 체크리스트만 길어진다
        unused = sorted(k for k in self.flags if k not in FL.CODE_FLAGS)
        if unused:
            warnings.append(
                "시트에 있으나 코드가 올리지 않는 플래그입니다. 검수 목록에만 남습니다: %s"
                % ", ".join(unused))

        for key in ("FREE_SHIPPING_THRESHOLD", "SHIPPING_FEE"):
            if self.get(key) is None:
                warnings.append("견적 계산에 필요한 %s 행이 없습니다" % key)

        unmapped = [
            r.get("키") for r in self.active
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
            lg = _lang_of(r)
            rows.append({
                "구분": r.get("구분", ""),
                "키": key,
                "값": r.get("값", ""),
                "값유형": r.get("값유형", ""),
                "lang": lg or "공통",
                "적용": "○" if (self._map.get(key) is r) else "",
                "소비처": {"code": "코드", "prompt": "프롬프트", "both": "코드+프롬프트"}[
                    self.consumer_of(key)
                ],
                "편집": "잠금" if self.is_locked(key) else "가능",
                "적용대상": r.get("적용대상", ""),
            })
        return pd.DataFrame(rows)
