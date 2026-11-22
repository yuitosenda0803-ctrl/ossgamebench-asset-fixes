"""バグ修正 PR とリポジトリのコミットを紐づけ、変更ファイルを取り出す。

紐づけ方法（いずれかに該当すれば紐づける）:
  sha   : pr_commit の SHA がリポジトリのコミットに存在する（通常マージ・fast-forward）
  squash: コミットメッセージ 1 行目に "(#PR番号)" がある（多くのプロジェクトの squash マージで見られる形式。GitHub の既定かは未確認。rebase マージでは通常付かない）
  merge : コミットメッセージが "Merge pull request #PR番号" で始まる（マージコミット）
  title : PR タイトルとコミットメッセージ 1 行目が完全一致（rebase マージで PR 番号が残らない場合の補助）。
          誤一致を避けるため、タイトル 15 文字以上・プロジェクト内でそのタイトルのコミットが 1 件のみ・
          コミット日時がマージ日時 +2 日以内、を条件とする。
"Fixes #123" のような本文中の参照は issue 番号の可能性があるため使わない。

取り込み用マージの除外（2026-11-20、目視確認で発見）:
  PR の作業中に本流を取り込んだコミット（"Merge branch 'master' into …" など）は、本流側の無関係な変更を
  ファイル一覧に含む。そこで (1) sha で紐づいたコミットのうちメッセージ 1 行目が "Merge " で始まるものは使わない。
  (2) squash か merge で紐づいたコミットがある PR は、それだけを使う（PR 全体の差分を 1 コミットで表すため）。

出力:
  outputs/fix_commits.parquet          修正 PR × コミット × 変更ファイル（1行=1ファイル）
  outputs/tables/link_coverage.csv     プロジェクト別の捕捉率（修正 PR のうち変更ファイルが取れた割合）
"""
from __future__ import annotations

import re

import pandas as pd

from common import DATA, OUT, TAB, analysed_commit_files

COMMITS = DATA / "commits"
RX_SQUASH = re.compile(r"\(#(\d+)\)")
RX_MERGE = re.compile(r"^Merge pull request #(\d+)")

bugs = pd.read_parquet(OUT / "bugs.parquet", columns=["project", "number", "outcome"])
fixed = bugs[bugs.outcome == "fixed_by_pr"]
ip = pd.read_parquet(DATA / "issue_pr.parquet")
pr = pd.read_parquet(DATA / "pr.parquet", columns=["project", "number", "title", "merged_at"])
pr["merged_at"] = pd.to_datetime(pr.merged_at, utc=True)
fix_pr = (ip.merge(fixed, left_on=["project", "issue_number"], right_on=["project", "number"])
          .merge(pr[pr.merged_at.notna()], left_on=["project", "pr_number"], right_on=["project", "number"],
                 suffixes=("", "_pr")))[["project", "issue_number", "pr_number"]].drop_duplicates()

pc = pd.read_parquet(DATA / "pr_commit.parquet")

