"""ダウンロードした OSSGameBench.zip（Zenodo record 17553539, 65.8GB）から、分析に使うファイルだけを取り出す。

使い方: python 00_extract_zip.py <OSSGameBench.zip のパス> [--out <出力先>]
出力:  分析対象データ/*.parquet（表 8 ファイル）
       分析対象データ/commits/<owner>__<repo>.parquet（analysed_projects.txt の 83 プロジェクト）

zip の中のコミットファイル名は commits|owner|repo.parquet で、「|」は Windows のファイル名に使えない。
そのため zip を普通に展開せず、このスクリプトで「|」を「__」に置き換えた名前で書き出す。
zip を展開する必要はない（zip のまま読む）。06・07b はこの名前のファイルを読む。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

from common import ANALYSED_PROJECTS, DATA

TABLES = ["project", "issue", "issue_comment", "pr", "pr_comment", "issue_pr", "pr_commit", "commit_metadata"]
COMMIT_PREFIX = "ossgamebench/commits/commits|"


def extract(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size == info.file_size:
        print(f"skip (exists): {dest.name}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    with zf.open(info) as src, open(tmp, "wb") as out:
        shutil.copyfileobj(src, out, 8 * 1024 * 1024)
    with open(tmp, "rb") as f:
        head = f.read(4)
        f.seek(-4, 2)
        tail = f.read(4)
    if tmp.stat().st_size != info.file_size or head != b"PAR1" or tail != b"PAR1":
        tmp.unlink()
        raise RuntimeError(f"verification failed: {info.filename}")
    tmp.replace(dest)
    print(f"OK {dest.name} ({info.file_size / 1e6:.1f} MB)", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip", type=Path, help="OSSGameBench.zip のパス")
    ap.add_argument("--out", type=Path, default=DATA, help="出力先（既定: 分析対象データ/）")
    a = ap.parse_args()

    with zipfile.ZipFile(a.zip) as zf:
        infos = {i.filename.lower(): i for i in zf.infolist() if not i.is_dir()}
        missing = []
        for t in TABLES:
            info = infos.get(f"ossgamebench/{t}.parquet")
            if info is None:
                missing.append(f"{t}.parquet")
                continue
            extract(zf, info, a.out / f"{t}.parquet")
        # zip 内の名前 commits|owner|repo.parquet → owner__repo.parquet（大文字小文字は区別しない）
        commits = {k[len(COMMIT_PREFIX):].removesuffix(".parquet").replace("|", "__"): i
                   for k, i in infos.items() if k.startswith(COMMIT_PREFIX) and k.endswith(".parquet")}
        for stem in ANALYSED_PROJECTS:
            info = commits.get(stem.lower())
            if info is None:
                missing.append(f"commits|{stem.replace('__', '|')}.parquet")
                continue
            extract(zf, info, a.out / "commits" / f"{stem}.parquet")
    if missing:
        print(f"zip に見つからないファイルが {len(missing)} 件あります:", *missing, sep="\n  ")
        return 1
    print(f"done: 表 {len(TABLES)} ファイル、コミット {len(ANALYSED_PROJECTS)} ファイル -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
