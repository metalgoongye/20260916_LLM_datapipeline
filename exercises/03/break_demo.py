"""중단 조건이 정말로 멈추는지 확인합니다.

정상 패널을 중단 조건 하나씩 위반하도록 망가뜨려 validate_panel.py 에 넣고,
(1) 종료코드가 1인지 (2) 산출물 디렉터리가 비어 있는지를 봅니다.

숫자가 나오는데 틀린 것이 제일 위험합니다. 중단은 좋은 결과입니다.

    python exercises/03/break_demo.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(HERE))
PANEL = os.path.join(BASE, "out", "panel_merged.csv")
VALIDATE = os.path.join(HERE, "validate_panel.py")


def f_empty(df):
    return df.iloc[0:0]


def f_drop_column(df):
    return df.drop(columns=["unit"])


def f_duplicate(df):
    row = df[df.indicator_code == "TFR"].iloc[[0]]
    return pd.concat([df, row], ignore_index=True)


def f_out_of_range(df):
    df = df.copy()
    i = df[(df.indicator_code == "TFR") & df.value.notna()].index[0]
    df.loc[i, "value"] = 9.9                      # 허용 0~3
    return df


def f_missing_sido(df):
    return df[df.region_code != "KR-50"]           # 제주 통째로 제거


def f_period_hole(df):
    mask = (df.source == "WORLDBANK") & (df.region_code == "KOR") & (df.period == 2020)
    return df[~mask]                               # 계열 한가운데 구멍


def f_reason_missing(df):
    df = df.copy()
    i = df[df.value.isna()].index[0]
    df.loc[i, "missing_reason"] = None             # 결측인데 사유만 지움
    return df


FAULTS = [
    ("수집 행 수 0", f_empty),
    ("표준 스키마 컬럼 누락", f_drop_column),
    ("중복 키", f_duplicate),
    ("지표별 값 범위 이탈", f_out_of_range),
    ("시도 17개 미충족", f_missing_sido),
    ("기간 연속성 위반", f_period_hole),
    ("사유 없는 결측", f_reason_missing),
]


def run_case(df, tmp):
    """망가뜨린 패널로 검증기를 돌리고 (종료코드, 사유, 산출물 수) 를 돌려줍니다."""
    csv_path = os.path.join(tmp, "broken.csv")
    out_dir = os.path.join(tmp, "out")
    os.makedirs(out_dir, exist_ok=True)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, VALIDATE, "--input", csv_path, "--out-dir", out_dir],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    produced = os.listdir(out_dir)
    reason = ""
    for line in (p.stderr or "").splitlines():
        if line.strip().startswith("- "):
            reason = line.strip()[2:]
            break
    return p.returncode, reason, produced


def main():
    if not os.path.exists(PANEL):
        print(f"중단: {PANEL} 이 없습니다. build_panel.py 를 먼저 돌리세요.", file=sys.stderr)
        return 1
    base = pd.read_csv(PANEL)

    rows = []
    tmp = tempfile.mkdtemp(prefix="break_demo_")
    try:
        code, reason, produced = run_case(base, tmp)
        rows.append(("(망가뜨리지 않음)", len(base), code, len(produced), reason or "통과"))
        shutil.rmtree(os.path.join(tmp, "out"), ignore_errors=True)

        for label, fault in FAULTS:
            broken = fault(base)
            code, reason, produced = run_case(broken, tmp)
            rows.append((label, len(broken), code, len(produced), reason))
            shutil.rmtree(os.path.join(tmp, "out"), ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    w = max(len(r[0]) for r in rows) + 2
    print(f"{'주입한 위반':<{w}}{'행수':>5}{'종료':>5}{'산출물':>6}  검증기가 말한 것")
    print("-" * (w + 90))
    for label, n, code, produced, reason in rows:
        print(f"{label:<{w}}{n:>5}{code:>5}{produced:>6}  {reason[:80]}")

    print()
    ok = True
    _, _, base_code, base_made, _ = rows[0]
    if base_code != 0 or base_made == 0:
        # 베이스라인이 실패하면 아래 7건이 멈춘 이유도 검증 때문이 아닐 수 있습니다
        print(f"⚠ 망가뜨리지 않은 패널이 종료코드 {base_code} · 산출물 {base_made}개입니다. "
              "검증 실패가 아니라 검증기 자체의 오류일 수 있습니다.")
        ok = False
    bad = [r for r in rows[1:] if r[2] == 0 or r[3] > 0]
    if bad:
        print(f"⚠ 멈추지 않았거나 산출물을 만든 경우 {len(bad)}건: {[r[0] for r in bad]}")
        ok = False
    if not ok:
        return 1
    print(f"중단 조건 {len(FAULTS)}개 전부 종료코드 1 · 산출물 0개. "
          f"정상 패널만 종료코드 0 으로 산출물 {base_made}개를 만듭니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
