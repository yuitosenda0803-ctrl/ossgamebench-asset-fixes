"""OSSGameBench zip から、指定プロジェクトのコミット parquet だけを HTTP Range で取得する。

使い方: python 05_fetch_commits.py owner/repo [owner/repo ...]
       python 05_fetch_commits.py --analysed   # レポートの 83 プロジェクト（analysed_projects.txt）。01 は不要
       python 05_fetch_commits.py --min-fixed 20 --max-mb 300   # 83 プロジェクトを選んだ条件（01 の後）
       python 05_fetch_commits.py --all   # 残り全部を 分析対象データ/commits_rest/ に取得（06 の対象外）
出力:  分析対象データ/commits/<owner>__<repo>.parquet

zip のセントラルディレクトリは 00_fetch_dataset.ps1 が作業ディレクトリに保存したものを使い、
無ければ取得する。作業ディレクトリは --work、環境変数 OSSGB_WORK、既定（C ドライブの data/OSSGameBench/_work）の順に決める。各エントリは deflate 圧縮なので、エントリ全体を並列チャンクで取得して展開する。
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import time
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from common import ANALYSED_PROJECTS, DATA, OUT

URL = "https://zenodo.org/api/records/17553539/files/OSSGameBench.zip/content"
ZIP_SIZE = 65816061871
WORK = Path(os.environ.get("OSSGB_WORK", r"C:\data\OSSGameBench\_work"))
DEST = DATA / "commits"
CHUNK = 8 * 1024 * 1024


def get_range(a: int, b: int, retries: int = 10) -> bytes:
    for i in range(retries):
        try:
            req = urllib.request.Request(URL, headers={"Range": f"bytes={a}-{b}"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            if len(data) == b - a + 1:
                return data
        except Exception as e:  # noqa: BLE001
            print(f"  retry {i + 1} ({a}-{b}): {e}", file=sys.stderr)
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"range {a}-{b} failed")


def central_directory() -> dict[str, tuple[int, int, int, int]]:
    cd_path = WORK / "central_dir.bin"
    if not cd_path.exists():
        WORK.mkdir(parents=True, exist_ok=True)
        tail = get_range(ZIP_SIZE - 65536, ZIP_SIZE - 1)
        loc = tail.rfind(b"PK\x06\x07")
        eocd = struct.unpack_from("<Q", tail, loc + 8)[0] - (ZIP_SIZE - 65536)
        size, off = struct.unpack_from("<QQ", tail, eocd + 40)
        cd_path.write_bytes(get_range(off, off + size - 1))
    b = cd_path.read_bytes()
    p, ent = 0, {}
    while p < len(b) and struct.unpack_from("<I", b, p)[0] == 0x02014B50:
        meth, = struct.unpack_from("<H", b, p + 10)
        csz, usz = struct.unpack_from("<II", b, p + 20)
        nl, xl, cl = struct.unpack_from("<HHH", b, p + 28)
        off, = struct.unpack_from("<I", b, p + 42)
        name = b[p + 46:p + 46 + nl].decode("utf-8")
        x, xe = p + 46 + nl, p + 46 + nl + xl
        while x < xe:
            hid, sz = struct.unpack_from("<HH", b, x)
            if hid == 1:
                q = x + 4
                if usz == 0xFFFFFFFF:
                    usz, = struct.unpack_from("<Q", b, q); q += 8
                if csz == 0xFFFFFFFF:
                    csz, = struct.unpack_from("<Q", b, q); q += 8
                if off == 0xFFFFFFFF:
                    off, = struct.unpack_from("<Q", b, q); q += 8
            x += 4 + sz
        ent[name] = (meth, csz, usz, off)
        p = xe + cl
    return ent


def fetch_entry(key: str, ent: dict, dest: Path, workers: int = 8) -> Path:
    """zip エントリ key を dest に展開する。チャンクを順に展開して書き出すので、大きなファイルでもメモリを使い切らない。"""
    meth, csz, usz, off = ent[key]
    if dest.exists() and dest.stat().st_size == usz:
        print(f"skip (exists): {dest.name}")
        return dest
    lh = get_range(off, off + 29)
    start = off + 30 + struct.unpack_from("<H", lh, 26)[0] + struct.unpack_from("<H", lh, 28)[0]
    ranges = [(s, min(s + CHUNK, start + csz) - 1) for s in range(start, start + csz, CHUNK)]
    t0 = time.time()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    dec = zlib.decompressobj(-15) if meth == 8 else None
    head = b""
    tail = b""
    written = 0
    with open(tmp, "wb") as fo, ThreadPoolExecutor(workers) as ex:
        for i in range(0, len(ranges), workers * 4):  # 一度に workers*4 チャンク（最大 256MB）まで
            for part in ex.map(lambda r: get_range(*r), ranges[i:i + workers * 4]):
                out = dec.decompress(part) if dec else part
                if out:
                    if len(head) < 4:
                        head += out[:4 - len(head)]
                    tail = (tail + out)[-4:]
                    fo.write(out)
                    written += len(out)
        if dec:
            out = dec.flush()
            if out:
                tail = (tail + out)[-4:]
                fo.write(out)
                written += len(out)
    assert written == usz and head == b"PAR1" and tail == b"PAR1", f"verification failed: {key}"
    tmp.replace(dest)
    dt = time.time() - t0
    print(f"OK {dest.name}: {csz / 1e6:.1f} MB in {dt:.0f}s ({csz / 1e6 / max(dt, 1e-9):.2f} MB/s)", flush=True)
    return dest


def fetch(project: str, ent: dict, workers: int = 8, dest_dir: Path = DEST) -> Path:
    owner, repo = project.split("/", 1)
    key = next((k for k in ent if k.lower() == f"ossgamebench/commits/commits|{owner}|{repo}.parquet".lower()), None)
    if key is None:
        raise KeyError(project)
    return fetch_entry(key, ent, dest_dir / f"{owner}__{repo}.parquet", workers)


def dest_name(key: str) -> str:
    """zip エントリ名 OSSGameBench/commits/commits|owner|repo[|n].parquet → owner__repo[__n].parquet"""
    stem = key.split("commits|", 1)[1].rsplit(".parquet", 1)[0]
    return stem.replace("|", "__") + ".parquet"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("projects", nargs="*")
    ap.add_argument("--min-fixed", type=int)
    ap.add_argument("--max-mb", type=float, help="圧縮サイズがこれ以上のプロジェクトは除外")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--all", action="store_true",
                    help="zip 内の全コミット parquet を取得する。既定の保存先は 分析対象データ/commits_rest/（取得済みの commits/ にあるものは飛ばす）")
    ap.add_argument("--dest", type=Path, help="保存先ディレクトリ")
    ap.add_argument("--analysed", action="store_true",
                    help="レポートで分析した 83 プロジェクト（scripts/analysed_projects.txt）を取得する")
    ap.add_argument("--work", type=Path, help="作業ディレクトリ（セントラルディレクトリのキャッシュ置き場）")
    a = ap.parse_args()
    if a.work:
        WORK = a.work
    if a.analysed:
        ent = central_directory()
        names = {dest_name(k).lower(): k for k in ent if k.lower().startswith("ossgamebench/commits/commits|")}
        missing = [s for s in ANALYSED_PROJECTS if f"{s}.parquet".lower() not in names]
        if missing:
            sys.exit(f"zip に見つからない: {missing}")
        dest_dir = a.dest or DEST
        failed = []
        for i, s in enumerate(ANALYSED_PROJECTS, 1):
            print(f"[{i}/{len(ANALYSED_PROJECTS)}] ", end="", flush=True)
            try:
                fetch_entry(names[f"{s}.parquet".lower()], ent, dest_dir / f"{s}.parquet", a.workers)
            except Exception as e:  # noqa: BLE001
                print(f"FAILED {s}: {e}", flush=True)
                failed.append(s)
        print("done. failed:", failed)
        sys.exit(1 if failed else 0)
    if a.all:
        ent = central_directory()
        dest_dir = a.dest or (DATA / "commits_rest")
        have = {f.name.lower() for f in DEST.glob("*.parquet")}
        keys = sorted((k for k in ent if k.lower().startswith("ossgamebench/commits/commits|")), key=lambda k: ent[k][1])
        keys = [k for k in keys if dest_name(k).lower() not in have]
        print(f"対象 {len(keys)} ファイル, 圧縮 {sum(ent[k][1] for k in keys) / 1e9:.2f} GB, "
              f"展開後 {sum(ent[k][2] for k in keys) / 1e9:.2f} GB -> {dest_dir}", flush=True)
        failed = []
        for i, k in enumerate(keys, 1):
            print(f"[{i}/{len(keys)}] ", end="", flush=True)
            try:
                fetch_entry(k, ent, dest_dir / dest_name(k), a.workers)
            except Exception as e:  # noqa: BLE001
                print(f"FAILED {k}: {e}", flush=True)
                failed.append(k)
        print("done. failed:", failed)
        sys.exit(1 if failed else 0)
    projects = list(a.projects)
    n = pd.Series(dtype=int)
    if a.min_fixed:
        b = pd.read_parquet(OUT / "bugs.parquet", columns=["project", "outcome"])
        n = b[b.outcome == "fixed_by_pr"].groupby("project").size()
        projects += n[n >= a.min_fixed].index.tolist()
    ent = central_directory()
    lower = {k.lower(): v for k, v in ent.items()}

    def csize(p):
        o, r = p.split("/", 1)
        e = lower.get(f"ossgamebench/commits/commits|{o}|{r}.parquet".lower())
        return e[1] if e else None

    sizes = {p: csize(p) for p in projects}
    missing = [p for p, v in sizes.items() if v is None]
    if missing:
        print("単一ファイル名で zip に無い（分割ファイルの可能性あり。--all で取得）:", missing)
    projects = [p for p, v in sizes.items() if v is not None and (a.max_mb is None or v < a.max_mb * 1e6)]
    total = sum(sizes[p] for p in projects)
    nfix = int(n.reindex(projects).fillna(0).sum())
    print(f"対象 {len(projects)} プロジェクト, 圧縮 {total / 1e9:.2f} GB, PR で修正されたバグ {nfix} 件", flush=True)
    failed = []
    for i, p in enumerate(sorted(projects, key=lambda x: sizes[x]), 1):
        print(f"[{i}/{len(projects)}] ", end="", flush=True)
        try:
            fetch(p, ent, a.workers, a.dest or DEST)
        except Exception as e:  # noqa: BLE001
            print(f"FAILED {p}: {e}", flush=True)
            failed.append(p)
    print("done. failed:", failed)
