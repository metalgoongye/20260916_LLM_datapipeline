"""표준 스키마 계약. references/schema.md 를 코드로 옮긴 것입니다.

    source | indicator_code | region_code | period | value | unit | vintage |
    retrieved_at | source_url | missing_reason

핵심 규칙은 하나입니다. **값이 비어 있으면 결측 사유가 반드시 있어야 하고,
없으면 객체 생성 자체가 실패합니다.** 나중에 검사하는 것이 아니라 만들어질 때
막습니다. 검증 단계까지 흘러간 빈칸은 이미 사유를 복원할 수 없기 때문입니다.

    >>> Row(source="WORLDBANK", indicator_code="SP.DYN.TFRT.IN", region_code="KOR",
    ...     period=2019, value=None, unit="명", vintage="연간",
    ...     source_url="https://api.worldbank.org/v2/")
    Traceback (most recent call last):
    SchemaError: value 가 비어 있으면 missing_reason 이 필요합니다 ...
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

COLUMNS = ["source", "indicator_code", "region_code", "period", "value", "unit",
           "vintage", "retrieved_at", "source_url", "missing_reason"]

MISSING_REASONS = {
    "NA_NOTSURVEYED": "미조사",      # 보간 검토 가능
    "NA_NOTAPPLICABLE": "해당없음",   # 보간 금지
    "NA_CONFIDENTIAL": "비공개",      # 값은 존재. 다른 경로 검토
}
VINTAGES = {"확정", "잠정", "추계", "연간"}

REGION_KR = re.compile(r"^KR-\d{2}$")      # 국내: KR- + 행정구역코드 2자리
REGION_ISO3 = re.compile(r"^[A-Z]{3}$")    # 국외: ISO 3166-1 alpha-3
PERIOD_MIN, PERIOD_MAX = 1900, 2100


class SchemaError(ValueError):
    """표준 스키마 위반. 이 예외가 나면 행을 만들지 않습니다."""


def _now():
    return datetime.now(KST).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Row:
    """표준 스키마 1행. 규칙을 어기면 생성되지 않습니다."""

    source: str
    indicator_code: str
    region_code: str
    period: int
    value: float | None
    unit: str
    vintage: str
    source_url: str
    missing_reason: str | None = None
    retrieved_at: str = field(default_factory=_now)

    def __post_init__(self):
        set_ = object.__setattr__  # frozen 이라 직접 대입이 막혀 있습니다

        for name in ("source", "indicator_code", "region_code", "unit", "source_url"):
            v = getattr(self, name)
            if not isinstance(v, str) or not v.strip():
                raise SchemaError(f"{name} 는 비워 둘 수 없습니다: {v!r}")
            set_(self, name, v.strip())

        if not (REGION_KR.match(self.region_code) or REGION_ISO3.match(self.region_code)):
            raise SchemaError(
                f"region_code 형식이 아닙니다: {self.region_code!r}. "
                "국내는 KR-00, 국외는 ISO 3166-1 alpha-3 입니다. "
                "사전(references/region-codes.csv)에 없는 지역명을 임의로 매핑하지 마십시오."
            )

        set_(self, "period", _as_period(self.period))
        set_(self, "value", _as_value(self.value))

        if self.vintage not in VINTAGES:
            raise SchemaError(f"vintage 는 {' / '.join(sorted(VINTAGES))} 중 하나입니다: {self.vintage!r}")

        reason = self.missing_reason or None
        if reason is not None and reason not in MISSING_REASONS:
            raise SchemaError(
                f"missing_reason 이 정의된 코드가 아닙니다: {reason!r}. "
                f"허용: {' / '.join(MISSING_REASONS)}"
            )
        set_(self, "missing_reason", reason)

        # 핵심 규칙. 결측과 사유는 한 몸입니다.
        if self.value is None and reason is None:
            raise SchemaError(
                f"value 가 비어 있으면 missing_reason 이 필요합니다 "
                f"({self.source}/{self.region_code}/{self.period}). "
                "미조사 · 해당없음 · 비공개 중 무엇인지 수집 시점에 적어야 합니다. "
                "지금 안 적으면 이후에 복원할 방법이 없습니다."
            )
        if self.value is not None and reason is not None:
            raise SchemaError(
                f"값이 있는데 missing_reason 이 붙었습니다 "
                f"({self.source}/{self.region_code}/{self.period}: "
                f"value={self.value}, missing_reason={reason})."
            )

        _check_retrieved_at(self.retrieved_at)

    def as_dict(self):
        """표준 스키마 컬럼 순서의 dict. None 은 csv 에서 빈칸으로 적힙니다."""
        d = asdict(self)
        return {c: d[c] for c in COLUMNS}


def _as_period(raw):
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise SchemaError(f"period 는 연도 정수입니다: {raw!r}")
    s = str(raw).strip()
    if not s.isdigit():
        raise SchemaError(f"period 를 연도로 읽을 수 없습니다: {raw!r}")
    n = int(s)
    if not PERIOD_MIN <= n <= PERIOD_MAX:
        raise SchemaError(f"period 가 범위를 벗어났습니다: {n} (허용 {PERIOD_MIN}~{PERIOD_MAX})")
    return n


def _as_value(raw):
    """빈 문자열과 공백은 결측으로 봅니다. 0 은 결측이 아닙니다."""
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            raise SchemaError(f"value 를 수치로 읽을 수 없습니다: {raw!r}") from None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise SchemaError(f"value 는 실수이거나 비어 있어야 합니다: {raw!r}")
    return float(raw)


def _check_retrieved_at(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise SchemaError("retrieved_at 는 비워 둘 수 없습니다")
    try:
        datetime.fromisoformat(raw)
    except ValueError:
        raise SchemaError(f"retrieved_at 가 ISO 8601 이 아닙니다: {raw!r}") from None
