"""前処理: バグ issue を抽出し、分析用の特徴量テーブルを作る。

出力:
  outputs/projects.parquet  プロジェクト単位の属性（エンジン、規模など）
  outputs/issues_all.parquet 全 issue の最小列（バグ判定フラグつき）
  outputs/bugs.parquet      バグ issue と特徴量（RQ1〜3 の入力）
  outputs/tables/label_coverage.csv  判定に使われたラベルの内訳
  outputs/tables/bug_sample_for_validation.csv  妥当性確認用の無作為標本
"""
from __future__ import annotations

import re
import warnings

import numpy as np
import pandas as pd

from common import (DATA, OUT, TAB, SEED, LABEL_TOPICS_RE, classify_engine, is_bug_label, is_engine_project,
                    is_invalid_label, is_stale_label, drop_dup, norm_label, primary_language)

warnings.filterwarnings("ignore", message="This pattern is interpreted")

# ---------------------------------------------------------------------------
print("[1/6] project")
proj = pd.read_parquet(DATA / "project.parquet",
                       columns=["project", "is_fork", "languages", "topics", "created_at", "size",
                                "readme_content"])
proj = drop_dup(proj)
eng = proj.apply(lambda r: classify_engine(r.languages, r.topics, r.readme_content), axis=1)
proj["engine"] = [e for e, _ in eng]
proj["engine_evidence"] = [s for _, s in eng]
proj["primary_language"] = proj.languages.map(primary_language)
proj["is_engine_project"] = proj.topics.map(is_engine_project)
proj = proj.rename(columns={"created_at": "repo_created_at", "size": "repo_size_kb"})
proj = proj.drop(columns=["readme_content"])

# ---------------------------------------------------------------------------
print("[2/6] issue")
iss = drop_dup(pd.read_parquet(DATA / "issue.parquet"))
iss["created_at"] = pd.to_datetime(iss.created_at, utc=True)
iss["closed_at"] = pd.to_datetime(iss.closed_at, utc=True)
iss["labels"] = iss.labels.map(lambda x: list(x) if x is not None else [])
iss["is_bug"] = iss.labels.map(lambda ls: any(is_bug_label(l) for l in ls))
iss["is_invalid"] = iss.labels.map(lambda ls: any(is_invalid_label(l) for l in ls))
iss["is_stale"] = iss.labels.map(lambda ls: any(is_stale_label(l) for l in ls))
iss["n_labels"] = iss.labels.map(len)

# データ収集の打ち切り時点: データ中の最新タイムスタンプ
CUTOFF = max(iss.created_at.max(), iss.closed_at.max())
print("  cutoff =", CUTOFF)

# 一括クローズ（同一プロジェクト・同一日に大量に閉じられた issue）を検出する。
# 移行・整理作業による機械的なクローズで、「解決」とは言えない可能性が高い。
closed_day = iss.closed_at.dt.floor("D")
day_cnt = iss.groupby([iss.project, closed_day]).number.transform("size")
iss["bulk_closed"] = iss.closed_at.notna() & (day_cnt >= 50)

# ラベル内訳（判定の透明性のため）
lab = iss[["labels"]].explode("labels").dropna()
lab["norm"] = lab["labels"].map(norm_label)
lab["bug"] = lab["labels"].map(is_bug_label)
lab["invalid"] = lab["labels"].map(is_invalid_label)
cov = (lab.groupby("norm").agg(n=("bug", "size"), bug=("bug", "first"), invalid=("invalid", "first"))
       .sort_values("n", ascending=False))
cov[cov.bug].to_csv(TAB / "label_coverage_bug.csv", encoding="utf-8-sig")
cov[cov.invalid].to_csv(TAB / "label_coverage_invalid.csv", encoding="utf-8-sig")

# ---------------------------------------------------------------------------
print("[3/6] comments")
com = drop_dup(pd.read_parquet(DATA / "issue_comment.parquet", columns=["project", "issue_number", "contributor"]))
com["is_bot"] = com.contributor.fillna("").str.contains(r"\[bot\]$|-bot$|bot$", case=False, regex=True)
cagg = com.groupby(["project", "issue_number"]).agg(
    n_comments=("contributor", "size"),
    n_bot_comments=("is_bot", "sum"),
    n_commenters=("contributor", "nunique"),
).reset_index().rename(columns={"issue_number": "number"})

# ---------------------------------------------------------------------------
print("[4/6] linked PRs")
pr = pd.read_parquet(DATA / "pr.parquet", columns=["project", "number", "contributor", "state",
                                                    "created_at", "merged_at"])
pr = drop_dup(pr)
pr["merged_at"] = pd.to_datetime(pr.merged_at, utc=True)
# issue_pr には完全に重複した行がある（78,060 行中 8,961 行。2026-11-18 判明）ので除く
ip = drop_dup(pd.read_parquet(DATA / "issue_pr.parquet")).drop_duplicates()
link = ip.merge(pr.rename(columns={"number": "pr_number", "contributor": "pr_author",
                                   "created_at": "pr_created_at"}),
                on=["project", "pr_number"], how="left")
link["merged_pr_number"] = link.pr_number.where(link.merged_at.notna())
lagg = link.groupby(["project", "issue_number"]).agg(
    n_linked_prs=("pr_number", "nunique"),
    n_merged_prs=("merged_pr_number", "nunique"),
    first_merge_at=("merged_at", "min"),
).reset_index().rename(columns={"issue_number": "number"})

# 報告者が「報告の時点で」そのプロジェクトにマージ済み PR を持つ（＝コード貢献者）かどうか。
# 以前は報告より後のマージも数えていた（時間のリーク。2026-11-18 修正）。
first_merge_by_author = (pr[pr.merged_at.notna()].dropna(subset=["contributor"])
                         .groupby(["project", "contributor"]).merged_at.min())

