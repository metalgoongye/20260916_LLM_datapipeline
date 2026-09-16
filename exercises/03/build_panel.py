"""3교시 세 데이터 → 하나의 패널.

    API      out/wb_tfr.csv          World Bank 실시간 (국가 · ISO3)
             out/samples_merged.csv  샘플 3형식 중 국내분 (시도)
    크롤링    out/policy_count.csv    정책브리핑 픽스처 (시도)
    내부자료  out/internal_panel.csv  internal_panel.xlsx (시도)

확정된 병합 규칙
- World Bank 국가 지표는 **실시간 수집본**을 정본으로 씁니다. samples/ 의
  WORLDBANK 9행은 구조 확인용 가공값이라 버립니다.
- 국내 시도 TFR 은 **공표자료 우선(KOSIS > OPENDATA > INTERNAL)**, 앞 순위가
  결측이면 다음 순위의 실측값으로 보완합니다. 보완한 행은 코드북에 남깁니다.
- 사전에 없는 지역명, 규칙으로 분류되지 않는 정책명은 판단하지 않고
  코드북 4절에 미확인으로 남깁니다.

    python exercises/03/build_panel.py
"""
from __future__ import annotations

import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, BASE)

from contract import COLUMNS, MISSING_REASONS  # noqa: E402
from scripts.normalize import POLICY_TAXONOMY, load_region_map  # noqa: E402

OUT = os.path.join(BASE, "out")
KEY = ["indicator_code", "region_code", "period"]

# 같은 키가 여러 소스에 있을 때의 채택 순서. references/api-registry.md 기준.
PRIORITY = {"KOSIS": 0, "OPENDATA": 1, "WORLDBANK": 2, "정책브리핑": 3, "INTERNAL": 9}

PARTS = [
    ("API 실시간", "wb_tfr.csv", None),
    ("API 샘플(국내분)", "samples_merged.csv", "WORLDBANK"),   # WORLDBANK 행은 제외
    ("크롤링", "policy_count.csv", None),
    ("내부자료", "internal_panel.csv", None),
]

COUNT_UNITS = {"POLICY_COUNT": "건"}   # 건수 지표는 명으로 환산할 수 없습니다


class BuildError(RuntimeError):
    pass


def load_parts(log=print):
    frames = []
    for label, filename, drop_source in PARTS:
        path = os.path.join(OUT, filename)
        if not os.path.exists(path):
            raise BuildError(
                f"{filename} 이 없습니다. 3교시 산출물을 먼저 만드세요.\n"
                f"    API      python exercises/03/collect_wb.py\n"
                f"             python exercises/03/merge_samples.py\n"
                f"    크롤링    python exercises/03/crawl_policy_page.py\n"
                f"    내부자료  python exercises/03/convert_panel.py"
            )
        df = pd.read_csv(path)
        before = len(df)
        if drop_source:
            df = df[df["source"] != drop_source]
        log(f"  {label:<16} {filename:<22} {len(df):>3}행"
            + (f"  ({before - len(df)}행 제외: {drop_source})" if drop_source else ""))
        frames.append(df)
    return pd.concat(frames, ignore_index=True)[COLUMNS]


def resolve(df):
    """중복 키를 소스 우선순위로 정리합니다. 앞 순위가 결측이면 뒤 순위 실측으로 보완."""
    df = df.copy()
    df["_rank"] = df["source"].map(PRIORITY).fillna(99).astype(int)
    df["_has"] = df["value"].notna()

    kept, supplemented, dropped = [], [], []
    for key, g in df.groupby(KEY, sort=False):
        g = g.sort_values("_rank")
        top = g.iloc[0]
        real = g[g["_has"]]
        if real.empty:
            chosen = top                      # 전부 결측이면 앞 순위의 결측 사유를 남깁니다
        else:
            chosen = real.iloc[0]
            if chosen["source"] != top["source"]:
                supplemented.append({
                    "키": f"{key[0]} / {key[1]} / {key[2]}",
                    "앞순위": f"{top['source']} (결측: {top['missing_reason']})",
                    "채택": f"{chosen['source']} = {chosen['value']}",
                })
        kept.append(chosen)
        for _, row in g.iterrows():
            if row is not chosen and not row.equals(chosen):
                dropped.append(f"{key[0]}/{key[1]}/{key[2]} {row['source']}={row['value']}")
    return pd.DataFrame(kept)[COLUMNS], supplemented, dropped


def check(df, log=print):
    """references/schema.md 의 중단 조건. 걸리면 산출물을 만들지 않습니다."""
    fail = []
    if len(df) == 0:
        fail.append("수집 행 수 0")
    if list(df.columns) != COLUMNS:
        fail.append(f"표준 스키마 컬럼 불일치: {list(df.columns)}")
    dups = int(df.duplicated(KEY).sum())
    if dups:
        fail.append(f"중복 키 {dups}건")
    bad_reason = sorted(set(df["missing_reason"].dropna()) - set(MISSING_REASONS))
    if bad_reason:
        fail.append(f"정의되지 않은 결측 사유 {bad_reason}")
    no_reason = int((df["value"].isna() & df["missing_reason"].isna()).sum())
    if no_reason:
        fail.append(f"사유 없는 결측 {no_reason}건")

    ranges = {"TFR": (0, 3), "BIRTHS": (0, 500000),
              "SP.DYN.TFRT.IN": (0, 8), "POLICY_COUNT": (0, 500)}
    for ind, (lo, hi) in ranges.items():
        v = df[(df["indicator_code"] == ind)]["value"].dropna()
        if not v.empty and (v.min() < lo or v.max() > hi):
            fail.append(f"{ind} 값 범위 이탈 {v.min()}~{v.max()} (허용 {lo}~{hi})")

    for ind, g in df.groupby("indicator_code"):
        units = sorted(g["unit"].unique())
        want = COUNT_UNITS.get(ind, "명")
        if units != [want]:
            fail.append(f"{ind} 단위 불일치 {units} (기대 {want})")

    for f in fail:
        log(f"  실패: {f}")
    return fail


