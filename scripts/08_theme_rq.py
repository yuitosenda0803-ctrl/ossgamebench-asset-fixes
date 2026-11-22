"""テーマA の RQ0〜RQ2 の分析（表を outputs/tables/ に書く。図は 09 で作る）。

RQ0: バグ issue の終わり方の内訳と、本研究で分析できる修正の割合
RQ1: ゲーム資産を変更する修正 PR の割合（Wilson 区間とプロジェクト単位のクラスタブートストラップ）、種類、プロジェクト間の違い
RQ2: 資産を変更する修正 PR と変更しない修正 PR の比較
     (a) issue 作成 → PR 作成、(b) PR 作成 → マージ、(c) 変更ファイル数
     効果量 Cliff's δ（95% CI はクラスタブートストラップ）、プロジェクト内 δ の重み付き平均、
     プロジェクトで層別した Cox（変更ファイル数で調整、クラスタ頑健 SE）
感度分析: title だけで紐づいた PR を除く／資産 PR の多い上位 5 プロジェクトを除く／
          ゲームデータ・設定を資産に含める／捕捉率 90% 以上・50% 以上／
          リポジトリに資産ファイルを持つプロジェクトだけ（07b の repo_assets.csv。2026-11-20 追加）

入力: outputs/bugs.parquet, outputs/fix_prs.parquet, outputs/tables/link_coverage.csv, outputs/tables/repo_assets.csv（07b）
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter
from scipy.stats import mannwhitneyu
from statsmodels.stats.proportion import proportion_confint

from common import OUT, TAB, SEED

warnings.filterwarnings("ignore")
N_BOOT = 1000
METRICS = {"a_issue_to_pr": "days_issue_to_pr", "b_pr_to_merge": "days_pr_to_merge", "c_n_files": "n_files"}


# ---------------------------------------------------------------------------
def cliffs_delta(x: np.ndarray, y: np.ndarray) -> float:
    """δ = P(X>Y) − P(X<Y)。X が資産を変更する修正、Y が変更しない修正。"""
    if len(x) == 0 or len(y) == 0:
        return np.nan
    u = mannwhitneyu(x, y, alternative="two-sided").statistic  # x の U（同順位は 0.5）
    return 2 * u / (len(x) * len(y)) - 1


def within_project_delta(df: pd.DataFrame, col: str, group: str) -> float:
    """プロジェクトごとに δ を計算し、PR 数で重み付けした平均（両群が 1 件以上あるプロジェクトのみ）。"""
    ds, ws = [], []
    for _, g in df.groupby("project"):
        x, y = g.loc[g[group], col].dropna().values, g.loc[~g[group], col].dropna().values
        if len(x) and len(y):
            ds.append(cliffs_delta(x, y))
            ws.append(len(x) + len(y))
    return float(np.average(ds, weights=ws)) if ds else np.nan


def cluster_boot(df: pd.DataFrame, stat, n: int = N_BOOT, seed: int = SEED) -> tuple[float, float]:
    """プロジェクトを復元抽出して stat(df) の 95% パーセンタイル区間を返す。"""
    rng = np.random.default_rng(seed)
    projects = df.project.unique()
    groups = {p: g for p, g in df.groupby("project")}
    vals = []
    for _ in range(n):
        pick = rng.choice(projects, size=len(projects), replace=True)
        vals.append(stat(pd.concat([groups[p] for p in pick], ignore_index=True)))
    lo, hi = np.nanpercentile(vals, [2.5, 97.5])
    return float(lo), float(hi)


def strat_cox(df: pd.DataFrame, dur: str, group: str, adjust: bool) -> dict:
    d = df[[dur, group, "n_files", "project"]].dropna().copy()
    d = d[d[dur] >= 0]
    d["T"] = d[dur].clip(lower=1 / 1440)  # 0 日を避ける（1 分）
    d["E"] = 1  # 全員が PR 作成・マージに至っている（マージ済み PR だけが対象）
    d["asset"] = d[group].astype(int)
    d["cluster"] = d.project  # lifelines は cluster_col を取り出して使うので、層とは別の列にする
    cols = ["T", "E", "asset", "project", "cluster"]
    if adjust:
        d["log_files"] = np.log1p(d.n_files)
        cols.insert(3, "log_files")
    cph = CoxPHFitter()
    cph.fit(d[cols], "T", "E", strata=["project"], cluster_col="cluster", robust=True)
    r = cph.summary.loc["asset"]
    return {"HR": r["exp(coef)"], "lo": r["exp(coef) lower 95%"], "hi": r["exp(coef) upper 95%"],
            "p": r["p"], "n": len(d)}


# ---------------------------------------------------------------------------
def rq0() -> None:
    b = pd.read_parquet(OUT / "bugs.parquet", columns=["project", "number", "outcome"])
    prs = pd.read_parquet(OUT / "fix_prs.parquet")
    cov = pd.read_csv(TAB / "link_coverage.csv")
    from common import DATA, drop_dup
    ip = drop_dup(pd.read_parquet(DATA / "issue_pr.parquet")).drop_duplicates()
    linked = ip.merge(prs[["project", "pr_number", "in_main"]], on=["project", "pr_number"])
    b = b.merge(linked.groupby(["project", "issue_number"]).in_main.max().rename("analyzable_main").reset_index(),
                left_on=["project", "number"], right_on=["project", "issue_number"], how="left")
    b["linked_commit"] = b.analyzable_main.notna() & (b.outcome == "fixed_by_pr")
    b["analyzable"] = b.linked_commit & b.analyzable_main.fillna(False).astype(bool)
    scopes = {"all_projects": b, "fetched_83": b[b.project.isin(cov.project)],
              "main_78": b[b.project.isin(cov.loc[cov.coverage >= 0.7, "project"])]}
    rows = []
    for name, d in scopes.items():
        vc = d.outcome.value_counts()
        row = {"scope": name, "n_bugs": len(d), "n_projects": d.project.nunique()}
        for o in ["fixed_by_pr", "closed_other", "open", "closed_bulk_or_stale", "closed_invalid"]:
            row[f"share_{o}"] = vc.get(o, 0) / len(d)
        row["n_analyzable"] = int(d.analyzable.sum())
        row["share_analyzable"] = d.analyzable.mean()
        rows.append(row)
    pd.DataFrame(rows).to_csv(TAB / "rq0_outcomes.csv", index=False, encoding="utf-8-sig")
    # プロジェクトごとの fixed_by_pr の割合（バグ 30 件以上）
    pp = b.groupby("project").agg(n=("outcome", "size"), fixed=("outcome", lambda s: (s == "fixed_by_pr").mean()))
    pp = pp[pp.n >= 30]
    pd.DataFrame({"n_projects": [len(pp)], "median": [pp.fixed.median()], "q1": [pp.fixed.quantile(.25)],
                  "q3": [pp.fixed.quantile(.75)], "zero_share_projects": [(pp.fixed == 0).sum()]}) \
        .to_csv(TAB / "rq0_project_fixed_share.csv", index=False, encoding="utf-8-sig")


def rq1(m: pd.DataFrame, group: str = "has_asset") -> dict:
    k, n = int(m[group].sum()), len(m)
    wlo, whi = proportion_confint(k, n, method="wilson")
    blo, bhi = cluster_boot(m, lambda d: d[group].mean())
    return {"n_prs": n, "n_projects": m.project.nunique(), "k_asset": k, "share": k / n,
            "wilson_lo": wlo, "wilson_hi": whi, "boot_lo": blo, "boot_hi": bhi}


def rq2(m: pd.DataFrame, group: str = "has_asset", boot: bool = True) -> list[dict]:
    rows = []
    for name, col in METRICS.items():
        d = m[list(dict.fromkeys([col, group, "project", "n_files"]))].dropna()  # col が n_files のとき列が重複しないように
        if col == "days_issue_to_pr":
            d = d[d[col] >= 0]  # issue より前に作られた PR は (a) から除く
        x, y = d.loc[d[group], col].values, d.loc[~d[group], col].values
        row = {"metric": name, "n_asset": len(x), "n_other": len(y),
               "median_asset": np.median(x), "median_other": np.median(y),
               "delta": cliffs_delta(x, y), "delta_within": within_project_delta(d, col, group)}
        if boot:
            row["delta_lo"], row["delta_hi"] = cluster_boot(
                d, lambda z: cliffs_delta(z.loc[z[group], col].values, z.loc[~z[group], col].values))
            row["within_lo"], row["within_hi"] = cluster_boot(d, lambda z: within_project_delta(z, col, group), n=300)
        if col != "n_files":
            for adj in (False, True):
                c = strat_cox(m, col, group, adj)
                tag = "adj" if adj else "raw"
                row.update({f"HR_{tag}": c["HR"], f"HR_{tag}_lo": c["lo"], f"HR_{tag}_hi": c["hi"], f"HR_{tag}_p": c["p"]})
        rows.append(row)
    return rows


def main() -> None:
    rq0()
    prs = pd.read_parquet(OUT / "fix_prs.parquet")
    m = prs[prs.in_main].copy()

    # RQ1
    pd.DataFrame([rq1(m)]).to_csv(TAB / "rq1_asset_share.csv", index=False, encoding="utf-8-sig")
    types = [c for c in m.columns if c.startswith("has_") and c not in ("has_asset", "has_asset_or_data", "has_incidental")]
    t = pd.DataFrame({"type": [c[4:] for c in types], "n_prs": [int(m[c].sum()) for c in types]})
    t["share_of_all_prs"] = t.n_prs / len(m)
    t["share_of_asset_prs"] = t.n_prs / m.has_asset.sum()
    t.sort_values("n_prs", ascending=False).to_csv(TAB / "rq1_asset_types.csv", index=False, encoding="utf-8-sig")
    other = pd.DataFrame({"kind": ["code_only", "incidental_any", "game_data_any", "engine_aux_any", "asset_only_no_code"],
                          "n_prs": [int(((m.n_files == m.n_code)).sum()), int(m.has_incidental.sum()),
                                    int((m.n_game_data > 0).sum()), int((m.n_engine_aux > 0).sum()),
                                    int((m.has_asset & (m.n_code == 0)).sum())]})
    other["share"] = other.n_prs / len(m)
    other.to_csv(TAB / "rq1_other_kinds.csv", index=False, encoding="utf-8-sig")
    pj = m.groupby("project").agg(n_prs=("has_asset", "size"), n_asset=("has_asset", "sum"))
    pj["share"] = pj.n_asset / pj.n_prs
    pj.sort_values("n_asset", ascending=False).to_csv(TAB / "rq1_project_shares.csv", encoding="utf-8-sig")
    top5 = pj.sort_values("n_asset", ascending=False).head(5)
    pd.DataFrame({"projects_with_asset_pr": [(pj.n_asset > 0).sum()], "projects_both_ge5": [((pj.n_asset >= 5) & (pj.n_prs - pj.n_asset >= 5)).sum()],
                  "top5_share_of_asset_prs": [top5.n_asset.sum() / pj.n_asset.sum()],
                  "project_share_median_ge20": [pj.loc[pj.n_prs >= 20, "share"].median()],
                  "project_share_q1_ge20": [pj.loc[pj.n_prs >= 20, "share"].quantile(.25)],
                  "project_share_q3_ge20": [pj.loc[pj.n_prs >= 20, "share"].quantile(.75)],
                  "n_projects_ge20": [(pj.n_prs >= 20).sum()]}) \
        .to_csv(TAB / "rq1_project_summary.csv", index=False, encoding="utf-8-sig")

    # RQ1 の分母の確認: リポジトリに資産ファイルを持つプロジェクトだけの割合と、資産 PR の多い上位 3 プロジェクトの寄与
    ra = pd.read_csv(TAB / "repo_assets.csv")
    has = dict(zip(ra.repo.str.lower(), ra.has_repo_assets))
    m["has_repo_assets"] = m.project.str.lower().map(has).fillna(False).astype(bool)
    prs["has_repo_assets"] = prs.project.str.lower().map(has).fillna(False).astype(bool)
    top3 = pj.sort_values("n_asset", ascending=False).head(3).index
    rows = [{"subset": "all_main", **rq1(m)}, {"subset": "repo_has_assets", **rq1(m[m.has_repo_assets])},
            {"subset": "repo_no_assets", **rq1(m[~m.has_repo_assets])},
            {"subset": "top3_asset_projects", "projects": ";".join(top3), **rq1(m[m.project.isin(top3)])},
            {"subset": "excluding_top3", **rq1(m[~m.project.isin(top3)])}]
    pd.DataFrame(rows).to_csv(TAB / "rq1_by_repo_assets.csv", index=False, encoding="utf-8-sig")

    # 本文で使う件数（以前は一時スクリプトで数えていたもの）
    lc = pd.read_csv(TAB / "link_coverage.csv")
    pd.DataFrame([{"n_linked_prs_all": len(prs), "n_title_only_all": int(prs.title_only.sum()),
                   "share_title_only_all": prs.title_only.mean(),
                   "n_main_prs_fixing_several_bugs": int((m.n_bugs_fixed > 1).sum()),
                   "n_main_prs_before_issue": int(m.pr_before_issue.sum()),
                   "n_linked_pairs": int(lc.n_linked_pairs.sum()), "n_linked_pairs_remote": int(lc.n_linked_pairs_remote.sum()),
                   "share_linked_pairs_remote": lc.n_linked_pairs_remote.sum() / lc.n_linked_pairs.sum()}])         .to_csv(TAB / "misc_counts.csv", index=False, encoding="utf-8-sig")

    # RQ2（主な分析）
    pd.DataFrame(rq2(m)).to_csv(TAB / "rq2_main.csv", index=False, encoding="utf-8-sig")

    # 感度分析
    cov_all = prs.drop(columns=["has_repo_assets"])
    scen = {
        "main": (m, "has_asset"),
        "exclude_title_only": (m[~m.title_only], "has_asset"),
        "exclude_top5_asset_projects": (m[~m.project.isin(top5.index)], "has_asset"),
        "asset_includes_game_data": (m, "has_asset_or_data"),
        "coverage_ge_0.9": (cov_all[cov_all.coverage >= 0.9], "has_asset"),
        "coverage_ge_0.5": (cov_all[cov_all.coverage >= 0.5], "has_asset"),
        "repo_has_assets_only": (m[m.has_repo_assets], "has_asset"),
    }
    rows = []
    for name, (d, g) in scen.items():
        r1 = {"scenario": name, "n_prs": len(d), "n_projects": d.project.nunique(), "asset_share": d[g].mean()}
        for r in rq2(d, g, boot=False):
            for k in ["delta", "delta_within", "HR_raw", "HR_raw_lo", "HR_raw_hi", "HR_adj", "HR_adj_lo", "HR_adj_hi"]:
                if k in r:
                    r1[f"{r['metric']}_{k}"] = r[k]
        rows.append(r1)
    pd.DataFrame(rows).to_csv(TAB / "rq2_sensitivity.csv", index=False, encoding="utf-8-sig")

    print(pd.read_csv(TAB / "rq0_outcomes.csv").T.to_string())
    print(pd.read_csv(TAB / "rq1_asset_share.csv").T.to_string())
    print(pd.read_csv(TAB / "rq2_main.csv").T.to_string())
    print(pd.read_csv(TAB / "rq2_sensitivity.csv").T.to_string())


if __name__ == "__main__":
    main()