# プロジェクト規模の指標
psize = pd.DataFrame({
    "n_issues": iss.groupby("project").size(),
    "n_bug_issues": iss.groupby("project").is_bug.sum(),
    "n_prs": pr.groupby("project").size(),
    "n_code_contributors": pr[pr.merged_at.notna()].groupby("project").contributor.nunique(),
}).fillna(0).reset_index().rename(columns={"index": "project"})
proj = proj.merge(psize, on="project", how="left")
proj["bug_label_share"] = proj.n_bug_issues / proj.n_issues.replace(0, np.nan)
proj.to_parquet(OUT / "projects.parquet", index=False)

iss[["project", "number", "state", "created_at", "closed_at", "is_bug", "is_invalid", "is_stale",
     "bulk_closed", "n_labels", "title"]].to_parquet(OUT / "issues_all.parquet", index=False)

# ---------------------------------------------------------------------------
print("[5/6] bug features")
b = iss[iss.is_bug].copy()
b = b.merge(cagg, on=["project", "number"], how="left").merge(lagg, on=["project", "number"], how="left")
for c in ["n_comments", "n_bot_comments", "n_commenters", "n_linked_prs", "n_merged_prs"]:
    b[c] = b[c].fillna(0).astype(int)
b["n_human_comments"] = b.n_comments - b.n_bot_comments
b["has_linked_pr"] = b.n_linked_prs > 0
b["has_merged_pr"] = b.n_merged_prs > 0
_fm = pd.Series(first_merge_by_author.reindex(pd.MultiIndex.from_arrays([b.project, b.contributor])).values, index=b.index)
b["reporter_is_contributor"] = (pd.to_datetime(_fm, utc=True) < b.created_at).fillna(False).astype(bool)

body = b.body.fillna("")
b["body_len"] = body.str.len()
b["title_len"] = b.title.fillna("").str.len()
b["has_code_block"] = body.str.contains("```", regex=False)
b["has_stacktrace"] = body.str.contains(
    r"traceback|exception|stack ?trace|segmentation fault|sigsegv|\bat [\w.$<>]+\([\w.]+:\d+\)|nullreference",
    case=False, regex=True)
b["has_image"] = body.str.contains(
    r"!\[|\.(png|jpe?g|gif|webp|mp4|webm)\b|user-attachments|youtu\.?be", case=False, regex=True)
b["has_repro_steps"] = body.str.contains(
    r"steps to reproduce|to reproduce|reproduction steps|repro steps|how to reproduce", case=False, regex=True)
b["has_version_info"] = body.str.contains(
    r"\bversion\b|\bv\d+\.\d+|\bcommit\b|\bbuild\b|\bos\b|operating system", case=False, regex=True)

lab_text = b.labels.map(lambda ls: " | ".join(norm_label(l) for l in ls))
for k, rx in LABEL_TOPICS_RE.items():
    b[f"lab_{k}"] = lab_text.str.contains(rx)

# 解決期間（日）と打ち切りフラグ
b["closed"] = b.closed_at.notna()
end = b.closed_at.fillna(CUTOFF)
b["duration_days"] = (end - b.created_at).dt.total_seconds() / 86400
b["duration_days"] = b.duration_days.clip(lower=1 / 1440)  # 0 日を避ける（1分）
b["created_year"] = b.created_at.dt.year
b["time_to_merge_days"] = (b.first_merge_at - b.created_at).dt.total_seconds() / 86400

# 解決の種類（close 理由は収集されていないため代理指標で分類）
def outcome(r):
    if not r.closed:
        return "open"
    if r.is_invalid:
        return "closed_invalid"
    if r.has_merged_pr:
        return "fixed_by_pr"
    if r.bulk_closed or r.is_stale:
        return "closed_bulk_or_stale"
    return "closed_other"

b["outcome"] = b.apply(outcome, axis=1)
b = b.merge(proj[["project", "engine", "engine_evidence", "primary_language", "repo_size_kb", "n_issues",
                  "n_prs", "n_code_contributors", "is_fork", "is_engine_project"]], on="project", how="left")

keep = [c for c in b.columns if c not in ("body",)]
b[keep].to_parquet(OUT / "bugs.parquet", index=False)

# ---------------------------------------------------------------------------
print("[6/6] validation sample")
rng = np.random.default_rng(SEED)
s_bug = b.sample(100, random_state=SEED)[["project", "number", "title", "labels"]]
s_bug["set"] = "classified_bug"
nb = iss[~iss.is_bug & (iss.n_labels > 0)].sample(100, random_state=SEED)[["project", "number", "title", "labels"]]
nb["set"] = "labeled_non_bug"
pd.concat([s_bug, nb]).assign(labels=lambda d: d.labels.map(lambda ls: "; ".join(ls)),
                              url=lambda d: "https://github.com/" + d.project + "/issues/" + d.number.astype(str)
                              ).to_csv(TAB / "bug_sample_for_validation.csv", index=False, encoding="utf-8-sig")

# 要約
summary = {
    "issues_total": len(iss),
    "issues_labeled": int((iss.n_labels > 0).sum()),
    "bug_issues": int(iss.is_bug.sum()),
    "bug_invalid": int((iss.is_bug & iss.is_invalid).sum()),
    "projects_total": iss.project.nunique(),
    "projects_with_bug": int(b.project.nunique()),
    "cutoff": str(CUTOFF),
}
pd.Series(summary).to_csv(TAB / "preprocess_summary.csv", header=["value"], encoding="utf-8-sig")
print(pd.Series(summary).to_string())
print(b.outcome.value_counts().to_string())
print(proj.engine.value_counts().to_string())
print(proj.groupby(["engine", "engine_evidence"]).size().to_string())
