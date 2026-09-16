#!/usr/bin/env python3
"""World Bank Indicators 수집 → 표준 스키마 CSV.

명세: references/api-registry.md · 출력: references/schema.md · 대상: config/sources.yml 의 wb_tfr

    python exercises/03/collect_wb.py                    실제 호출
    python exercises/03/collect_wb.py --scenario paged    fake_api.py 로 장애 주입

이 호출에서 일어날 수 있는 실패 다섯 가지와 처리
------------------------------------------------------------------
1. 비JSON 응답 (format 함정 · 에러 페이지)
   format 을 빼면 200 과 함께 XML 이 옵니다. 파싱에서 터지거나, 더 나쁘게는
   엉뚱하게 파싱됩니다.
   → format=json 을 요청에 강제하고, 본문이 JSON 이 아니면 재시도하지 않고
     즉시 중단합니다. 일시적 장애가 아니라 요청이 틀린 경우이기 때문입니다.

2. 조용한 0건
   국가코드나 지표코드를 틀리면 200 과 빈 배열이 옵니다. 에러가 없습니다.
   → [0].total 과 실제 행 수를 둘 다 확인해 0 이면 중단합니다.

3. 부분 수집 (뒷장 유실)
   per_page 를 크게 줘도 서버가 자기 한도로 잘라 첫 장만 줍니다.
   → 응답이 알려준 pages 를 따라 끝까지 돌고, 모은 행 수가 total 과 다르면
     중단합니다. 요청한 per_page 가 아니라 응답의 값을 믿습니다.

4. 스키마 드리프트 (응답 키 변경)
   countryiso3code 가 사라지면 region_code 가 전부 빈칸이 됩니다. 행 수는
   그대로라 숫자만 봐서는 모릅니다.
   → 필수 키를 명시해 검사하고, 비슷한 다른 키로 얼버무리지 않고 중단합니다.

5. 타임아웃 · 호출 제한(429) 에서의 무한 재시도
   → 재시도 3회와 지수 백오프, 페이지 상한으로 못박습니다. 소진하면 중단합니다.

결측: value 가 null 이면 missing_reason 을 NA_NOTSURVEYED 로 적습니다.
      World Bank 는 미조사와 비공개를 구분해 주지 않습니다. 구분이 필요하면
      원 통계기관 쪽을 확인해야 합니다.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(HERE))
KST = timezone(timedelta(hours=9))

SOURCE = "WORLDBANK"
COUNTRY_KEY = "countryiso3code"      # 실패 4. 이 키가 없으면 멈춥니다
RETRY = 3
BACKOFF = 1.5
MAX_PAGES = 50                       # 실패 5. 페이지 순회의 상한
TIMEOUT = 30

COLUMNS = ["source", "indicator_code", "region_code", "period", "value", "unit",
           "vintage", "retrieved_at", "source_url", "missing_reason"]


class CollectError(RuntimeError):
    """수집을 중단시키는 오류. 산출물을 만들지 않습니다."""


def load_source(source_id):
    with open(os.path.join(BASE, "config", "sources.yml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for s in cfg.get("sources", []):
        if s["id"] == source_id:
            return s
    raise CollectError(f"config/sources.yml 에 {source_id} 가 없습니다")


def build_request(source):
    """엔드포인트의 {country}/{indicator} 를 채우고 질의 파라미터를 만듭니다."""
    params = dict(source.get("params", {}))
    try:
        url = source["endpoint"].format(country=params.pop("country"),
                                        indicator=params.pop("indicator"))
    except KeyError as e:
        raise CollectError(f"config/sources.yml 의 params 에 {e} 가 없습니다") from None
    params["format"] = "json"        # 실패 1. 빼면 XML 이 옵니다
    return url, params


def _get(http, url, params):
    """요청 1건. 재시도는 일시적 장애에만, 횟수를 정해 놓고 합니다."""
    last = None
    for attempt in range(RETRY):
        try:
            r = http.get(url, params=params, timeout=TIMEOUT)
            if r.status_code == 429:
                raise RuntimeError("호출 제한(429)")     # 실패 5. 재시도 대상
            r.raise_for_status()
            try:
                return r.json()
            except ValueError:
                body = (getattr(r, "text", "") or "").strip().replace("\n", " ")[:300]
                raise CollectError(                      # 실패 1. 재시도하지 않습니다
                    "응답 본문이 JSON 이 아닙니다. format=json 과 파라미터를 확인하세요.\n"
                    f"    요청: {url}\n"
                    f"    본문: {body}"
                ) from None
        except CollectError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < RETRY - 1:
                time.sleep(BACKOFF ** attempt)
    raise CollectError(f"{RETRY}회 재시도 후 실패: {last}")


def _envelope(payload):
    """응답 최상위는 [페이징, 관측치] 2원소 배열이어야 합니다."""
    ok = (isinstance(payload, list) and len(payload) == 2
          and isinstance(payload[0], dict) and isinstance(payload[1], list))
    if not ok:
        raise CollectError(                              # 실패 4. 봉투가 바뀐 경우
            "응답 최상위가 [페이징, 관측치] 2원소 배열이 아닙니다. "
            "명세가 바뀌었거나 에러 응답입니다.\n"
            f"    받은 모양: {repr(payload)[:300]}"
        )
    return payload[0], payload[1]


def fetch(source, http, log=print):
    """(요청주소, 관측치) 쌍의 목록을 끝까지 모아 돌려줍니다."""
    url, params = build_request(source)
    pairs, total, page = [], 0, 1

    while page <= MAX_PAGES:
        page_params = dict(params, page=page)
        meta, chunk = _envelope(_get(http, url, page_params))
        total = int(meta.get("total", 0))
        pages = int(meta.get("pages", 1))

        if total == 0:                                   # 실패 2. 조용한 0건
            raise CollectError(
                "수집 0건입니다. country · indicator · date 를 확인하세요.\n"
                f"    요청: {url}?{urlencode(page_params)}"
            )
        if not chunk:                                    # 실패 3. 빈 페이지
            raise CollectError(f"{page}쪽이 비어 있습니다. 모은 행 {len(pairs)}/{total}건")

        page_url = f"{url}?{urlencode(page_params)}"
        pairs.extend((page_url, row) for row in chunk)
        log(f"  {page}/{pages}쪽 {len(chunk)}행 (누적 {len(pairs)}/{total})")

        if page >= pages:
            break
        page += 1

    if len(pairs) != total:                              # 실패 3 · 5. 부분 수집
        raise CollectError(
            f"부분 수집 {len(pairs)}/{total}건. 페이지 처리 또는 페이지 상한"
            f"(MAX_PAGES={MAX_PAGES})을 확인하세요."
        )
    return pairs


def _year(raw):
    s = str(raw or "").strip()
    if not (len(s) == 4 and s.isdigit() and 1900 <= int(s) <= 2100):
        raise CollectError(f"연도로 읽을 수 없는 period 입니다: {raw!r}. 소스 설정의 date 를 확인하세요.")
    return int(s)


def to_rows(pairs, source):
    """표준 스키마로 옮깁니다. 키가 하나라도 없으면 빈칸을 만들지 않고 멈춥니다."""
    now = datetime.now(KST).isoformat(timespec="seconds")
    rows = []
    for page_url, x in pairs:
        region = (x.get(COUNTRY_KEY) or "").strip()
        if not region:                                   # 실패 4. 지역 소실
            raise CollectError(
                f"필수 키 '{COUNTRY_KEY}' 가 없거나 비어 있습니다. 응답 스키마가 바뀌었습니다.\n"
                f"    이 행의 키: {sorted(x)}"
            )
        indicator = (x.get("indicator") or {}).get("id")
        if not indicator:
            raise CollectError(f"필수 키 'indicator.id' 가 없습니다. 이 행의 키: {sorted(x)}")

        value = x.get("value")
        rows.append(dict(zip(COLUMNS, [
            SOURCE, indicator, region, _year(x.get("date")),
            None if value is None else float(value),
            source["unit"], source["vintage"], now, page_url,
            None if value is not None else "NA_NOTSURVEYED",
        ])))
    return rows


def write_csv(rows, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    sys.path.insert(0, HERE)
    from fake_api import SCENARIOS, FakeAPI

    ap = argparse.ArgumentParser(description="World Bank Indicators 수집")
    ap.add_argument("--source", default="wb_tfr", help="config/sources.yml 의 소스 id")
    ap.add_argument("--scenario", choices=SCENARIOS, help="fake_api.py 로 장애를 주입합니다")
    ap.add_argument("--out", default=os.path.join(BASE, "out", "wb_tfr.csv"))
    args = ap.parse_args(argv)

    if args.scenario:
        http = FakeAPI(args.scenario)
    else:
        import requests
        http = requests

    try:
        source = load_source(args.source)
        rows = to_rows(fetch(source, http), source)
    except CollectError as e:
        print(f"중단: {e}", file=sys.stderr)
        return 1

    write_csv(rows, args.out)
    regions = {r["region_code"] for r in rows}
    missing = sum(1 for r in rows if r["value"] is None)
    print(f"{len(rows)}행 · 국가 {len(regions)}개 · 결측 {missing}건 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
