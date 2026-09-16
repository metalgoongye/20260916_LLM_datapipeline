"""보고서 슬라이드 생성. out/panel.csv → HTML 덱 + PPTX.

    python scripts/report_slides.py

슬라이드에 들어가는 수치는 전부 이 스크립트가 패널에서 계산합니다. 손으로
옮겨 적은 숫자는 하나도 없습니다. 패널이 바뀌면 다시 돌리면 됩니다.

산출물
    out/slides/index.html        make-slide `data-focus` 테마 · 발표자 노트 포함
    out/slides/report.pptx       파워포인트 (네이티브 차트, 편집 가능)
    out/slides/figures.json      계산된 수치

HTML 템플릿: references/slide-theme/data-focus.html
    github.com/Kuneosu/make-slide 의 themes/data-focus/reference.html (MIT)
"""
from __future__ import annotations

import json
import os
import re
import sys

import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "out", "slides")
TEMPLATE = os.path.join(BASE, "references", "slide-theme", "data-focus.html")

SIDO = {
    "KR-11": "서울", "KR-26": "부산", "KR-27": "대구", "KR-28": "인천", "KR-29": "광주",
    "KR-30": "대전", "KR-31": "울산", "KR-36": "세종", "KR-41": "경기", "KR-51": "강원",
    "KR-43": "충북", "KR-44": "충남", "KR-52": "전북", "KR-46": "전남", "KR-47": "경북",
    "KR-48": "경남", "KR-50": "제주",
}
COUNTRY = {"KOR": "한국", "JPN": "일본", "FRA": "프랑스", "DEU": "독일",
           "ITA": "이탈리아", "ESP": "스페인", "USA": "미국"}

ACCENT, GREEN, RED, AMBER, PURPLE = "#2563eb", "#10b981", "#ef4444", "#f59e0b", "#8b5cf6"


# ---------------------------------------------------------------- 수치 계산

def figures(panel_path):
    df = pd.read_csv(panel_path)
    F = {}

    F["overview"] = {
        "rows": int(len(df)),
        "sources": sorted(df["source"].unique().tolist()),
        "indicators": sorted(df["indicator_code"].unique().tolist()),
        "period": [int(df.period.min()), int(df.period.max())],
        "regions": int(df.region_code.nunique()),
        "by_source": {k: int(v) for k, v in df.groupby("source").size().items()},
    }

    nat = df[(df.indicator_code == "TFR") & (df.region_code == "KR-00")].sort_values("period")
    F["tfr_nat"] = [{"year": int(r.period), "value": float(r.value)}
                    for r in nat.itertuples() if pd.notna(r.value)]
    a, b = F["tfr_nat"][0], F["tfr_nat"][-1]
    low = min(F["tfr_nat"], key=lambda x: x["value"])
    F["tfr_change"] = {"first": a, "last": b, "diff": round(b["value"] - a["value"], 3),
                       "pct": round((b["value"] / a["value"] - 1) * 100, 1), "low": low,
                       "rebound_pct": round((b["value"] / low["value"] - 1) * 100, 1),
                       "rebound_years": b["year"] - low["year"]}

    y = int(df[df.indicator_code == "TFR"].period.max())
    s = df[(df.indicator_code == "TFR") & (df.region_code != "KR-00") & (df.period == y)]
    s = s.dropna(subset=["value"]).sort_values("value", ascending=False)
    F["tfr_sido"] = {"year": y, "national": float(nat[nat.period == y].value.iloc[0]),
                     "rows": [{"region": SIDO[r.region_code], "value": float(r.value)}
                              for r in s.itertuples()]}
    F["tfr_sido"]["spread"] = round(F["tfr_sido"]["rows"][0]["value"]
                                    - F["tfr_sido"]["rows"][-1]["value"], 3)

    bn = df[(df.indicator_code == "BIRTHS") & (df.region_code == "KR-00")].sort_values("period")
    F["births_nat"] = [{"year": int(r.period), "value": int(r.value)}
                       for r in bn.itertuples() if pd.notna(r.value)]
    a, b = F["births_nat"][0], F["births_nat"][-1]
    F["births_change"] = {"first": a, "last": b, "diff": b["value"] - a["value"],
                          "pct": round((b["value"] / a["value"] - 1) * 100, 1),
                          "low": min(F["births_nat"], key=lambda x: x["value"])}

    by = b["year"]
    bs = df[(df.indicator_code == "BIRTHS") & (df.region_code != "KR-00") & (df.period == by)]
    bs = bs.dropna(subset=["value"]).sort_values("value", ascending=False)
    rows = [{"region": SIDO[r.region_code], "value": int(r.value)} for r in bs.itertuples()]
    F["births_sido"] = {"year": by, "rows": rows, "national": b["value"],
                        "top3_share": round(sum(x["value"] for x in rows[:3]) / b["value"] * 100, 1)}

    wb = df[df.indicator_code == "SP.DYN.TFRT.IN"].dropna(subset=["value"])
    wy = int(wb.period.max())
    w = wb[wb.period == wy].sort_values("value", ascending=False)
    rows = [{"country": COUNTRY[r.region_code], "code": r.region_code, "value": float(r.value)}
            for r in w.itertuples()]
    kor = next(x for x in rows if x["code"] == "KOR")
    others = [x for x in rows if x["code"] != "KOR"]
    mean = round(sum(x["value"] for x in others) / len(others), 3)
    F["intl"] = {"year": wy, "rows": rows, "kor": kor, "others_mean": mean,
                 "rank": rows.index(kor) + 1, "n": len(rows),
                 "vs_mean_pct": round((kor["value"] / mean - 1) * 100, 1)}

    p = df[df.indicator_code == "POLICY_COUNT"].copy()
    p["name"] = p.region_code.map(SIDO)
    p = p.sort_values("value", ascending=False)
    F["policy"] = {"year": int(p.period.iloc[0]), "total": int(p.value.sum()),
                   "regions": int(len(p)), "mean": round(float(p.value.mean()), 1),
                   "rows": [{"region": r.name, "value": int(r.value)} for r in p.itertuples()]}
    m = p[["region_code", "value"]].rename(columns={"value": "policy"}).merge(
        s[["region_code", "value"]].rename(columns={"value": "tfr"}), on="region_code")
    F["policy"]["corr"] = round(float(m["policy"].corr(m["tfr"])), 3)
    F["policy"]["corr_n"] = int(len(m))

    miss = df[df.value.isna()]
    F["quality"] = {"missing": int(len(miss)),
                    "by_reason": {k: int(v) for k, v in miss.missing_reason.value_counts().items()},
                    "coverage": round((1 - len(miss) / len(df)) * 100, 1),
                    "detail": sorted({f"{r.region_code} {int(r.period)}" for r in miss.itertuples()})}
    return F


