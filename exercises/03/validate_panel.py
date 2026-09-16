"""패널 검증. references/schema.md 의 중단 조건을 전부 검사합니다.

중단 — 하나라도 걸리면 산출물을 만들지 않습니다.
    1. 수집 행 수 0
    2. 표준 스키마 컬럼 누락
    3. 중복 키 (indicator_code + region_code + period)
    4. 지표별 값 범위 이탈
    5. 시도 17개 미충족
    6. 기간 연속성 위반
    7. 사유 없는 결측

기록 — 진행하되 남깁니다. 원자료가 실제로 그럴 수 있기 때문입니다.
    - 전년 대비 변화율 30% 초과
    - 전국값과 시도 평균의 괴리
    - 허용 범위가 등록되지 않은 지표 (검사를 건너뛴 자리라 직접 짚어줍니다)

    python exercises/03/validate_panel.py
    python exercises/03/validate_panel.py --input out/panel_merged.csv --no-write
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

from contract import COLUMNS, MISSING_REASONS  # noqa: E402

KEY = ["indicator_code", "region_code", "period"]
COUNT_UNITS = {"POLICY_COUNT": "건"}
STOP, NOTE = "중단", "기록"


def _disp(path):
    """표시용 경로. 다른 드라이브에 있으면 relpath 가 터지므로 원본을 그대로 씁니다."""
    try:
        return os.path.relpath(path, BASE)
    except ValueError:
        return path


def load_config():
    with open(os.path.join(BASE, "config", "sources.yml"), encoding="utf-8") as f:
        return yaml.safe_load(f)["validation"]


def _add(results, name, ok, gate, detail=""):
    results.append({"검사": name, "구분": gate,
                    "결과": "통과" if ok else ("확인" if gate == NOTE else "실패"),
                    "내용": detail})


def run(df, v):
    results = []

    # 1. 행 수 0
    _add(results, "행 수 0 아님", len(df) > 0, STOP, f"{len(df)}행")
    if len(df) == 0:
        return results

    # 2. 표준 스키마 컬럼
    missing_cols = [c for c in COLUMNS if c not in df.columns]
    extra = [c for c in df.columns if c not in COLUMNS]
    _add(results, "표준 스키마 컬럼 존재", not missing_cols, STOP,
         (f"누락 {missing_cols}" if missing_cols else "") + (f" 추가 {extra}" if extra else ""))
    if missing_cols:
        return results

    # 3. 중복 키
    dups = df[df.duplicated(KEY, keep=False)]
    _add(results, "중복 키 없음", dups.empty, STOP,
         f"중복 {dups.duplicated(KEY).sum() + 0}건 예: "
         f"{dups.iloc[0][KEY].to_dict() if not dups.empty else ''}" if not dups.empty else "")

    # 4. 지표별 값 범위
    ranges, bad, unregistered = v.get("value_range", {}), [], []
    for ind, g in df.groupby("indicator_code"):
        vals = g["value"].dropna()
        if ind not in ranges:
            unregistered.append(ind)
            continue
        lo, hi = ranges[ind]
        out = vals[(vals < lo) | (vals > hi)]
        if not out.empty:
            bad.append(f"{ind} {list(out.head(3))} (허용 {lo}~{hi})")
    _add(results, "값 범위 확인", not bad, STOP, " · ".join(bad))

    # 5. 시도 17개 — scripts/validate.py 와 같은 기준(패널 전체의 KR- 코드, 전국 제외)
    sido = {r for r in df["region_code"] if r.startswith("KR-") and r != "KR-00"}
    need = v["expected_regions_시도"]
    _add(results, f"시도 {need}개 전수 존재", len(sido) == need, STOP,
         f"{len(sido)}개" + (f" 누락 {need - len(sido)}개" if len(sido) < need else ""))

    # 6. 기간 연속성 — 계열(지표×지역)마다 min~max 사이에 빠진 연도가 없어야 합니다
    holes = []
    for (ind, reg), g in df.groupby(["indicator_code", "region_code"]):
        yrs = set(g["period"])
        gap = set(range(min(yrs), max(yrs) + 1)) - yrs
        if gap:
            holes.append(f"{ind}/{reg} 누락 {sorted(gap)}")
    _add(results, "기간 연속성 (계열별)", not holes, STOP,
         " · ".join(holes[:3]) + (f" 외 {len(holes) - 3}건" if len(holes) > 3 else ""))

    # 7. 사유 없는 결측
    no_reason = df[df["value"].isna() & df["missing_reason"].isna()]
    bad_code = sorted(set(df["missing_reason"].dropna()) - set(MISSING_REASONS))
    _add(results, "결측에 사유 표기", no_reason.empty and not bad_code, STOP,
         (f"사유 없는 결측 {len(no_reason)}건 " if not no_reason.empty else "")
         + (f"정의 안 된 코드 {bad_code}" if bad_code else ""))

    # --- 기록 ---
    limit, spikes = v["yoy_change_limit"], []
    for (ind, reg), g in df.groupby(["indicator_code", "region_code"]):
        s = g.sort_values("period")[["period", "value"]].dropna()
        prev_y, prev_v = None, None
        for _, r in s.iterrows():
            if prev_v not in (None, 0) and prev_y == r["period"] - 1:
                rate = abs(r["value"] - prev_v) / abs(prev_v)
                if rate > limit:
                    spikes.append(f"{ind}/{reg} {int(r['period'])} {rate:.0%}")
            prev_y, prev_v = r["period"], r["value"]
    _add(results, f"전년 대비 변화율 {int(limit * 100)}% 이내", not spikes, NOTE,
         " · ".join(spikes[:5]))

    nat = df[df["region_code"] == "KR-00"]
    if nat.empty:
        _add(results, "전국값과 시도 평균 정합", True, NOTE, "전국(KR-00) 행이 없어 해당 없음")
    else:
        gaps = []
        for (ind, per), g in nat.groupby(["indicator_code", "period"]):
            sido_vals = df[(df.indicator_code == ind) & (df.period == per)
                           & df.region_code.str.startswith("KR-")
                           & (df.region_code != "KR-00")]["value"].dropna()
            n = g["value"].dropna()
            if not sido_vals.empty and not n.empty:
                diff = abs(n.iloc[0] - sido_vals.mean()) / abs(n.iloc[0])
                if diff > 0.10:
                    gaps.append(f"{ind} {per} {diff:.0%}")
        _add(results, "전국값과 시도 평균 정합", not gaps, NOTE, " · ".join(gaps))

    _add(results, "지표별 허용 범위 등록", not unregistered, NOTE,
         f"미등록 {unregistered} — config/sources.yml 의 value_range 에 추가하세요"
         if unregistered else "")

    units = [f"{i}:{sorted(g['unit'].unique())}" for i, g in df.groupby("indicator_code")
             if sorted(g["unit"].unique()) != [COUNT_UNITS.get(i, "명")]]
    _add(results, "지표별 단위 단일", not units, NOTE, " · ".join(units))
    return results


def write_outputs(df, results, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    made = []

    path = os.path.join(out_dir, "panel_validated.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    made.append(path)

    L = ["# 검증 결과", "",
         f"총 {len(df)}행 · 지표 {df['indicator_code'].nunique()}개 · "
         f"지역 {df['region_code'].nunique()}개", "",
         "| 검사 | 구분 | 결과 | 내용 |", "|---|---|---|---|"]
    L += [f"| {r['검사']} | {r['구분']} | {r['결과']} | {r['내용']} |" for r in results]
    L += ["", "중단 조건은 전부 통과했습니다. `확인` 은 원자료가 실제로 그럴 수 있어 "
          "멈추지 않고 기록만 한 항목입니다.", ""]
    path = os.path.join(out_dir, "validation_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    made.append(path)

    tfr = df[df.indicator_code == "TFR"]
    if not tfr.empty:
        t = tfr.pivot_table(index="region_code", columns="period", values="value")
        years = list(t.columns)
        L = [f"# 표 1. 시도별 합계출산율 ({min(years)}–{max(years)})", "",
             "| 지역 | " + " | ".join(str(y) for y in years) + " |",
             "|---" * (len(years) + 1) + "|"]
        for reg, row in t.iterrows():
            cells = []
            for y in years:
                v = row[y]
                if pd.isna(v):
                    src = tfr[(tfr.region_code == reg) & (tfr.period == y)]
                    cells.append(src.iloc[0]["missing_reason"] if not src.empty else "")
                else:
                    cells.append(f"{v:.3f}")
            L.append(f"| {reg} | " + " | ".join(cells) + " |")
        L += ["", "결측은 값을 비우지 않고 사유 코드로 표기했습니다.", ""]
        path = os.path.join(out_dir, "table_tfr.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(L))
        made.append(path)
    return made


def main(argv=None):
    ap = argparse.ArgumentParser(description="패널 검증")
    ap.add_argument("--input", default=os.path.join(BASE, "out", "panel_merged.csv"))
    ap.add_argument("--out-dir", default=os.path.join(BASE, "out"))
    ap.add_argument("--no-write", action="store_true", help="통과해도 산출물을 만들지 않습니다")
    args = ap.parse_args(argv)

    df = pd.read_csv(args.input)
    results = run(df, load_config())

    width = max(len(r["검사"]) for r in results) + 2
    print(f"검증 — {_disp(args.input)}")
    for r in results:
        mark = {"통과": "  ok  ", "확인": " note ", "실패": " FAIL "}[r["결과"]]
        print(f"  [{mark}] {r['검사']:<{width}} {r['내용']}")

    failed = [r for r in results if r["결과"] == "실패"]
    if failed:
        print()
        print(f"중단: 중단 조건 {len(failed)}건 실패. 산출물을 만들지 않습니다.", file=sys.stderr)
        for r in failed:
            print(f"  - {r['검사']}: {r['내용']}", file=sys.stderr)
        return 1

    if args.no_write:
        print("\n통과. (--no-write 이므로 산출물은 만들지 않습니다)")
        return 0

    print()
    print("통과. 산출물을 만듭니다.")
    for p in write_outputs(df, results, args.out_dir):
        print(f"  {_disp(p)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
