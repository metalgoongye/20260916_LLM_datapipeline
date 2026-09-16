"""policy_page.html → 지역별 출산지원정책 건수 (표준 스키마).

셀렉터
    section.region-card[data-region]   지역 블록
      h2.region-name                   지역명 (data-region 과 교차 확인)
      ul.policy-list li.policy-item
        span.policy-name               정책명
        span.policy-type               구분

크롤링은 0건이 나와도 HTTP 코드가 소리를 내지 않습니다. 그래서 기대 최소
행 수와 필수 필드를 검사에 넣고, 구조가 바뀌면 빈 결과를 내지 않고 멈춥니다.

정책명은 사전(POLICY_TAXONOMY)으로만 분류합니다. 사전에 없으면 판단하지 않고
미확인으로 남깁니다. 지역명이 사전에 없을 때도 마찬가지로 멈춥니다.

    python exercises/03/crawl_policy_page.py [경로]
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, BASE)

from contract import Row  # noqa: E402
from scripts.normalize import POLICY_TAXONOMY, load_region_map  # noqa: E402

SOURCE = "정책브리핑"
INDICATOR = "POLICY_COUNT"
UNIT = "건"
PERIOD = 2026                      # 페이지의 수집일 2026-08-31 기준
SOURCE_URL = "https://www.korea.kr/"
MIN_ROWS = 15                      # 기대 최소 건수. 이보다 적으면 구조 변경을 의심합니다


class CrawlError(RuntimeError):
    """조용한 빈 결과를 막습니다."""


def check_robots(path=os.path.join(HERE, "robots.txt"), agent="*"):
    """실행 전에 robots.txt 를 확인합니다. 픽스처라 같은 폴더의 파일을 봅니다."""
    if not os.path.exists(path):
        raise CrawlError(f"robots.txt 가 없습니다: {path}. 확인 없이 긁지 않습니다.")
    rules, current, delay = {"disallow": [], "allow": []}, False, None
    for line in open(path, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if not line or ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            current = v in (agent, "*")
        elif current and k in rules:
            rules[k].append(v)
        elif current and k == "crawl-delay":
            delay = float(v)
    if "/" in rules["disallow"] and "/" not in rules["allow"]:
        raise CrawlError("robots.txt 가 전체 경로를 막고 있습니다. 수집하지 않습니다.")
    return delay


def extract(html_path, region_map):
    with open(html_path, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    sections = soup.select("section.region-card")
    if not sections:
        raise CrawlError(
            f"{os.path.basename(html_path)}: section.region-card 를 찾지 못했습니다. "
            "페이지 구조가 바뀌었습니다. 빈 결과를 내지 않고 멈춥니다."
        )

    items, unmapped, pending = [], [], []
    for sec in sections:
        name = (sec.get("data-region") or "").strip()
        shown = sec.select_one("h2.region-name")
        shown = shown.get_text(strip=True) if shown else ""
        if not name:
            name = shown
        if name and shown and name != shown:
            raise CrawlError(f"지역명 불일치: data-region={name!r} vs h2={shown!r}")

        for li in sec.select("ul.policy-list li.policy-item"):
            el = li.select_one("span.policy-name")
            if el is None:
                raise CrawlError(f"{name}: span.policy-name 이 없는 항목이 있습니다")
            items.append((name, el.get_text(strip=True)))

    if len(items) < MIN_ROWS:
        raise CrawlError(f"수집 {len(items)}건 < 기대 최소 {MIN_ROWS}건. 구조 변경을 확인하세요.")

    counts = {}
    for name, policy in items:
        code = region_map.get(name)
        if code is None:                       # 임의 매핑 금지. 미확인으로 남깁니다
            if not any(u["항목"] == name for u in unmapped):
                unmapped.append({"항목": name, "종류": "지역명", "소스": "policy_page"})
            continue
        if policy not in POLICY_TAXONOMY:       # 규칙으로 못 정합니다. 판단하지 않습니다
            if not any(p["원문"] == policy for p in pending):
                pending.append({"원문": policy, "종류": "정책분류", "제안": "",
                                "근거": "사전(POLICY_TAXONOMY)에 없음", "확인": "미확인"})
        counts[code] = counts.get(code, 0) + 1

    rows = [Row(source=SOURCE, indicator_code=INDICATOR, region_code=code, period=PERIOD,
                value=float(n), unit=UNIT, vintage="확정", source_url=SOURCE_URL)
            for code, n in sorted(counts.items())]
    return rows, items, unmapped, pending


def main(argv=None):
    argv = argv or sys.argv[1:]
    html_path = argv[0] if argv else os.path.join(HERE, "policy_page.html")
    if not os.path.isabs(html_path):
        html_path = os.path.join(BASE, html_path)

    try:
        delay = check_robots()
        print(f"robots.txt 확인 — Crawl-delay {delay}")
        region_map = load_region_map(BASE)
        rows, items, unmapped, pending = extract(html_path, region_map)
    except CrawlError as e:
        print(f"중단: {e}", file=sys.stderr)
        return 1

    import csv

    from contract import COLUMNS
    out = os.path.join(BASE, "out", "policy_count.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(r.as_dict() for r in rows)

    print(f"정책 {len(items)}건 · 지역 {len(rows)}개 → out/policy_count.csv")
    if unmapped:
        print(f"사전에 없는 지역명 {len(unmapped)}건: {[u['항목'] for u in unmapped]}")
    print(f"규칙으로 분류 안 된 정책명 {len(pending)}건 (미확인):")
    for p in pending:
        print(f"  {p['원문']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
