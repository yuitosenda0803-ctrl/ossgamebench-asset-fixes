"""分類の精度確認（目視）の集計と、見つかった誤分類を直した場合の再計算。

入力: outputs/tables/validation_file_sample.csv（評価用 110 件）、validation_bug_sample_blind.csv（50 件。本人が一次判定を見ずに判定）、
      validation_bug_sample.csv（一次判定）、validation_link_sample.csv
      your_judgement が空欄の行は、claude_judgement（一次判定）に本人が同意したものとして扱う。
      一次判定の「誤（正しくは X）」「誤?（正しくは X）」は、正解を X とする。
出力: outputs/tables/validation_file_accuracy.csv … 分類（スクリプトの判定）ごとの適合率と Wilson 95% CI
      outputs/tables/validation_bug_accuracy.csv  … バグ判定の適合率（本人が一次判定を見ずに判定した結果）
      outputs/tables/validation_corrected.csv     … 誤分類の拡張子を直した場合の RQ1・RQ2（主な対象 78 プロジェクト）
      outputs/tables/validation_link_accuracy.csv … 紐づけの確認: 無作為の 60 PR で、紐づけた変更ファイル数と GitHub の changed_files の一致
                                                    （validation_link_sample.csv。github_changed_files は gh api で取得して記入した値）
注意 2: 評価用 110 件は 2026-11-20 の紐づけ修正（取り込み用マージの除外）の前に抽出した。分類の正誤はファイルの
      パスと中身だけで決まるので集計は 110 件のまま行い、修正後の対象に残る件数（in_population）を別に示す。
注意: 抽出は分類ごとに件数を決めた層別抽出で、二通りに読める拡張子（def, ts, map, obj）を 10 件追加しているので、
      生の正解率は全ファイルの正解率ではない。全体の値は、分類ごとの適合率を分類の全件数で重み付けした参考値。
"""
from __future__ import annotations

import importlib.util
import re

import numpy as np
import pandas as pd
from statsmodels.stats.proportion import proportion_confint

from common import OUT, TAB

spec = importlib.util.spec_from_file_location("rq", __file__.replace("10_validation.py", "08_theme_rq.py"))
rq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rq)


def judged(d: pd.DataFrame) -> pd.Series:
    """本人の判定があればそれを、無ければ一次判定を使う。"""
    yours = d.your_judgement.astype("string").str.strip()
    return yours.where(yours.notna() & (yours != ""), d.claude_judgement.astype(str))


def file_accuracy(main_files: pd.DataFrame) -> pd.DataFrame:
    d = pd.read_csv(TAB / "validation_file_sample.csv", encoding="utf-8-sig")
    j = judged(d)
    ok = j.str.startswith("正") | (j == d.category)
    rows = []
    pop = main_files.category.value_counts()
    for c, g in d.assign(ok=ok).groupby("category"):
        lo, hi = proportion_confint(g.ok.sum(), len(g), method="wilson")
        rows.append({"category": c, "n_checked": len(g), "n_correct": int(g.ok.sum()), "precision": g.ok.mean(),
                     "wilson_lo": lo, "wilson_hi": hi, "n_population": int(pop.get(c, 0))})
    out = pd.DataFrame(rows)
    w = (out.precision * out.n_population).sum() / out.n_population.sum()
    out = pd.concat([out, pd.DataFrame([{"category": "ALL (raw)", "n_checked": len(d), "n_correct": int(ok.sum()),
                                         "precision": ok.mean()},
                                        {"category": "ALL (weighted by population)", "precision": w}])])
    keys = main_files[["project", "pr_number", "file"]].drop_duplicates().assign(in_pop=True)
    inpop = d.merge(keys, on=["project", "pr_number", "file"], how="left").in_pop.fillna(False).astype(bool)
    out.loc[out.category == "ALL (raw)", "in_population"] = int(inpop.sum())
    a = d.assign(ok=ok)[inpop & (d.category == "asset")]
    out = pd.concat([out, pd.DataFrame([{"category": "asset (still in corrected population)", "n_checked": len(a),
                                         "n_correct": int(a.ok.sum()), "precision": a.ok.mean()}])])
    out.to_csv(TAB / "validation_file_accuracy.csv", index=False, encoding="utf-8-sig")
    errs = d.loc[~ok, ["ext", "category"]].assign(truth=j[~ok].map(lambda s: (re.findall(r"正しくは ([a-z_]+)", s) or [s])[0]))
    print(errs.to_string())
    return out