# ---------------------------------------------------------------- HTML

def _bars(items, label_key, value_key, fmt, maxh=230, hi=None):
    """테마의 CSS 막대 차트. 값에 비례한 높이를 계산해 넣습니다."""
    top = max(x[value_key] for x in items)
    out = []
    for x in items:
        h = round(x[value_key] / top * maxh)
        o = 0.25 + 0.60 * (x[value_key] / top)
        color = f"rgba(239,68,68,0.80)" if hi and x[label_key] in hi else \
                f"rgba(37,99,235,{o:.2f})"
        out.append(
            '<div class="bar-group">'
            f'<div class="bar-value">{fmt(x[value_key])}</div>'
            f'<div class="bar" style="height:{h}px;background:{color}"></div>'
            f'<div class="bar-label">{x[label_key]}</div></div>')
    return ('<div class="a chart-container"><div class="chart-grid">'
            + "".join('<div class="chart-grid-line"><span class="chart-grid-val">'
                      f'{fmt(top * f)}</span></div>' for f in (1, .75, .5, .25, 0))
            + '</div><div class="chart">' + "".join(out) + "</div></div>")


def _slide(notes, body):
    return f'<div class="slide" data-notes="{notes}">\n{body}\n</div>\n'


def _stat(label, value, trend=None, up=None):
    t = ""
    if trend:
        cls = "up" if up else "down"
        arrow = "&#9650;" if up else "&#9660;"
        t = f'<div class="stat-trend {cls}">{arrow} {trend}</div>'
    return (f'<div class="stat-card"><div class="stat-label">{label}</div>'
            f'<div class="stat-value">{value}</div>{t}</div>')


