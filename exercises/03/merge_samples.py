"""samples/ 의 JSON · XML · CSV 를 표준 스키마 한 벌로 합칩니다.

형식마다 결측 표기가 다릅니다. 전부 사유 코드로 옮깁니다.

    JSON  "value": null            → NA_NOTSURVEYED
    XML   <value></value> · <value/> → NA_NOTSURVEYED
    CSV   '-'                       → NA_CONFIDENTIAL   (사용자 확정)
          공란                       → NA_NOTSURVEYED    (사용자 확정)

CSV 의 '-' 와 공란은 응답만으로는 구분할 근거가 없어 사람이 정했습니다.
XML 의 <value></value> 와 <value/> 는 파서에서 똑같이 text=None 입니다.
건너뛰도록 짜면 9행이 7행이 되는데 에러가 나지 않습니다. 그래서 태그가
'없는' 경우와 '비어 있는' 경우를 갈라서 봅니다.

    python exercises/03/merge_samples.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
import xml.etree.ElementTree as ET

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(HERE))
SAMPLES = os.path.join(HERE, "samples")
sys.path.insert(0, HERE)
sys.path.insert(0, BASE)

from contract import COLUMNS, Row, SchemaError  # noqa: E402
from scripts.normalize import load_region_map  # noqa: E402

UNIT = "명"                       # 세 파일 모두 합계출산율. JSON 의 unit 은 빈 문자열입니다
CSV_MISSING = {"-": "NA_CONFIDENTIAL", "": "NA_NOTSURVEYED"}


class ParseError(RuntimeError):
    """구조가 명세와 다릅니다. 조용히 건너뛰지 않고 멈춥니다."""


def _rel(path):
    return os.path.relpath(path, BASE).replace("\\", "/")


def from_json(path):
    """World Bank 식. 원소 2개 배열이고 [1] 이 관측치입니다."""
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if not (isinstance(payload, list) and len(payload) == 2 and isinstance(payload[1], list)):
        raise ParseError(f"{_rel(path)}: 최상위가 [페이징, 관측치] 2원소 배열이 아닙니다")
    meta, items = payload

    rows = []
    for x in items:
        value = x.get("value")          # null 이 그대로 None 으로 옵니다
        rows.append(Row(
            source="WORLDBANK",
            indicator_code=x["indicator"]["id"],
            region_code=x["countryiso3code"],
            period=x["date"],
            value=value,
            unit=UNIT,
            vintage="연간",
            source_url=_rel(path),
            missing_reason=None if value is not None else "NA_NOTSURVEYED",
        ))
    _check_count(path, len(rows), meta.get("total"))
    return rows


def from_xml(path, region_map):
    """공공데이터포털 식. <items><item> 아래 평평한 항목들입니다."""
    root = ET.parse(path).getroot()
    items = root.findall(".//items/item")
    if not items:
        raise ParseError(f"{_rel(path)}: items/item 을 찾지 못했습니다. 구조가 바뀌었는지 확인하세요")

    rows = []
    for item in items:
        el = item.find("value")
        if el is None:                  # 태그 자체가 없음 = 스키마 변경
            raise ParseError(f"{_rel(path)}: <value> 태그가 없는 item 이 있습니다")
        raw = (el.text or "").strip()   # <value></value> 와 <value/> 가 여기서 똑같아집니다

        name = item.findtext("regionName", "").strip()
        code = region_map.get(name)
        if code is None:                # 임의 매핑 금지. 사전에 없으면 멈춥니다
            raise ParseError(
                f"{_rel(path)}: '{name}' 이 references/region-codes.csv 에 없습니다. "
                "임의로 매핑하지 않고 멈춥니다."
            )
        rows.append(Row(
            source="OPENDATA",
            indicator_code=item.findtext("itemCode", "").strip(),
            region_code=code,
            period=item.findtext("year", "").strip(),
            value=raw or None,
            unit=item.findtext("unit", "").strip() or UNIT,
            vintage="확정",
            source_url=_rel(path),
            missing_reason=None if raw else "NA_NOTSURVEYED",
        ))
    _check_count(path, len(rows), root.findtext(".//totalCount"))
    return rows


def from_csv(path, region_map):
    """KOSIS 내려받기 식. 결측이 '-' 와 공란 두 가지입니다."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        records = list(csv.DictReader(f))
    if not records:
        raise ParseError(f"{_rel(path)}: 데이터 행이 없습니다")

    rows = []
    for x in records:
        raw = (x.get("값") or "").strip()
        if raw in CSV_MISSING:
            value, reason = None, CSV_MISSING[raw]
        else:
            value, reason = raw, None

        name = (x.get("행정구역별") or "").strip()
        code = region_map.get(name)
        if code is None:
            raise ParseError(
                f"{_rel(path)}: '{name}' 이 references/region-codes.csv 에 없습니다. "
                "임의로 매핑하지 않고 멈춥니다."
            )
        rows.append(Row(
            source="KOSIS",
            indicator_code="TFR",       # CSV 에는 코드가 없습니다. config/sources.yml 의 kosis_tfr 기준
            region_code=code,
            period=x.get("시점"),
            value=value,
            unit=(x.get("단위") or "").strip() or UNIT,
            vintage="확정",
            source_url=_rel(path),
            missing_reason=reason,
        ))
    return rows


def _check_count(path, got, declared):
    """응답이 스스로 밝힌 건수와 대조합니다. 부분 파싱을 조용히 넘기지 않습니다."""
    if declared is None:
        return
    if got != int(declared):
        raise ParseError(f"{_rel(path)}: {got}행만 읽었는데 응답은 {declared}건이라고 합니다")


def main():
    region_map = load_region_map(BASE)
    try:
        rows = (from_json(os.path.join(SAMPLES, "response.json"))
                + from_xml(os.path.join(SAMPLES, "response.xml"), region_map)
                + from_csv(os.path.join(SAMPLES, "response.csv"), region_map))
    except (ParseError, SchemaError) as e:
        print(f"중단: {e}", file=sys.stderr)
        return 1

    df = pd.DataFrame([r.as_dict() for r in rows], columns=COLUMNS)
    out = os.path.join(BASE, "out", "samples_merged.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")

    print(f"{len(df)}행 · 지역 {df['region_code'].nunique()}개 · "
          f"기간 {df['period'].min()}~{df['period'].max()} → {_rel(out)}")
    print()
    print("소스별")
    print(df.groupby("source").agg(행수=("value", "size"),
                                   결측=("value", lambda s: int(s.isna().sum()))).to_string())
    print()
    print("결측 사유별")
    miss = df[df["value"].isna()]
    print(miss.groupby("missing_reason").size().to_string())
    print()
    print(miss[["source", "region_code", "period", "missing_reason"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