def bug_accuracy() -> pd.DataFrame:
    """バグ判定の確認。2026-11-20 に本人が一次判定を見ずに判定した validation_bug_sample_blind.csv を使う
    （「バグ」「バグでない」「不明」）。一次判定（validation_bug_sample.csv）は 50 件すべて「バグ」。
    相談して決めた 4 件（triplea#8168、fheroes2#10020、18xx#4836、fheroes2#6202）を含む。"""
    d = pd.read_csv(TAB / "validation_bug_sample_blind.csv", encoding="utf-8-sig")
    j = d.your_judgement.astype(str).str.strip()
    n_bug, n_not, n_unk = int((j == "バグ").sum()), int((j == "バグでない").sum()), int((j == "不明").sum())
    lo, hi = proportion_confint(n_bug, n_bug + n_not, method="wilson")
    first = pd.read_csv(TAB / "validation_bug_sample.csv", encoding="utf-8-sig")
    agree = int(((j == "バグ") & first.claude_judgement.astype(str).str.startswith("正")).sum())
    out = pd.DataFrame([{"n_checked": len(d), "n_bug": n_bug, "n_not_bug": n_not, "n_undecided": n_unk,
                         "precision_decided": n_bug / (n_bug + n_not), "wilson_lo": lo, "wilson_hi": hi,
                         "precision_if_undecided_wrong": n_bug / len(d), "agree_with_first_pass": agree,
                         "n_discussed_with_claude": 4}])
    out.to_csv(TAB / "validation_bug_accuracy.csv", index=False, encoding="utf-8-sig")
    return out


def link_accuracy() -> pd.DataFrame:
    d = pd.read_csv(TAB / "validation_link_sample.csv", encoding="utf-8-sig").dropna(subset=["github_changed_files"])
    diff = (d.n_files - d.github_changed_files).abs()
    out = pd.DataFrame([{"n_sampled": len(pd.read_csv(TAB / "validation_link_sample.csv")), "n_checked": len(d),
                         "n_exact": int((diff == 0).sum()), "n_within_1": int((diff <= 1).sum()),
                         "max_abs_diff": diff.max(), "n_more_than_github": int((d.n_files > d.github_changed_files).sum())}])
    out.to_csv(TAB / "validation_link_accuracy.csv", index=False, encoding="utf-8-sig")
    return out


def corrected(m: pd.DataFrame, main_files: pd.DataFrame) -> pd.DataFrame:
    """目視で誤りと分かった拡張子だけを直す: def は資産から外す、shp・lmp は資産に入れる。"""
    f = main_files
    is_asset = ((f.category == "asset") & (f.ext != "def")) | f.ext.isin(["shp", "lmp"])
    flag = f.assign(a=is_asset).groupby(["project", "pr_number"]).a.any().rename("has_asset_corr").reset_index()
    mm = m.merge(flag, on=["project", "pr_number"], how="left")
    mm["has_asset_corr"] = mm.has_asset_corr.fillna(False).astype(bool)
    r1 = rq.rq1(mm, "has_asset_corr")
    row = {"scenario": "def_not_asset__shp_lmp_asset", "n_prs": len(mm), "k_asset": int(mm.has_asset_corr.sum()),
           "share": r1["share"], "wilson_lo": r1["wilson_lo"], "wilson_hi": r1["wilson_hi"],
           "boot_lo": r1["boot_lo"], "boot_hi": r1["boot_hi"],
           "moved_out": int((mm.has_asset & ~mm.has_asset_corr).sum()), "moved_in": int((~mm.has_asset & mm.has_asset_corr).sum())}
    for r in rq.rq2(mm, "has_asset_corr", boot=False):
        for k in ["delta", "HR_raw", "HR_raw_lo", "HR_raw_hi", "HR_adj", "HR_adj_lo", "HR_adj_hi"]:
            if k in r:
                row[f"{r['metric']}_{k}"] = r[k]
    out = pd.DataFrame([row])
    out.to_csv(TAB / "validation_corrected.csv", index=False, encoding="utf-8-sig")
    return out


def main() -> None:
    prs = pd.read_parquet(OUT / "fix_prs.parquet")
    m = prs[prs.in_main].copy()
    files = pd.read_parquet(OUT / "fix_files.parquet")
    main_files = files.merge(m[["project", "pr_number"]], on=["project", "pr_number"])
    print(file_accuracy(main_files).round(3).to_string(index=False))
    print(bug_accuracy().round(3).to_string(index=False))
    print(link_accuracy().to_string(index=False))
    print(corrected(m, main_files).T.to_string())


if __name__ == "__main__":
    main()