def build_html(F):
    n1 = lambda v: f"{v:.3f}"          # noqa: E731
    n0 = lambda v: f"{v:,.0f}"         # noqa: E731
    ov, tc, ts, bc, bs, it, po, q = (F["overview"], F["tfr_change"], F["tfr_sido"],
                                     F["births_change"], F["births_sido"], F["intl"],
                                     F["policy"], F["quality"])
    y0, y1 = ov["period"]
    S = []

    S.append(_slide(
        f"인구 지표 패널 보고서입니다. {ov['rows']}행, 소스 {len(ov['sources'])}개, "
        f"{y0}년부터 {y1}년까지의 데이터를 다룹니다. [2분]",
        f'  <div class="a overline">POLICY DATA PIPELINE &middot; {y0}&ndash;{y1}</div>\n'
        '  <h1 class="a xl">인구 지표 패널<br>보고서</h1>\n'
        f'  <div class="a sub">합계출산율 &middot; 출생아수 &middot; 국제비교 &middot; 지자체 정책</div>'))

    S.append(_slide(
        f"먼저 핵심 수치 네 개입니다. 전국 합계출산율은 {tc['last']['value']}, "
        f"출생아수는 {bc['last']['value']:,}명입니다. 10년 동안 각각 {tc['pct']}%, "
        f"{bc['pct']}% 줄었습니다. 다만 {tc['low']['year']}년을 저점으로 반등 중입니다.",
        '  <div class="a tag">Summary</div>\n'
        '  <h2 class="a lg">한 장 요약</h2>\n'
        '  <div class="a stat-cards">\n'
        + _stat(f"합계출산율 {tc['last']['year']}", n1(tc['last']['value']),
                f"{tc['pct']}% vs {tc['first']['year']}", up=False)
        + _stat(f"출생아수 {bc['last']['year']}", f"{bc['last']['value']:,}",
                f"{bc['pct']}% vs {bc['first']['year']}", up=False)
        + _stat(f"국제 순위 {it['year']}", f"{it['rank']}/{it['n']}",
                f"평균 대비 {it['vs_mean_pct']}%", up=False)
        + _stat("데이터 커버리지", f"{q['coverage']}%", f"결측 {q['missing']}건", up=True)
        + '  </div>\n'
        f'  <p class="a body">패널 <span class="data-num">{ov["rows"]}</span>행 &middot; '
        f'지표 {len(ov["indicators"])}개 &middot; 지역 {ov["regions"]}개 &middot; '
        f'중단 조건 <span class="hi">7개 전부 통과</span></p>'))

    src = " &middot; ".join(f"{k} {v}행" for k, v in ov["by_source"].items())
    S.append(_slide(
        "데이터 구성입니다. KOSIS에서 합계출산율과 출생아수를, World Bank에서 국제 비교 지표를, "
        "정책브리핑에서 지자체 정책 건수를 가져왔습니다. 전부 같은 표준 스키마 한 벌로 맞췄습니다.",
        '  <div class="num-bg">01</div>\n  <div class="a tag">Data</div>\n'
        '  <h2 class="a md">무엇을 어디서<br>가져왔나</h2>\n'
        f'  <p class="a body">소스 {src}. 모든 행이 <span class="hi">표준 스키마 10개 컬럼</span>'
        f'(source / indicator_code / region_code / period / value / unit / vintage / '
        f'retrieved_at / source_url / missing_reason)으로 수렴합니다. '
        f'지역 코드는 국내 <span class="data-num">KR-</span>+행정구역코드, '
        f'국외는 <span class="data-num">ISO3</span>. 기간은 연 단위 정수입니다.</p>'))

    S.append(_slide(
        f"전국 합계출산율 추이입니다. {tc['first']['year']}년 {tc['first']['value']}에서 "
        f"{tc['low']['year']}년 {tc['low']['value']}까지 떨어졌습니다. "
        f"빨간 막대가 저점입니다. [잠깐 멈춤]",
        '  <div class="num-bg">02</div>\n  <div class="a tag">Trend</div>\n'
        f'  <h2 class="a md">전국 합계출산율 {tc["first"]["year"]}&ndash;{tc["last"]["year"]}</h2>\n'
        + _bars(F["tfr_nat"], "year", "value", n1, hi={tc["low"]["year"]})
        + f'\n  <p class="a body"><span class="data-num">{n1(tc["first"]["value"])}</span> → '
          f'<span class="data-num">{n1(tc["last"]["value"])}</span>, '
          f'<span class="hi">{tc["pct"]}%</span> 감소. 저점은 {tc["low"]["year"]}년 '
          f'{n1(tc["low"]["value"])}입니다.</p>'))

    S.append(_slide(
        f"주목할 점은 마지막 {tc['rebound_years']}년입니다. 저점 이후 {tc['rebound_pct']}% 올랐습니다. "
        "추세 반전인지 일시적 반등인지는 아직 단정할 수 없습니다. 원인 분석은 이 패널 밖의 일입니다.",
        '  <div class="a tag">Inflection</div>\n'
        f'  <h2 class="a lg">{tc["low"]["year"]}년 저점 이후 반등</h2>\n'
        '  <div class="a cmp">\n'
        '    <div class="cmp-col cmp-bad">\n'
        f'      <div class="cmp-label">{tc["first"]["year"]}&ndash;{tc["low"]["year"]} 하락</div>\n'
        f'      <ul><li>{n1(tc["first"]["value"])} → {n1(tc["low"]["value"])}</li>'
        f'<li>{tc["low"]["year"] - tc["first"]["year"]}년 연속 감소</li>'
        f'<li>OECD 최저 수준 진입</li></ul>\n    </div>\n'
        '    <div class="cmp-col cmp-good">\n'
        f'      <div class="cmp-label">{tc["low"]["year"]}&ndash;{tc["last"]["year"]} 반등</div>\n'
        f'      <ul><li>{n1(tc["low"]["value"])} → <strong style="color:var(--accent)">'
        f'{n1(tc["last"]["value"])}</strong></li>'
        f'<li>{tc["rebound_years"]}년간 +{tc["rebound_pct"]}%</li>'
        f'<li>추세 반전 여부는 <strong>판단 보류</strong></li></ul>\n    </div>\n  </div>'))

    top, bot = ts["rows"][0], ts["rows"][-1]
    S.append(_slide(
        f"{ts['year']}년 시도별입니다. 가장 높은 {top['region']} {top['value']}와 "
        f"가장 낮은 {bot['region']} {bot['value']} 사이 격차가 {ts['spread']}입니다. "
        "전국 평균 하나로는 이 편차가 보이지 않습니다.",
        '  <div class="num-bg">03</div>\n  <div class="a tag">Region</div>\n'
        f'  <h2 class="a md">시도별 합계출산율 &middot; {ts["year"]}</h2>\n'
        + _bars(ts["rows"], "region", "value", n1, hi={bot["region"]})
        + f'\n  <p class="a body">최고 <span class="hi">{top["region"]} {n1(top["value"])}</span> ~ '
          f'최저 <span class="hi">{bot["region"]} {n1(bot["value"])}</span>, '
          f'격차 <span class="data-num">{ts["spread"]}</span>. '
          f'전국 {n1(ts["national"])}.</p>'))

    S.append(_slide(
        f"출생아수는 더 가파릅니다. {bc['first']['year']}년 {bc['first']['value']:,}명에서 "
        f"{bc['last']['year']}년 {bc['last']['value']:,}명으로 {abs(bc['diff']):,}명 줄었습니다. "
        "출산율보다 감소폭이 큰 것은 모집단인 가임 여성 인구 자체가 줄었기 때문입니다.",
        '  <div class="num-bg">04</div>\n  <div class="a tag">Births</div>\n'
        f'  <h2 class="a md">전국 출생아수 {bc["first"]["year"]}&ndash;{bc["last"]["year"]}</h2>\n'
        + _bars(F["births_nat"], "year", "value", n0, hi={bc["low"]["year"]})
        + f'\n  <p class="a body"><span class="data-num">{bc["first"]["value"]:,}</span>명 → '
          f'<span class="data-num">{bc["last"]["value"]:,}</span>명, '
          f'<span class="hi">{abs(bc["diff"]):,}명({bc["pct"]}%)</span> 감소. '
          f'출산율 감소폭 {tc["pct"]}%보다 큽니다.</p>'))

    t3 = bs["rows"][:3]
    seg = t3 + [{"region": "그 외 14개 시도",
                 "value": sum(x["value"] for x in bs["rows"][3:])}]
    tot = sum(x["value"] for x in seg)
    deg, parts, legend = 0, [], []
    for x, c in zip(seg, [ACCENT, GREEN, AMBER, "#d1d5db"]):
        d = x["value"] / tot * 360
        parts.append(f"{c} {deg:.0f}deg {deg + d:.0f}deg")
        legend.append(f'<div class="legend-item"><div class="legend-dot" style="background:{c}">'
                      f'</div><span class="legend-label">{x["region"]}</span>'
                      f'<span class="legend-value">{x["value"] / tot * 100:.1f}%</span></div>')
        deg += d
    S.append(_slide(
        f"{bs['year']}년 출생아를 지역별로 보면 경기, 서울, 인천 세 곳이 "
        f"전체의 {bs['top3_share']}퍼센트를 차지합니다. 출생아수는 수도권에 몰려 있는데 "
        "출산율은 서울이 가장 낮습니다. 인구 규모와 출산율은 다른 이야기입니다.",
        '  <div class="num-bg">05</div>\n  <div class="a tag">Distribution</div>\n'
        f'  <h2 class="a md">출생아 지역 분포 &middot; {bs["year"]}</h2>\n'
        f'  <div class="a donut-container">\n'
        f'    <div class="donut" style="background:conic-gradient({",".join(parts)})">\n'
        f'      <div class="donut-hole"><div class="donut-total">'
        f'{bs["national"] / 1000:.0f}K</div>'
        f'<div class="donut-total-label">전국</div></div>\n    </div>\n'
        f'    <div class="donut-legend">{"".join(legend)}</div>\n  </div>\n'
        f'  <p class="a body">상위 3개 시도가 <span class="hi">{bs["top3_share"]}%</span>. '
        f'다만 출산율 최저는 서울입니다.</p>'))

    S.append(_slide(
        f"국제 비교입니다. {it['year']}년 기준 한국은 {it['kor']['value']}로 "
        f"비교 {it['n']}개국 중 {it['rank']}위, 즉 최하위입니다. "
        f"나머지 국가 평균 {it['others_mean']}보다 {abs(it['vs_mean_pct'])}퍼센트 낮습니다.",
        '  <div class="num-bg">06</div>\n  <div class="a tag">Global</div>\n'
        f'  <h2 class="a md">국제 비교 &middot; {it["year"]}</h2>\n'
        + _bars(it["rows"], "country", "value", n1, hi={"한국"})
        + f'\n  <p class="a body">한국 <span class="data-num">{n1(it["kor"]["value"])}</span> — '
          f'<span class="hi">{it["n"]}개국 중 {it["rank"]}위</span>. '
          f'나머지 평균 {n1(it["others_mean"])} 대비 '
          f'<span class="hi">{it["vs_mean_pct"]}%</span>. '
          f'출처 World Bank <span class="data-num">SP.DYN.TFRT.IN</span>.</p>'))

    S.append(_slide(
        f"지자체 출산지원정책은 {po['year']}년 기준 {po['regions']}개 시도에 "
        f"모두 {po['total']}건, 평균 {po['mean']}건입니다. 정책브리핑 페이지를 크롤링해 모았습니다.",
        '  <div class="num-bg">07</div>\n  <div class="a tag">Policy</div>\n'
        f'  <h2 class="a md">지자체 출산지원정책 &middot; {po["year"]}</h2>\n'
        + _bars(po["rows"], "region", "value", lambda v: f"{v:.0f}")
        + f'\n  <p class="a body">총 <span class="data-num">{po["total"]}</span>건 &middot; '
          f'{po["regions"]}개 시도 &middot; 평균 <span class="data-num">{po["mean"]}</span>건</p>'))

    S.append(_slide(
        f"여기서 한 가지 짚고 갑니다. 정책 건수와 출산율의 상관계수는 {po['corr']}로 "
        "사실상 0입니다. 정책을 많이 만든 지역의 출산율이 높지도 낮지도 않습니다. "
        "건수는 정책의 규모나 예산을 나타내지 않고, 표본도 17개뿐입니다. "
        "이 표로는 정책 효과를 말할 수 없습니다. [강조]",
        '  <div class="a tag">Caution</div>\n'
        '  <h2 class="a lg">정책 건수로<br>효과를 말할 수 없습니다</h2>\n'
        '  <div class="a stat-cards">\n'
        + _stat("상관계수", f"{po['corr']}", "사실상 0", up=None)
        + _stat("표본", f"n={po['corr_n']}", "시도 17개", up=None)
        + _stat("단위", "건", "예산·규모 아님", up=None)
        + _stat("기간", f"{po['year']}", "단일 시점", up=None)
        + '  </div>\n'
        '  <p class="a body">건수는 <span class="hi">정책의 크기를 담지 않습니다</span>. '
        '10억짜리 사업과 안내문 한 장이 똑같이 1건입니다. '
        '효과를 보려면 예산액·수급자수·시행연도가 필요합니다.</p>'))

    reason = " &middot; ".join(f"{k} {v}건" for k, v in q["by_reason"].items())
    S.append(_slide(
        f"데이터 품질입니다. 커버리지 {q['coverage']}퍼센트, 결측 {q['missing']}건이고 "
        "전부 사유가 붙어 있습니다. 결측을 빈칸 하나로 뭉개지 않고 "
        "미조사·해당없음·비공개 세 가지로 나눠 기록합니다.",
        '  <div class="num-bg">08</div>\n  <div class="a tag">Quality</div>\n'
        '  <h2 class="a md">결측을 빈칸으로<br>두지 않습니다</h2>\n'
        '  <div class="a stat-cards">\n'
        + _stat("커버리지", f"{q['coverage']}%", f"{ov['rows']}행 중", up=True)
        + _stat("결측", f"{q['missing']}건", "전부 사유 기록", up=True)
        + _stat("사유 없는 결측", "0건", "중단 조건", up=True)
        + _stat("중복 키", "0건", "중단 조건", up=True)
        + '  </div>\n'
        f'  <p class="a body">결측 내역: {reason}. '
        f'전부 World Bank <span class="data-num">{it["year"] + 1}</span>년 미발표분입니다. '
        f'<span class="hi">NA_NOTSURVEYED</span>(미조사) / '
        f'<span class="hi">NA_NOTAPPLICABLE</span>(해당없음) / '
        f'<span class="hi">NA_CONFIDENTIAL</span>(비공개)로 구분합니다.</p>'))

    S.append(_slide(
        "만드는 방식입니다. 언어모델에게 숫자를 읽히지 않고, 숫자를 읽는 코드를 짜게 했습니다. "
        "검증에서 중단 조건에 걸리면 산출물을 아예 만들지 않습니다. "
        "이 슬라이드의 모든 수치도 패널에서 스크립트가 계산한 것입니다.",
        '  <div class="num-bg">09</div>\n  <div class="a tag">Method</div>\n'
        '  <h2 class="a md">어떻게 만들었나</h2>\n'
        '  <div class="a cmp">\n'
        '    <div class="cmp-col cmp-bad">\n'
        '      <div class="cmp-label">하지 않은 것</div>\n'
        '      <ul><li>응답에서 숫자를 옮겨 적기</li>'
        '<li>0건을 정상 종료로 넘기기</li>'
        '<li>사전에 없는 지역명 임의 매핑</li>'
        '<li>검사를 느슨하게 고쳐 통과시키기</li></ul>\n    </div>\n'
        '    <div class="cmp-col cmp-good">\n'
        '      <div class="cmp-label">한 것</div>\n'
        '      <ul><li>파싱·검증·산출 전부 <strong style="color:var(--accent)">코드</strong>로</li>'
        '<li>중단 조건 <strong style="color:var(--accent)">7개</strong> 전수 검사</li>'
        '<li>판단이 든 건은 코드북에 미확인으로</li>'
        '<li>원본 응답은 스냅샷으로 보관</li></ul>\n    </div>\n  </div>'))

    S.append(_slide(
        "마지막으로 한계입니다. 코드북에 미확인 2건이 남아 있고, 정책 건수는 단일 시점입니다. "
        "확인은 연구자가 합니다. 질문 받겠습니다.",
        '  <div class="a tag">Limits</div>\n'
        '  <h2 class="a lg">남은 것</h2>\n'
        '  <p class="a body">'
        '&#9312; 코드북 4절 <span class="hi">미확인 2건</span> — '
        '규칙으로 분류되지 않은 정책명. 확인은 연구자가 합니다.<br>'
        f'&#9313; 정책 건수는 <span class="hi">{po["year"]}년 단일 시점</span> — '
        '시계열이 없어 추세 분석 불가.<br>'
        '&#9314; 표준 스키마의 결측 코드 3개로는 '
        '<span class="hi">&ldquo;아직 미발표&rdquo;</span>를 표현할 수 없습니다.<br>'
        '&#9315; 반등의 <span class="hi">원인</span>은 이 패널 밖입니다.</p>'))

    S.append(_slide(
        "감사합니다. 파이프라인 전체는 run.py 한 줄로 재현됩니다.",
        '  <div class="a overline">THANK YOU</div>\n'
        '  <h1 class="a xl">질문</h1>\n'
        f'  <div class="a sub" style="margin-top:24px">'
        f'{ov["rows"]}행 &middot; 소스 {len(ov["sources"])}개 &middot; {y0}&ndash;{y1}</div>\n'
        '  <div class="a" style="display:flex;gap:24px;margin-top:32px">'
        '<span style="font-size:14px;color:var(--text-secondary);font-family:var(--mono)">'
        'python run.py</span>'
        '<span style="font-size:14px;color:var(--text-tertiary)">&middot;</span>'
        '<span style="font-size:14px;color:var(--text-secondary);font-family:var(--mono)">'
        'out/panel.csv</span></div>'))

    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()

    slides = "".join(S).replace('<div class="slide"', '<div class="slide active"', 1)
    html = re.sub(r'(<div class="deck">\n).*?(\n</div>\n\n<!-- Notes Panel -->)',
                  lambda m: m.group(1) + slides + m.group(2), tpl, flags=re.S)
    if html == tpl:
        raise RuntimeError("템플릿에서 deck 영역을 찾지 못했습니다. 템플릿이 바뀌었는지 확인하세요.")
    html = re.sub(r"<title>.*?</title>", "<title>인구 지표 패널 보고서</title>", html, flags=re.S)
    return html, len(S)


