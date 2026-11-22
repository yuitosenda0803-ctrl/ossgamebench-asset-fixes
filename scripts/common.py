"""共通定義: パス、バグ判定、エンジン判定。

全スクリプトから import して使う。判定ルールはレポートに記載したものと一致させること。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "分析対象データ"
OUT = ROOT / "outputs"
FIG = OUT / "figs"
TAB = OUT / "tables"
for _p in (OUT, FIG, TAB):
    _p.mkdir(parents=True, exist_ok=True)

SEED = 20261115

# 分析対象の 83 プロジェクト（PR で修正されたバグが 20 件以上、かつ zip 内のコミット parquet の圧縮サイズが 300MB 未満）。
# データセットを全部展開した場合でも結果が変わらないよう、06・07b はこの一覧のファイルだけを読む。
ANALYSED_PROJECTS = (ROOT / "scripts" / "analysed_projects.txt").read_text(encoding="utf-8").split()
COMMIT_DIRS = [DATA / "commits", DATA / "commits_rest"]


def analysed_commit_files() -> list[Path]:
    """分析対象 83 プロジェクトのコミット parquet（commits/ か commits_rest/ のどちらかにある）。"""
    out = []
    for stem in ANALYSED_PROJECTS:
        hits = [d / f"{stem}.parquet" for d in COMMIT_DIRS if (d / f"{stem}.parquet").exists()]
        if not hits:
            raise FileNotFoundError(f"{stem}.parquet が 分析対象データ/commits(_rest)/ にありません")
        out.append(hits[0])
    return out

# ---------------------------------------------------------------------------
# バグ判定（ラベルベース）
# ---------------------------------------------------------------------------
# ラベルは小文字化し、絵文字など英数字以外の先頭記号を除去してから判定する。
# 「bug」「defect」「crash」「regression」「glitch」を単語として含む、または
# bugfix / error report 系の明示ラベルを持つ issue をバグ報告とみなす。
# "debug" のような部分一致を避けるため単語境界を使う。
BUG_PATTERN = re.compile(
    r"(?<![a-z])(bugs?|defects?|crash(es|ed)?|regressions?|glitch(es)?)(?![a-z])"
    r"|bug ?fix|error report|crash or hang|type-defect"
)
# バグ報告ではない（または開発者に不正と判断された）ことを示すラベル。
# バグラベルと同時に付いていても「無効バグ」として解決時間の分析から除外する。
INVALID_PATTERN = re.compile(
    r"not (really )?a bug|invalid|duplicate|wont ?fix|won't ?fix|cannot reproduce|can't reproduce"
    r"|cant reproduce|works ?for ?me|as intended|by design|not reproducible|unreproducible"
    r"|blizzlike"
)
# stale bot による自動クローズを示すラベル
STALE_PATTERN = re.compile(r"(?<![a-z])stale(?![a-z])|inactive|abandoned|auto-?closed")


def norm_label(label: str) -> str:
    s = label.lower().strip()
    s = re.sub(r"^[^a-z0-9]+", "", s)  # 先頭の絵文字・記号
    return s


def is_bug_label(label: str) -> bool:
    s = norm_label(label)
    if re.search(r"not (really )?a bug", s):
        return False
    return bool(BUG_PATTERN.search(s))


def is_invalid_label(label: str) -> bool:
    return bool(INVALID_PATTERN.search(norm_label(label)))


def is_stale_label(label: str) -> bool:
    return bool(STALE_PATTERN.search(norm_label(label)))


# ラベルから読み取る補助カテゴリ（RQ2 の説明変数）
LABEL_TOPICS = {
    # 単語境界を付ける（"hang" が "changes" に一致していた。2026-11-18 修正）
    "crash": r"crash|(?<![a-z])(hang(s|ing)?|freez(e|es|ing)|segfault)(?![a-z])",
    "regression": r"regression",
    "graphics": r"graphic|render|visual|shader|sprite|texture|animation",
    "network": r"network|multiplayer|netcode|online|server|(?<![a-z])(de)?sync",
    "performance": r"(?<![a-z])perf|(?<![a-z])lag|fps|memory|optimi",  # "lag" が "flag" に一致しないように
    "ui": r"(?<![a-z])(ui|gui|ux|hud|interface|menu)(?![a-z])",
    # "mac" が "macro" に、"ios" が "studios" などに一致していた（2026-11-18 修正）
    "platform": r"android|linux|windows|platform|mobile|steam ?deck|(?<![a-z])(ios|mac|macos|osx|web|webgl|html5)(?![a-z])",
    "high_priority": r"(?<![a-z])(critical|blocker|urgent|high|major|p0|p1|severe)(?![a-z])",
    "low_priority": r"(?<![a-z])(low|minor|trivial|p3|p4)(?![a-z])|low priority",
    "good_first_issue": r"good first issue|easy|beginner|starter",
    "needs_info": r"need(s)? (more )?info|needs repro|waiting|feedback|unconfirmed|untriaged|triage",
}
LABEL_TOPICS_RE = {k: re.compile(v) for k, v in LABEL_TOPICS.items()}


# ---------------------------------------------------------------------------
# エンジン判定（プロジェクト単位）
# ---------------------------------------------------------------------------
# 原論文は issue/PR 本文の部分文字列一致（"unity" が "community" にも一致する）で
# エンジンを推定しており誤検出が多い。ここではリポジトリ側の情報を優先して判定する:
#   1. 言語統計（GDScript→Godot, ShaderLab→Unity）
#   2. topics（単語境界つき一致）
#   3. README（単語境界つき一致）
# 上から順に最初に決まったものを採用する。
ENGINE_KEYWORDS = [
    ("Unity", r"unity3d|unity ?engine|unity-engine|made with unity|(?<![a-z])unity(?![a-z])"),
    ("Godot", r"godot"),
    ("Unreal", r"unreal"),
    # その他の既製エンジン・フレームワーク
    ("Other framework", r"monogame|libgdx|love2d|löve|pygame|(?<![a-z])bevy(?![a-z])|phaser|raylib"
                        r"|(?<![a-z])sfml(?![a-z])|three\.?js|minetest|luanti|(?<![a-z])xna(?![a-z])"
                        r"|cocos2d|haxeflixel|openfl|pixi\.?js|gamemaker|rpg ?maker|defold|ren'?py"
                        r"|(?<![a-z])fna(?![a-z])|stride3d|o3de|cryengine|(?<![a-z])ogre3d(?![a-z])"
                        r"|jmonkey|(?<![a-z])excalibur(?![a-z])|construct ?3|macroquad|ebiten|pico-?8"),
]
ENGINE_KEYWORDS_RE = [(n, re.compile(p)) for n, p in ENGINE_KEYWORDS]
ENGINE_ORDER = ["Unity", "Godot", "Unreal", "Other framework", "Custom/unknown"]


def _lang_share(langs: dict, key: str) -> float:
    tot = sum(langs.values()) or 1
    return langs.get(key, 0) / tot


def classify_engine(languages: str | None, topics: str | None, readme: str | None) -> tuple[str, str]:
    """(engine, 根拠) を返す。

    言語統計は誤検出がある（例: Quake 系の .shader が ShaderLab と判定される）ため、
    Unity は「C# が 20% 以上」、Godot は「GDScript が 5% 以上」を必要条件として加える。
    Unreal は C++ が 20% 以上の場合に限る。
    """
    langs = json.loads(languages) if languages else {}
    cs, cpp, gd = (_lang_share(langs, k) for k in ("C#", "C++", "GDScript"))
    if gd >= 0.05:
        return "Godot", "language"
    if cs >= 0.20 and "ShaderLab" in langs:
        return "Unity", "language"
    t = (topics or "").lower().replace(";", " ")
    r = (readme or "").lower()
    # 複数のエンジン系統に言及する場合（ライブラリやツールの「対応エンジン一覧」など）は
    # 根拠として使わず、次の情報源へ進む。
    for src, text in (("topics", t), ("readme", r)):
        hits = []
        for name, rx in ENGINE_KEYWORDS_RE:
            if not rx.search(text):
                continue
            if name == "Unity" and cs < 0.20:
                continue
            if name == "Unreal" and cpp < 0.20:
                continue
            hits.append(name)
        if len(hits) == 1:
            return hits[0], src
    return "Custom/unknown", "none"


ENGINE_PROJECT_RE = re.compile(
    r"(^|;)(game-engine|engine|framework|game-framework|game-library|gamedev-library|2d-game-engine"
    r"|3d-game-engine|game-engine-2d|game-engine-3d|rendering-engine|graphics-engine)(;|$)")


def is_engine_project(topics: str | None) -> bool:
    """topics に engine / framework 系の語を持つ（エンジン・フレームワーク開発やエンジン再実装）。"""
    return bool(ENGINE_PROJECT_RE.search((topics or "").lower()))


def primary_language(languages: str | None) -> str | None:
    langs = json.loads(languages) if languages else {}
    if not langs:
        return None
    return max(langs.items(), key=lambda kv: kv[1])[0]


# ---------------------------------------------------------------------------
# 図の共通設定
# ---------------------------------------------------------------------------
def setup_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.family": ["Yu Gothic", "Meiryo", "MS Gothic", "sans-serif"],
        "figure.dpi": 150,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "font.size": 9,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })
    return plt


# ---------------------------------------------------------------------------
# 重複リポジトリ（改名・移管で同一 issue が2つの名前で収録されている）
# 原論文のノートブック analysis-2.ipynb の duplicated_repos_v1 と同じ20件で、
# issue の (番号, 作成日時, タイトル) が完全一致するペアを検出した結果とも一致した。
# 旧名側を除外する。
DUPLICATED_REPOS = {
    "Dragon-Fox-Collective/SBEPIS", "lVlyke/starfield-mod-loader", "kyh/vibed-games",
    "lexi-the-cute/catgirl-engine", "benpollarduk/BP.AdventureFramework", "MathiasGruber/TheNinjaRPG",
    "alcalyn/hex", "sefinek24/genshin-impact-reshade", "TeamFluXis/fluXis", "alextangxiao/tengine",
    "srmeier/knightonline", "zerulight/erupe", "dylan-power/dinosaur-exploder", "minetest/minetest_game",
    "mihailris/voxelengine-cpp", "LostArtefacts/TR1X", "joshzcold/cold-family-feud",
    "fundament-software/feathergui", "themeparkworld/opentpw", "draftbot-a-discord-adventure/draftbot",
}


def drop_dup(df, col: str = "project"):
    return df[~df[col].isin(DUPLICATED_REPOS)]
