"""テーマA の図と、層別 Cox の比例ハザード仮定の検定。

出力: outputs/figs/figA1_rq2_ecdf.(png|pdf)   … RQ2 (a)(b)(c) の分布（累積分布、対数軸）
      outputs/figs/figA2_rq2_hr.(png|pdf)     … 調整なし・調整後のハザード比（主な分析と感度分析）
      outputs/tables/rq2_ph_test.csv           … 比例ハザード仮定の検定（Schoenfeld 残差、rank 変換）
色は 2 色（青＝資産を変更する修正、橙＝変更しない修正）。色だけに頼らないよう線種も変える。
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter
from lifelines.statistics import proportional_hazard_test

from common import OUT, TAB, FIG, setup_matplotlib

warnings.filterwarnings("ignore")
BLUE, ORANGE, INK, MUTED = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e"


def ph_test(m: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, col in {"a_issue_to_pr": "days_issue_to_pr", "b_pr_to_merge": "days_pr_to_merge"}.items():
        d = m[[col, "has_asset", "n_files", "project"]].dropna()
        d = d[d[col] >= 0].copy()
        d["T"] = d[col].clip(lower=1 / 1440)
        d["E"] = 1
        d["asset"] = d.has_asset.astype(int)
        d["log_files"] = np.log1p(d.n_files)
        for adj in (False, True):
            cols = ["T", "E", "asset"] + (["log_files"] if adj else []) + ["project"]
            cph = CoxPHFitter().fit(d[cols], "T", "E", strata=["project"])
            r = proportional_hazard_test(cph, d[cols], time_transform="rank").summary
            for cov, rr in r.iterrows():
                cov = cov[0] if isinstance(cov, tuple) else cov
                rows.append({"metric": name, "adjusted": adj, "covariate": cov,
                             "test_statistic": rr["test_statistic"], "p": rr["p"]})
    out = pd.DataFrame(rows)
    out.to_csv(TAB / "rq2_ph_test.csv", index=False, encoding="utf-8-sig")
    return out


def ecdf(ax, values, color, ls, label):
    v = np.sort(values)
    y = np.arange(1, len(v) + 1) / len(v)
    ax.step(v, y, where="post", color=color, lw=2, ls=ls, label=label)


def fig_ecdf(m: pd.DataFrame) -> None:
    plt = setup_matplotlib()
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.4), sharey=True)
    panels = [("days_issue_to_pr", "(a) Issue created → PR created (days)"),
              ("days_pr_to_merge", "(b) PR created → merged (days)"),
              ("n_files", "(c) Changed files")]
    for ax, (col, title) in zip(axes, panels):
        d = m[[col, "has_asset"]].dropna()
        if col == "days_issue_to_pr":
            d = d[d[col] >= 0]
        lo = 1 / 1440 if col != "n_files" else 1
        for flag, color, ls, lab in [(True, BLUE, "-", "Fix PRs changing game assets"),
                                     (False, ORANGE, "--", "Other fix PRs")]:
            v = d.loc[d.has_asset == flag, col].clip(lower=lo).values
            ecdf(ax, v, color, ls, lab)  # n はパネルで違う（(a) は issue より前の PR を除く）ので本文・キャプションに書く
            med = np.median(v)
            ax.axvline(med, color=color, lw=0.8, ls=ls, alpha=0.6)
        ax.set_xscale("log")
        ax.set_title(title, fontsize=8.5, color=INK)
        ax.grid(axis="y", color="#e5e4e0", lw=0.6)
        ax.tick_params(colors=MUTED, labelsize=7.5)
    axes[0].set_ylabel("Cumulative share", color=MUTED)
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=2, frameon=False, fontsize=8, bbox_to_anchor=(0.5, -0.08))
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    for ext in ("png", "pdf"):
        fig.savefig(FIG / f"figA1_rq2_ecdf.{ext}")
    plt.close(fig)


def fig_hr() -> None:
    plt = setup_matplotlib()
    s = pd.read_csv(TAB / "rq2_sensitivity.csv")
    names = {"main": "Main analysis (78 projects)", "exclude_title_only": "Excl. title-only links",
             "exclude_top5_asset_projects": "Excl. top-5 asset projects",
             "asset_includes_game_data": "Game data counted as asset",
             "coverage_ge_0.9": "Link coverage ≥ 90%", "coverage_ge_0.5": "Link coverage ≥ 50%",
             "repo_has_assets_only": "Repos with ≥ 20 asset files"}
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8), sharey=True)
    for ax, (key, title) in zip(axes, [("a_issue_to_pr", "(a) Issue created → PR created"),
                                       ("b_pr_to_merge", "(b) PR created → merged")]):
        y = np.arange(len(s))[::-1]
        for yi, (_, r) in zip(y, s.iterrows()):
            ax.plot([r[f"{key}_HR_raw_lo"], r[f"{key}_HR_raw_hi"]], [yi + 0.15] * 2, color=ORANGE, lw=2)
            ax.plot(r[f"{key}_HR_raw"], yi + 0.15, "o", color=ORANGE, ms=5, mec="#fcfcfb", mew=1)
            ax.plot([r[f"{key}_HR_adj_lo"], r[f"{key}_HR_adj_hi"]], [yi - 0.15] * 2, color=BLUE, lw=2)
            ax.plot(r[f"{key}_HR_adj"], yi - 0.15, "s", color=BLUE, ms=5, mec="#fcfcfb", mew=1)
        ax.axvline(1, color=MUTED, lw=0.8)
        ax.set_xscale("log")
        ax.set_xticks([0.6, 0.8, 1.0, 1.25])
        ax.set_xticklabels(["0.6", "0.8", "1.0", "1.25"])
        ax.xaxis.set_minor_formatter(plt.NullFormatter())
        ax.set_title(title, fontsize=8.5, color=INK)
        ax.set_xlabel("Hazard ratio (< 1: asset-changing PRs slower)", fontsize=7.5, color=MUTED)
        ax.tick_params(colors=MUTED, labelsize=7.5)
        ax.grid(axis="x", color="#e5e4e0", lw=0.6)
    axes[0].set_yticks(np.arange(len(s))[::-1])
    axes[0].set_yticklabels([names[n] for n in s.scenario], fontsize=7.5)
    from matplotlib.lines import Line2D
    handles = [Line2D([], [], color=ORANGE, marker="o", ls="-", label="Unadjusted (95% CI)"),
               Line2D([], [], color=BLUE, marker="s", ls="-", label="Adjusted for log(1 + changed files) (95% CI)")]
    fig.legend(handles=handles, loc="lower center", ncol=2, frameon=False, fontsize=8, bbox_to_anchor=(0.55, -0.08))
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    for ext in ("png", "pdf"):
        fig.savefig(FIG / f"figA2_rq2_hr.{ext}")
    plt.close(fig)


def main() -> None:
    prs = pd.read_parquet(OUT / "fix_prs.parquet")
    m = prs[prs.in_main]
    print(ph_test(m).to_string(index=False))
    fig_ecdf(m)
    fig_hr()


if __name__ == "__main__":
    main()
