"""プロジェクトのリポジトリにゲーム資産のファイルがどれだけあるか（RQ1 の分母の確認）。

採点役サブエージェントの指摘（2026-11-20）: エンジンの再実装など、資産をリポジトリに置かないプロジェクトでは
資産を変更する修正 PR は構造的に 0 件になり、全体の割合が標本の構成を映すだけになりうる。そこで、
コミット履歴に現れたファイル（全コミットの files の和集合）を 07 と同じ規則で分類し、プロジェクトごとに数える。

定義:
  資産を持つプロジェクト: 履歴に現れた異なるゲーム資産ファイルが ASSET_MIN 件以上
出力: outputs/tables/repo_assets.csv（プロジェクト × 異なるファイル数・異なる資産ファイル数・資産を持つか）
注意: エンジンの推定（*.unity や project.godot の有無）は、履歴に残った試作ファイルで誤判定する
      （freeorion の godot/ 実験クライアント、robusttoolbox の旧 *.godot/ など）ため 2026-11-20 にやめた。
"""
from __future__ import annotations

import importlib.util

import pandas as pd

from common import DATA, TAB, analysed_commit_files

ASSET_MIN = 20

spec = importlib.util.spec_from_file_location("cls", __file__.replace("07b_repo_assets.py", "07_classify_files.py"))
cls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cls)


def main() -> None:
    rows = []
    for f in analysed_commit_files():
        c = pd.read_parquet(f, columns=["repo", "files"])
        paths = set()
        for fl in c.files:
            paths.update(fl)
        n_asset = sum(cls.classify(p)[0] == "asset" for p in paths)
        rows.append({"repo": c.repo.iloc[0], "n_paths": len(paths), "n_asset_paths": n_asset})
    out = pd.DataFrame(rows)
    out["has_repo_assets"] = out.n_asset_paths >= ASSET_MIN
    out.to_csv(TAB / "repo_assets.csv", index=False, encoding="utf-8-sig")
    print(out.sort_values("n_asset_paths").to_string(index=False))
    print(out.has_repo_assets.value_counts())


if __name__ == "__main__":
    main()