rows, cov = [], []
for f in analysed_commit_files():
    c = pd.read_parquet(f, columns=["repo", "sha", "created_at", "message", "files"])
    project = c.repo.iloc[0]
    # 大文字小文字の違いを吸収してデータセット側のプロジェクト名に合わせる
    names = {p.lower(): p for p in fix_pr.project.unique()}
    project = names.get(project.lower(), project)
    targets = fix_pr[fix_pr.project == project]
    if targets.empty:
        continue
    want = set(targets.pr_number)
    c["sha40"] = c.sha.str.split().str[0]
    first = c.message.fillna("").str.split("\n").str[0]

    links = []
    # sha
    p_sha = pc[(pc.project == project) & pc.pr_number.isin(want)]
    m = p_sha.merge(c[["sha40"]], left_on="commit_sha", right_on="sha40")
    links.append(pd.DataFrame({"pr_number": m.pr_number, "sha40": m.sha40, "method": "sha"}))
    # squash / merge
    for method, rx in (("squash", RX_SQUASH), ("merge", RX_MERGE)):
        ex = first.str.findall(rx)
        e = pd.DataFrame({"sha40": c.sha40, "n": ex}).explode("n").dropna()
        e["pr_number"] = e.n.astype(int)
        e = e[e.pr_number.isin(want)]
        links.append(pd.DataFrame({"pr_number": e.pr_number, "sha40": e.sha40, "method": method}))
    # title
    t = pr[(pr.project == project) & pr.number.isin(want)][["number", "title", "merged_at"]].copy()
    t["key"] = t.title.fillna("").str.strip().str.lower()
    t = t[t.key.str.len() >= 15]
    ck = pd.DataFrame({"sha40": c.sha40, "key": first.str.strip().str.lower(),
                       "cdate": pd.to_datetime(c.created_at, utc=True, format="mixed")})
    ck = ck[ck.groupby("key").key.transform("size") == 1]
    e = t.merge(ck, on="key")
    e = e[e.cdate <= e.merged_at + pd.Timedelta(days=2)]
    links.append(pd.DataFrame({"pr_number": e.number, "sha40": e.sha40, "method": "title"}))
    # 同じコミットが複数の方法で紐づいたら squash / merge を優先して残す（自分の PR のマージコミットを除外しないため）
    lk = pd.concat(links[1:] + links[:1]).drop_duplicates(["pr_number", "sha40"])
    # 取り込み用マージの除外（冒頭の説明を参照）
    back = lk.sha40.isin(c.sha40[first.str.startswith("Merge ")]) & (lk.method == "sha")
    n_back = int(back.sum())
    lk = lk[~back]
    c["remote"] = c.sha.str.split().str[1].fillna("").str.startswith("remotes/")
    lk = lk.merge(c[["sha40", "created_at", "files", "remote"]], on="sha40")
    lk["n_files"] = lk.files.map(len)
    # ファイル一覧のある squash / merge コミットがあれば、その PR はそれだけを使う
    whole = lk.method.isin(["squash", "merge"]) & (lk.n_files > 0)
    lk = lk[whole | ~lk.pr_number.isin(lk.pr_number[whole])]
    ok = lk[lk.n_files > 0]
    cov.append({
        "project": project, "fix_prs": len(want), "linked_prs": ok.pr_number.nunique(),
        "coverage": ok.pr_number.nunique() / len(want),
        "by_sha": lk[lk.method == "sha"].pr_number.nunique(),
        # 修正 PR の pr_commit の SHA のうち、リポジトリのコミットに存在する割合
        "sha_commit_match": (p_sha.commit_sha.isin(c.sha40).mean() if len(p_sha) else float("nan")),
        "by_squash": lk[lk.method == "squash"].pr_number.nunique(),
        "by_merge": lk[lk.method == "merge"].pr_number.nunique(),
        "by_title": lk[lk.method == "title"].pr_number.nunique(),
        "merge_commits_without_files": int((lk[lk.method == "merge"].n_files == 0).sum()),
        "n_back_merge_commits_dropped": n_back,
        "n_linked_pairs": int((lk.n_files > 0).sum()), "n_linked_pairs_remote": int(lk[lk.n_files > 0].remote.sum()),
        "n_commits_repo": len(c),
    })
    ok = ok.assign(project=project).explode("files").rename(columns={"files": "file"})
    rows.append(ok[["project", "pr_number", "sha40", "method", "created_at", "file"]])

cov = pd.DataFrame(cov).sort_values("coverage", ascending=False)
cov.to_csv(TAB / "link_coverage.csv", index=False, encoding="utf-8-sig")
fc = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
fc.to_parquet(OUT / "fix_commits.parquet", index=False)

pd.set_option("display.width", 200)
print(cov.round(3).to_string(index=False))
for th in (0.5, 0.7, 0.9):
    s = cov[cov.coverage >= th]
    print(f"捕捉率 >= {th:.0%}: {len(s)} プロジェクト, 紐づいた修正 PR {s.linked_prs.sum()} 件")
print("修正 PR 合計:", cov.fix_prs.sum(), "紐づき:", cov.linked_prs.sum())