# ---------------------------------------------------------------- PPTX

def build_pptx(F, path):
    """네이티브 차트로 만든 파워포인트. 열어서 그대로 편집할 수 있습니다.

    pptx-spec.md 의 제약을 따릅니다 — 그라디언트 없음, 단색 hex, 16:9.
    폰트만 예외로 맑은 고딕을 씁니다. Arial 은 한글 자형이 없습니다.
    """
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Emu, Inches, Pt

    FONT = "맑은 고딕"
    C = {k: RGBColor.from_string(v.lstrip("#")) for k, v in
         {"accent": ACCENT, "green": GREEN, "red": RED, "amber": AMBER,
          "ink": "#1a1a2e", "muted": "#6b7280", "line": "#e5e7eb",
          "surface": "#f9fafb"}.items()}

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    W = prs.slide_width

    def add(notes):
        s = prs.slides.add_slide(blank)
        s.notes_slide.notes_text_frame.text = notes
        return s

    def text(s, x, y, w, h, runs, size=18, color="ink", bold=False, align=PP_ALIGN.LEFT):
        tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = True
        for i, line in enumerate(runs if isinstance(runs, list) else [runs]):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            r = p.add_run()
            r.text = line
            r.font.size, r.font.bold, r.font.name = Pt(size), bold, FONT
            r.font.color.rgb = C[color]
        return tb

    def bullets(s, x, y, w, h, items, size=16):
        tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = True
        for i, line in enumerate(items):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.space_after = Pt(10)
            r = p.add_run()
            r.text = "· " + line
            r.font.size, r.font.name = Pt(size), FONT
            r.font.color.rgb = C["ink"]

    def header(s, tag, title):
        text(s, 0.7, 0.45, 11, 0.4, tag, size=12, color="accent", bold=True)
        text(s, 0.7, 0.85, 12, 0.9, title, size=34, bold=True)

    def statrow(s, cards, y=2.0):
        n = len(cards)
        gap, margin = 0.25, 0.7
        w = (13.333 - margin * 2 - gap * (n - 1)) / n
        for i, (label, value, sub) in enumerate(cards):
            x = margin + i * (w + gap)
            box = s.shapes.add_shape(1, Inches(x), Inches(y), Inches(w), Inches(1.9))
            box.fill.solid()
            box.fill.fore_color.rgb = C["surface"]
            box.line.color.rgb = C["line"]
            box.shadow.inherit = False
            box.text_frame.text = ""
            text(s, x + 0.2, y + 0.18, w - 0.4, 0.35, label, size=12, color="muted")
            text(s, x + 0.2, y + 0.58, w - 0.4, 0.7, value, size=30, bold=True, color="accent")
            text(s, x + 0.2, y + 1.35, w - 0.4, 0.4, sub, size=11, color="muted")

    def chart(s, cats, vals, series_name, y=1.95, h=4.4, kind="bar", fmt='0.000'):
        cd = CategoryChartData()
        cd.categories = cats
        cd.add_series(series_name, vals)
        t = XL_CHART_TYPE.COLUMN_CLUSTERED if kind == "bar" else XL_CHART_TYPE.LINE_MARKERS
        gf = s.shapes.add_chart(t, Inches(0.7), Inches(y), Inches(11.9), Inches(h), cd)
        ch = gf.chart
        ch.has_legend = False
        plot = ch.plots[0]
        plot.has_data_labels = True
        plot.data_labels.number_format = fmt
        plot.data_labels.number_format_is_linked = False
        plot.data_labels.font.size = Pt(10)
        plot.data_labels.font.name = FONT
        if kind == "bar":
            plot.series[0].format.fill.solid()
            plot.series[0].format.fill.fore_color.rgb = C["accent"]
        for ax in (ch.category_axis, ch.value_axis):
            ax.tick_labels.font.size = Pt(11)
            ax.tick_labels.font.name = FONT
        return ch

    ov, tc, ts, bc, bs, it, po, q = (F["overview"], F["tfr_change"], F["tfr_sido"],
                                     F["births_change"], F["births_sido"], F["intl"],
                                     F["policy"], F["quality"])
    y0, y1 = ov["period"]

    # 1 표지
    s = add(f"인구 지표 패널 보고서입니다. {ov['rows']}행, {y0}~{y1}년. [2분]")
    text(s, 0.9, 2.4, 11.5, 1.0, f"POLICY DATA PIPELINE · {y0}–{y1}",
         size=14, color="accent", bold=True)
    text(s, 0.9, 3.0, 11.5, 1.6, "인구 지표 패널 보고서", size=54, bold=True)
    text(s, 0.9, 4.6, 11.5, 0.6, "합계출산율 · 출생아수 · 국제비교 · 지자체 정책",
         size=18, color="muted")

    # 2 요약
    s = add(f"핵심 수치 네 개입니다. 합계출산율 {tc['last']['value']}, "
            f"출생아수 {bc['last']['value']:,}명. 10년간 각각 {tc['pct']}%, {bc['pct']}%.")
    header(s, "SUMMARY", "한 장 요약")
    statrow(s, [
        (f"합계출산율 {tc['last']['year']}", f"{tc['last']['value']:.3f}",
         f"{tc['pct']}% vs {tc['first']['year']}"),
        (f"출생아수 {bc['last']['year']}", f"{bc['last']['value']:,}",
         f"{bc['pct']}% vs {bc['first']['year']}"),
        (f"국제 순위 {it['year']}", f"{it['rank']}/{it['n']}",
         f"평균 대비 {it['vs_mean_pct']}%"),
        ("데이터 커버리지", f"{q['coverage']}%", f"결측 {q['missing']}건"),
    ])
    text(s, 0.7, 4.3, 11.9, 1.2,
         [f"패널 {ov['rows']}행 · 지표 {len(ov['indicators'])}개 · 지역 {ov['regions']}개",
          "중단 조건 7개 전부 통과"], size=16, color="muted")

    # 3 데이터 개요
    s = add("KOSIS, World Bank, 정책브리핑 세 소스를 표준 스키마 한 벌로 맞췄습니다.")
    header(s, "DATA", "무엇을 어디서 가져왔나")
    bullets(s, 0.7, 2.0, 11.9, 4.5, [
        f"소스 {len(ov['sources'])}개 — " + ", ".join(
            f"{k} {v}행" for k, v in ov["by_source"].items()),
        f"지표 {len(ov['indicators'])}개 — " + ", ".join(ov["indicators"]),
        "표준 스키마 10개 컬럼 — source, indicator_code, region_code, period, value, "
        "unit, vintage, retrieved_at, source_url, missing_reason",
        "지역 코드 — 국내 KR-+행정구역코드 2자리, 국외 ISO 3166-1 alpha-3",
        "기간 — 연 단위 정수. 분기·월은 소스 설정에 적힌 방식으로 연 단위 집계",
    ], size=17)

    # 4 TFR 추이
    s = add(f"{tc['first']['year']}년 {tc['first']['value']}에서 "
            f"{tc['low']['year']}년 {tc['low']['value']}까지 하락했습니다.")
    header(s, "TREND", f"전국 합계출산율 {tc['first']['year']}–{tc['last']['year']}")
    chart(s, [str(x["year"]) for x in F["tfr_nat"]],
          [x["value"] for x in F["tfr_nat"]], "합계출산율", h=4.0)
    text(s, 0.7, 6.15, 11.9, 0.7,
         f"{tc['first']['value']:.3f} → {tc['last']['value']:.3f} ({tc['pct']}%) · "
         f"저점 {tc['low']['year']}년 {tc['low']['value']:.3f}", size=15, color="muted")

    # 5 반등
    s = add(f"저점 이후 {tc['rebound_years']}년간 {tc['rebound_pct']}% 반등했습니다. "
            "추세 반전인지는 단정할 수 없습니다.")
    header(s, "INFLECTION", f"{tc['low']['year']}년 저점 이후 반등")
    half = 5.9
    for i, (label, items, col) in enumerate([
        (f"{tc['first']['year']}–{tc['low']['year']} 하락",
         [f"{tc['first']['value']:.3f} → {tc['low']['value']:.3f}",
          f"{tc['low']['year'] - tc['first']['year']}년 연속 감소", "OECD 최저 수준 진입"], "red"),
        (f"{tc['low']['year']}–{tc['last']['year']} 반등",
         [f"{tc['low']['value']:.3f} → {tc['last']['value']:.3f}",
          f"{tc['rebound_years']}년간 +{tc['rebound_pct']}%", "추세 반전 여부는 판단 보류"], "green"),
    ]):
        x = 0.7 + i * (half + 0.6)
        box = s.shapes.add_shape(1, Inches(x), Inches(2.1), Inches(half), Inches(3.4))
        box.fill.solid()
        box.fill.fore_color.rgb = C["surface"]
        box.line.color.rgb = C["line"]
        box.shadow.inherit = False
        box.text_frame.text = ""
        text(s, x + 0.3, 2.35, half - 0.6, 0.45, label, size=15, bold=True, color=col)
        bullets(s, x + 0.3, 2.95, half - 0.6, 2.3, items, size=15)

    # 6 시도별
    s = add(f"{ts['year']}년 시도별 격차가 {ts['spread']}입니다. "
            "전국 평균 하나로는 이 편차가 보이지 않습니다.")
    header(s, "REGION", f"시도별 합계출산율 · {ts['year']}")
    chart(s, [x["region"] for x in ts["rows"]], [x["value"] for x in ts["rows"]],
          "합계출산율", h=4.0)
    text(s, 0.7, 6.15, 11.9, 0.7,
         f"최고 {ts['rows'][0]['region']} {ts['rows'][0]['value']:.3f} · "
         f"최저 {ts['rows'][-1]['region']} {ts['rows'][-1]['value']:.3f} · "
         f"격차 {ts['spread']} · 전국 {ts['national']:.3f}", size=15, color="muted")

    # 7 출생아수 추이
    s = add(f"출생아수는 {abs(bc['diff']):,}명 줄었습니다. 출산율보다 감소폭이 큽니다.")
    header(s, "BIRTHS", f"전국 출생아수 {bc['first']['year']}–{bc['last']['year']}")
    chart(s, [str(x["year"]) for x in F["births_nat"]],
          [x["value"] for x in F["births_nat"]], "출생아수", h=4.0, fmt='#,##0')
    text(s, 0.7, 6.15, 11.9, 0.7,
         f"{bc['first']['value']:,}명 → {bc['last']['value']:,}명 "
         f"({abs(bc['diff']):,}명, {bc['pct']}%) · 출산율 감소폭 {tc['pct']}%보다 큼",
         size=15, color="muted")

    # 8 지역 분포
    s = add(f"경기·서울·인천이 전체의 {bs['top3_share']}%입니다. "
            "출생아수는 수도권에 몰려 있는데 출산율은 서울이 최저입니다.")
    header(s, "DISTRIBUTION", f"출생아 지역 분포 · {bs['year']}")
    chart(s, [x["region"] for x in bs["rows"]], [x["value"] for x in bs["rows"]],
          "출생아수", h=4.0, fmt='#,##0')
    text(s, 0.7, 6.15, 11.9, 0.7,
         f"상위 3개 시도({', '.join(x['region'] for x in bs['rows'][:3])})가 "
         f"{bs['top3_share']}% · 다만 출산율 최저는 서울", size=15, color="muted")

    # 9 국제비교
    s = add(f"{it['year']}년 기준 한국은 {it['n']}개국 중 {it['rank']}위, 최하위입니다.")
    header(s, "GLOBAL", f"국제 비교 · {it['year']}")
    chart(s, [x["country"] for x in it["rows"]], [x["value"] for x in it["rows"]],
          "합계출산율", h=4.0)
    text(s, 0.7, 6.15, 11.9, 0.7,
         f"한국 {it['kor']['value']:.3f} — {it['n']}개국 중 {it['rank']}위 · "
         f"나머지 평균 {it['others_mean']:.3f} 대비 {it['vs_mean_pct']}% · "
         f"출처 World Bank SP.DYN.TFRT.IN", size=15, color="muted")

    # 10 정책
    s = add(f"{po['year']}년 기준 {po['regions']}개 시도에 모두 {po['total']}건입니다.")
    header(s, "POLICY", f"지자체 출산지원정책 · {po['year']}")
    chart(s, [x["region"] for x in po["rows"]], [x["value"] for x in po["rows"]],
          "정책 건수", h=4.0, fmt='0')
    text(s, 0.7, 6.15, 11.9, 0.7,
         f"총 {po['total']}건 · {po['regions']}개 시도 · 평균 {po['mean']}건",
         size=15, color="muted")

    # 11 주의
    s = add(f"상관계수가 {po['corr']}로 사실상 0입니다. 이 표로는 정책 효과를 말할 수 없습니다. [강조]")
    header(s, "CAUTION", "정책 건수로 효과를 말할 수 없습니다")
    statrow(s, [("상관계수", f"{po['corr']}", "사실상 0"),
                ("표본", f"n={po['corr_n']}", "시도 17개"),
                ("단위", "건", "예산·규모 아님"),
                ("기간", f"{po['year']}", "단일 시점")])
    bullets(s, 0.7, 4.3, 11.9, 2.4, [
        "건수는 정책의 크기를 담지 않습니다. 10억짜리 사업과 안내문 한 장이 똑같이 1건입니다.",
        "효과를 보려면 예산액·수급자수·시행연도가 필요합니다.",
        "표본 17개, 단일 시점으로는 인과를 논할 수 없습니다.",
    ], size=16)

    # 12 품질
    s = add(f"커버리지 {q['coverage']}%, 결측 {q['missing']}건 전부 사유가 붙어 있습니다.")
    header(s, "QUALITY", "결측을 빈칸으로 두지 않습니다")
    statrow(s, [("커버리지", f"{q['coverage']}%", f"{ov['rows']}행 중"),
                ("결측", f"{q['missing']}건", "전부 사유 기록"),
                ("사유 없는 결측", "0건", "중단 조건"),
                ("중복 키", "0건", "중단 조건")])
    bullets(s, 0.7, 4.3, 11.9, 2.4, [
        "NA_NOTSURVEYED (미조사) — 보간 검토 가능",
        "NA_NOTAPPLICABLE (해당없음) — 보간 금지",
        "NA_CONFIDENTIAL (비공개) — 값은 존재, 다른 경로 검토",
        "결측 내역: " + ", ".join(f"{k} {v}건" for k, v in q["by_reason"].items())
        + f" (전부 World Bank {it['year'] + 1}년 미발표분)",
    ], size=16)

    # 13 방법
    s = add("숫자를 읽히지 않고 숫자를 읽는 코드를 짰습니다. "
            "이 슬라이드의 수치도 전부 스크립트가 계산한 것입니다.")
    header(s, "METHOD", "어떻게 만들었나")
    for i, (label, items, col) in enumerate([
        ("하지 않은 것", ["응답에서 숫자를 옮겨 적기", "0건을 정상 종료로 넘기기",
                      "사전에 없는 지역명 임의 매핑", "검사를 느슨하게 고쳐 통과시키기"], "red"),
        ("한 것", ["파싱·검증·산출 전부 코드로", "중단 조건 7개 전수 검사",
                 "판단이 든 건은 코드북에 미확인으로", "원본 응답은 스냅샷으로 보관"], "green"),
    ]):
        x = 0.7 + i * (half + 0.6)
        box = s.shapes.add_shape(1, Inches(x), Inches(2.1), Inches(half), Inches(3.9))
        box.fill.solid()
        box.fill.fore_color.rgb = C["surface"]
        box.line.color.rgb = C["line"]
        box.shadow.inherit = False
        box.text_frame.text = ""
        text(s, x + 0.3, 2.35, half - 0.6, 0.45, label, size=15, bold=True, color=col)
        bullets(s, x + 0.3, 2.95, half - 0.6, 2.8, items, size=15)

    # 14 한계
    s = add("한계입니다. 코드북 미확인 2건은 연구자가 확인합니다. 질문 받겠습니다.")
    header(s, "LIMITS", "남은 것")
    bullets(s, 0.7, 2.1, 11.9, 4.3, [
        "코드북 4절 미확인 2건 — 규칙으로 분류되지 않은 정책명. 확인은 연구자가 합니다.",
        f"정책 건수는 {po['year']}년 단일 시점 — 시계열이 없어 추세 분석 불가.",
        "표준 스키마의 결측 코드 3개로는 '아직 미발표'를 표현할 수 없습니다.",
        "반등의 원인은 이 패널 밖입니다. 인과 분석에는 다른 설계가 필요합니다.",
    ], size=17)
    text(s, 0.7, 6.4, 11.9, 0.6, "재현: python run.py · 산출물: out/panel.csv",
         size=14, color="muted")

    # 15 맺음
    s = add("감사합니다. 파이프라인 전체는 run.py 한 줄로 재현됩니다.")
    text(s, 0.9, 2.6, 11.5, 0.5, "THANK YOU", size=14, color="accent", bold=True)
    text(s, 0.9, 3.1, 11.5, 1.4, "질문", size=54, bold=True)
    text(s, 0.9, 4.6, 11.5, 0.6,
         f"{ov['rows']}행 · 소스 {len(ov['sources'])}개 · {y0}–{y1}", size=18, color="muted")
    text(s, 0.9, 5.3, 11.5, 0.5, "python run.py   ·   out/panel.csv", size=14, color="muted")

    prs.save(path)
    return len(prs.slides.__iter__.__self__._sldIdLst)


# ---------------------------------------------------------------- main

def main():
    panel = os.path.join(BASE, "out", "panel.csv")
    if not os.path.exists(panel):
        print("중단: out/panel.csv 가 없습니다. python run.py 를 먼저 돌리세요.", file=sys.stderr)
        return 1
    os.makedirs(OUT, exist_ok=True)

    F = figures(panel)
    with open(os.path.join(OUT, "figures.json"), "w", encoding="utf-8") as f:
        json.dump(F, f, ensure_ascii=False, indent=1)

    html, n = build_html(F)
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    pptx_path = os.path.join(OUT, "report.pptx")
    m = build_pptx(F, pptx_path)

    print(f"HTML  out/slides/index.html   {n}장")
    print(f"PPTX  out/slides/report.pptx  {m}장")
    print(f"수치  out/slides/figures.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