def codebook(df, supplemented, pending, unmapped):
    L = ["# 코드북 (3교시 통합 패널)", "",
         f"총 {len(df)}행 · 지표 {df['indicator_code'].nunique()}개 · "
         f"지역 {df['region_code'].nunique()}개", "", "---", "",
         "## 4. 판정이 필요했던 항목", "",
         "규칙으로 처리하지 못해 판단이 들어간 건입니다. **확인은 연구자가 합니다.**", ""]

    L += ["### 규칙으로 분류되지 않은 정책명", ""]
    if pending:
        L += ["| 원문 | 제안된 분류 | 근거 | 확인 |", "|---|---|---|---|"]
        L += [f"| {p['원문']} | (제안 없음) | 사전(POLICY_TAXONOMY)에 없음 | 미확인 |"
              for p in pending]
        L += ["", "제안란을 비운 것은 의도적입니다. 분류를 추측하지 않습니다. "
              "확인이 끝나면 `scripts/normalize.py` 의 `POLICY_TAXONOMY` 에 등록하세요.", ""]
    else:
        L += ["없음", ""]

    L += ["### 사전에 없어 매핑하지 못한 지역명", ""]
    L += ([f"- {u}" for u in unmapped] if unmapped else ["없음"]) + [""]

    L += ["### 소스 우선순위로 보완한 값", "",
          "앞 순위 소스가 결측이라 다음 순위의 실측값을 채택한 건입니다. "
          "출처가 섞이므로 인용 시 주의가 필요합니다.", ""]
    if supplemented:
        L += ["| 키 | 앞 순위 | 채택 |", "|---|---|---|"]
        L += [f"| {s['키']} | {s['앞순위']} | {s['채택']} |" for s in supplemented]
    else:
        L += ["없음"]
    L += [""]

    L += ["---", "", "## 참고 · 결측 내역", "",
          "| 사유 | 뜻 | 건수 |", "|---|---|---|"]
    counts = df["missing_reason"].value_counts()
    L += [f"| {c} | {MISSING_REASONS[c]} | {counts.get(c, 0)} |" for c in MISSING_REASONS]
    return "\n".join(L) + "\n"


def main():
    print("[1/4] 3교시 산출물 읽기")
    try:
        raw = load_parts()
    except BuildError as e:
        print(f"중단: {e}", file=sys.stderr)
        return 1

    print()
    print("[2/4] 중복 키 정리")
    print(f"  단순 결합 {len(raw)}행 · 중복 키 {int(raw.duplicated(KEY).sum())}건")
    df, supplemented, dropped = resolve(raw)
    df = df.sort_values(["indicator_code", "region_code", "period"]).reset_index(drop=True)
    print(f"  정리 후 {len(df)}행 (중복 {len(raw) - len(df)}행 제거, 보완 {len(supplemented)}건)")

    print()
    print("[3/4] 검증 (references/schema.md 중단 조건)")
    fail = check(df)
    if fail:
        print("\n중단: 검증 실패. 산출물을 만들지 않습니다.", file=sys.stderr)
        return 1
    print("  통과")

    # 판단이 필요한 건 — 크롤링 단계에서 이미 식별된 것들을 다시 확인합니다
    region_map = load_region_map(BASE)
    known_regions = set(region_map.values())
    unmapped = sorted({r for r in df["region_code"]
                       if r.startswith("KR-") and r not in known_regions})
    sys.path.insert(0, HERE)
    from crawl_policy_page import extract
    _, items, crawl_unmapped, pending = extract(
        os.path.join(HERE, "policy_page.html"), region_map)
    unmapped += [u["항목"] for u in crawl_unmapped]

    print()
    print("[4/4] 산출물")
    panel = os.path.join(OUT, "panel_merged.csv")
    df.to_csv(panel, index=False, encoding="utf-8-sig")
    book = os.path.join(OUT, "codebook_merged.md")
    with open(book, "w", encoding="utf-8") as f:
        f.write(codebook(df, supplemented, pending, unmapped))
    print(f"  out/panel_merged.csv     {len(df)}행")
    print(f"  out/codebook_merged.md   미확인 {len(pending) + len(unmapped)}건")

    print()
    print("지표별")
    print(df.groupby(["indicator_code", "unit"]).agg(
        행수=("value", "size"), 지역수=("region_code", "nunique"),
        기간=("period", lambda s: f"{s.min()}~{s.max()}"),
        결측=("value", lambda s: int(s.isna().sum()))).to_string())
    print()
    print("소스별")
    print(df["source"].value_counts().to_string())
    if supplemented:
        print()
        print("소스 우선순위로 보완한 값")
        for s in supplemented:
            print(f"  {s['키']}: {s['앞순위']} → {s['채택']}")
    print()
    print(f"미확인 (코드북 4절): 정책명 {len(pending)}건 {[p['원문'] for p in pending]} · "
          f"지역명 {len(unmapped)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
