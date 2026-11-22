"""修正 PR の変更ファイルを分類し、PR 単位の特徴量を作る（テーマA の RQ1・RQ2 の入力）。

入力:  outputs/fix_commits.parquet（06）、outputs/bugs.parquet（01）、分析対象データ/pr.parquet, pr_commit.parquet,
       issue_pr.parquet、outputs/tables/link_coverage.csv（06）
出力:  outputs/fix_files.parquet   … 修正 PR × 変更ファイル（分類つき）
       outputs/fix_prs.parquet     … 修正 PR 単位（資産を変更したか、時間、規模、感度分析用のフラグ）
       outputs/tables/file_category_counts.csv, unclassified_ext_top.csv
       outputs/tables/validation_file_sample.csv（分類の精度確認・評価用、層別の無作為抽出。規則づくり用の
       validation_file_sample_dev.csv に含まれるファイルは除く）
       outputs/tables/validation_bug_sample.csv（バグ判定の精度確認用 50 件。既にあれば上書きしない）

分類の定義は README の「ゲーム資産の内訳」とレポートの 3.3 節。ゲーム資産の拡張子は、OSSGameBench 著者の分析ノートブック
（analysis-1-3-4.ipynb の non_code_asset_types）を土台にしている。
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from common import DATA, OUT, TAB, SEED, drop_dup

# ---------------------------------------------------------------------------
# 4.1 ゲーム資産（ノートブックの 7 種類。重複は 1 種類に割り当て、meta は除き、Godot の tscn・tres を追加）
# ---------------------------------------------------------------------------
ASSET_TYPES = {
    "images_and_textures": ["png", "jpg", "jpeg", "svg", "bmp", "tga", "dds", "pcx", "icns", "psd", "xcf", "pkm",
                            "ora", "atlas", "spriteatlas", "texinfo"],
    "audio_and_music": ["ogg", "wav", "mp3", "aud", "midi", "flac", "soundinfo"],
    "models_and_meshes": ["fbx", "obj", "mesh", "gltf", "glb", "blend", "3ds", "dae", "j3o", "j3m", "j3md",
                          "md5mesh", "md5anim", "vxl", "t3d"],
    "fonts_and_ui": ["ttf", "otf", "fnt", "font", "guiskin", "materialpreferences"],
    "shaders_and_materials": ["shader", "cginc", "glsl", "hlsl", "mat", "shadergraph", "mtlx", "behavior", "def",
                              "frag", "vert"],  # frag・vert（GLSL のシェーダ）を追加
    "scene_and_level_data": ["prefab", "scene", "map", "level", "tiledmap", "oramap", "layout", "world",
                             "controller", "anim", "tscn", "tres", "tmx"],  # tmx（Tiled のマップ）を追加
    "engine_specific_assets": ["unity", "asset", "uasset", "umap", "rendertexture", "physicmaterial", "assetbundle"],
}
ASSET_EXT = {e: t for t, es in ASSET_TYPES.items() for e in es}

# 4.2 資産以外
ENGINE_AUX_EXT = {"meta", "uid", "import"}
DATA_EXT = {"json", "json5", "yaml", "yml", "xml", "ini", "toml", "cfg", "conf", "csv"}
DOC_EXT = {"md", "txt", "rst", "adoc", "markdown"}
DOC_NAMES = {"changelog", "changes", "news", "history", "license", "licence", "readme", "authors", "contributors",
             "copying", "notice", "credits"}
RX_DOC_DIR = re.compile(r"^(docs?|documentation)/")  # リポジトリ直下の docs/ だけ（ゲーム内の wiki 画面などを誤って文書にしないため）
TRANSLATION_EXT = {"po", "pot", "mo"}
BUILD_EXT = {"cmake", "gradle", "vcxproj", "sln", "csproj", "fsproj", "props", "targets", "mk", "pro", "pri",
             "dockerfile", "lock"}
BUILD_NAMES = {"cmakelists.txt", "makefile", "dockerfile", "build.gradle", "settings.gradle", "package.json",
               "package-lock.json", "cargo.toml", "cargo.lock", "pyproject.toml", "setup.py", "meson.build",
               "configure.ac", "go.mod", "go.sum", ".gitignore", ".gitattributes", ".editorconfig"}
CODE_EXT = {
    "c", "h", "cc", "cpp", "cxx", "hh", "hpp", "hxx", "inl", "ipp", "m", "mm",
    "cs", "java", "kt", "kts", "scala", "groovy", "lua", "py", "pyx", "gd", "js", "mjs", "cjs", "jsx", "ts", "tsx",
    "rs", "go", "rb", "php", "swift", "dart", "zig", "nim", "hx", "hxml", "vala", "d", "pas", "pp", "lpr",
    "vue", "svelte", "html", "htm", "css", "scss", "sass", "less", "xaml", "razor", "cshtml", "jinja2", "j2", "hbs",
    "sh", "bash", "ps1", "bat", "cmd", "asm", "s", "f90", "for", "jl", "r", "ex", "exs", "erl", "clj", "elm", "ml",
    "fs", "fsx", "vb", "sql", "proto", "gml", "as", "sqf", "nut", "wren", "tcl", "pl", "pm", "coffee",
}
RX_LOCALE_DIR = re.compile(r"(^|/)(locale|locales|i18n|l10n|lang|langs|language|languages|translations?)(/|$)")


def classify(path: str) -> tuple[str, str]:
    """(分類, 資産の種類) を返す。分類は asset / code / incidental_translation / incidental_build /
    incidental_doc / game_data / engine_aux / other。"""
    p = path.replace("\\", "/").lower()
    name = p.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1] if "." in name.lstrip(".") else ""
    # パス規則を先に当てる
    if p.startswith(".github/") or "/.github/" in p or p.startswith(".gitlab") or p.startswith(".circleci/"):
        return "incidental_build", ""
    if RX_LOCALE_DIR.search(p) and ext in {"ts", "po", "pot", "mo", "json", "yaml", "yml", "xml", "txt", "ini",
                                            "csv", "properties", "resx", "lang", "cfg", "toml", "tr"}:
        return "incidental_translation", ""
    if ext == "properties" and re.match(r"(i18n|messages|strings|lang)(_|\.|$)", name):
        return "incidental_translation", ""
    # docs/ 配下はドキュメント（スクリーンショットなどの画像も含めて、ゲーム資産ではない）
    if RX_DOC_DIR.search(p):
        return "incidental_doc", ""
    # 二通りに読める拡張子
    if ext == "map" and (name.endswith(".js.map") or name.endswith(".css.map")):
        return "other", ""
    if ext == "obj" and re.search(r"(^|/)obj/", p):
        return "other", ""
    if ext in ASSET_EXT:
        return "asset", ASSET_EXT[ext]
    if ext in ENGINE_AUX_EXT:
        return "engine_aux", ""
    if name in BUILD_NAMES or ext in BUILD_EXT:
        return "incidental_build", ""
    if ext in TRANSLATION_EXT:
        return "incidental_translation", ""
    if ext in CODE_EXT:
        return "code", ""
    if ext in DATA_EXT:
        return "game_data", ""
    if name.split(".")[0] in DOC_NAMES or (ext in DOC_EXT and (ext != "txt" or "/" not in p)):
        return "incidental_doc", ""  # .txt はリポジトリ直下のものだけ文書とみなす（ゲームのスクリプトやデータの .txt があるため）
    return "other", ""


def main() -> None:
    fc = pd.read_parquet(OUT / "fix_commits.parquet")
    files = fc[["project", "pr_number", "file"]].drop_duplicates().reset_index(drop=True)
    cls = files.file.map(classify)
    files["category"] = cls.str[0]
    files["asset_type"] = cls.str[1]
    files["ext"] = files.file.str.lower().str.extract(r"\.([^./]+)$")[0].fillna("")
    files.to_parquet(OUT / "fix_files.parquet", index=False)

    # 集計表
    cnt = files.category.value_counts().rename_axis("category").reset_index(name="n_files")
    cnt["share"] = cnt.n_files / cnt.n_files.sum()
    cnt.to_csv(TAB / "file_category_counts.csv", index=False, encoding="utf-8-sig")
    files[files.category == "other"].ext.value_counts().head(40).rename_axis("ext").reset_index(name="n_files") \
        .to_csv(TAB / "unclassified_ext_top.csv", index=False, encoding="utf-8-sig")

    # ---------------- PR 単位 ----------------
    g = files.groupby(["project", "pr_number"])
    prs = g.size().rename("n_files").reset_index()
    for c in ["asset", "code", "game_data", "engine_aux", "other",
              "incidental_translation", "incidental_build", "incidental_doc"]:
        prs[f"n_{c}"] = g.category.apply(lambda s, c=c: int((s == c).sum())).values
    for t in ASSET_TYPES:
        prs[f"has_{t}"] = g.asset_type.apply(lambda s, t=t: bool((s == t).any())).values
    prs["has_asset"] = prs.n_asset > 0
    prs["has_asset_or_data"] = (prs.n_asset + prs.n_game_data) > 0  # 感度分析 (3)
    prs["has_incidental"] = (prs.n_incidental_translation + prs.n_incidental_build + prs.n_incidental_doc) > 0

    methods = fc.groupby(["project", "pr_number"]).method.agg(lambda s: frozenset(s)).rename("methods").reset_index()
    prs = prs.merge(methods, on=["project", "pr_number"], how="left")
    prs["title_only"] = prs.methods.map(lambda m: m == frozenset({"title"}))
    prs["methods"] = prs.methods.map(lambda m: ",".join(sorted(m)))

    # 時間: issue 作成（その PR が直したバグ issue のうち最も早いもの）→ PR 作成 → マージ
    bugs = pd.read_parquet(OUT / "bugs.parquet", columns=["project", "number", "created_at", "outcome"])
    bugs = bugs[bugs.outcome == "fixed_by_pr"]
    ip = drop_dup(pd.read_parquet(DATA / "issue_pr.parquet")).drop_duplicates()
    link = ip.merge(bugs, left_on=["project", "issue_number"], right_on=["project", "number"])
    first_issue = link.groupby(["project", "pr_number"]).agg(
        issue_created_at=("created_at", "min"), n_bugs_fixed=("issue_number", "nunique")).reset_index()
    pr = drop_dup(pd.read_parquet(DATA / "pr.parquet", columns=["project", "number", "created_at", "merged_at"]))
    pr = pr.rename(columns={"number": "pr_number", "created_at": "pr_created_at"})
    pr["pr_created_at"] = pd.to_datetime(pr.pr_created_at, utc=True)
    pr["merged_at"] = pd.to_datetime(pr.merged_at, utc=True)
    pc = drop_dup(pd.read_parquet(DATA / "pr_commit.parquet"))
    npc = pc.groupby(["project", "pr_number"]).commit_sha.nunique().rename("n_pr_commits").reset_index()
    prs = (prs.merge(first_issue, on=["project", "pr_number"], how="left")
              .merge(pr, on=["project", "pr_number"], how="left")
              .merge(npc, on=["project", "pr_number"], how="left"))
    prs["issue_created_at"] = pd.to_datetime(prs.issue_created_at, utc=True)
    day = 86400.0
    prs["days_issue_to_pr"] = (prs.pr_created_at - prs.issue_created_at).dt.total_seconds() / day
    prs["days_pr_to_merge"] = (prs.merged_at - prs.pr_created_at).dt.total_seconds() / day
    prs["pr_before_issue"] = prs.days_issue_to_pr < 0

    # 捕捉率と主な分析対象（捕捉率 70% 以上）
    cov = pd.read_csv(TAB / "link_coverage.csv")
    prs = prs.merge(cov[["project", "coverage"]], on="project", how="left")
    prs["in_main"] = prs.coverage >= 0.7
    prs.to_parquet(OUT / "fix_prs.parquet", index=False)

    # ---------------- 精度確認用の抽出（分類ごとに層別、主な対象のプロジェクトから） ----------------
    # 最初の抽出（validation_file_sample_dev.csv）は規則づくりに使ったので、精度の報告には使わない。
    # 規則を固定したあと、見たファイルを除き、別のシードで抽出し直したものを評価用にする。
    rng = np.random.default_rng(SEED + 7)
    main_files = files.merge(prs.loc[prs.in_main, ["project", "pr_number"]], on=["project", "pr_number"])
    dev_path = TAB / "validation_file_sample_dev.csv"
    if dev_path.exists():
        seen = pd.read_csv(dev_path)[["project", "pr_number", "file"]].assign(_seen=True)
        main_files = main_files.merge(seen, on=["project", "pr_number", "file"], how="left")
        main_files = main_files[main_files._seen.isna()].drop(columns="_seen")
    quota = {"asset": 30, "code": 15, "game_data": 15, "engine_aux": 5, "incidental_translation": 8,
             "incidental_build": 8, "incidental_doc": 5, "other": 14}
    parts = []
    for c, k in quota.items():
        pool = main_files[main_files.category == c]
        if len(pool):
            parts.append(pool.iloc[rng.choice(len(pool), size=min(k, len(pool)), replace=False)])
    # 二通りに読める拡張子は追加で確認する
    amb = main_files[main_files.ext.isin(["def", "ts", "map", "obj"])]
    amb = amb[~amb.index.isin(pd.concat(parts).index)]
    if len(amb):
        parts.append(amb.iloc[rng.choice(len(amb), size=min(10, len(amb)), replace=False)])
    sample = pd.concat(parts).sample(frac=1, random_state=SEED)
    sample["url"] = "https://github.com/" + sample.project + "/pull/" + sample.pr_number.astype(str) + "/files"
    sample = sample[["project", "pr_number", "file", "ext", "category", "asset_type", "url"]]
    sample["claude_judgement"] = ""
    sample["your_judgement"] = ""
    sample["note"] = ""
    eval_path = TAB / "validation_file_sample.csv"
    if not eval_path.exists():  # 確認作業の記入（claude_judgement・your_judgement）を上書きしない
        sample.to_csv(eval_path, index=False, encoding="utf-8-sig")

    # ---------------- バグ判定の精度確認用の抽出（主な対象の修正 PR が直したバグ issue から 50 件） ----------------
    bug_path = TAB / "validation_bug_sample.csv"
    if not bug_path.exists():  # 確認作業の記入を上書きしない
        bt = pd.read_parquet(OUT / "bugs.parquet", columns=["project", "number", "title", "labels", "outcome"])
        bt = bt[bt.outcome == "fixed_by_pr"]
        x = (ip.merge(prs.loc[prs.in_main, ["project", "pr_number"]], on=["project", "pr_number"])
               .merge(bt, left_on=["project", "issue_number"], right_on=["project", "number"])
               .drop_duplicates(["project", "issue_number"]))
        r2 = np.random.default_rng(SEED + 1)
        bs = x.iloc[r2.choice(len(x), 50, replace=False)].copy()
        bs["labels"] = bs.labels.map(lambda l: "; ".join(l))
        bs["url"] = "https://github.com/" + bs.project + "/issues/" + bs.issue_number.astype(str)
        bs = bs[["project", "issue_number", "title", "labels", "url"]].assign(claude_judgement="", your_judgement="", note="")
        bs.to_csv(bug_path, index=False, encoding="utf-8-sig")

    m = prs[prs.in_main]
    print(f"files: {len(files):,} / PRs: {len(prs):,} / main PRs: {len(m):,} ({m.project.nunique()} projects)")
    print(cnt.to_string(index=False))
    print(f"main: has_asset {m.has_asset.mean():.1%}, has_asset_or_data {m.has_asset_or_data.mean():.1%}, "
          f"title_only {m.title_only.mean():.1%}, pr_before_issue {m.pr_before_issue.mean():.1%}")
    print("validation sample:", len(sample))


if __name__ == "__main__":
    main()
