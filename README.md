# 高度ソフトウェア工学 演習レポート（OSSGameBench, Option A: Bug Resolution）

OSSGameBench を使って、オープンソースゲームのバグ解決を調べた演習レポートのコードと結果です。レポート本体は授業で別に提出したため、公開リポジトリには含めていません（下の「レポート」「3.3 節」などはそのレポートを指します）。
## テーマと RQ（2026-11-18 確定）
**テーマ: ゲームのバグ修正は何を変えているか — コードとゲーム資産**

| RQ | 問い（データで観測できる量で表現） |
|---|---|
| RQ0 | バグ issue はどう終わっているか（マージ済み PR に紐づく／PR なしで close／一括・stale close／無効／open）。そのうち、本研究で分析できる「マージ済み PR とコミットまで辿れる修正」は何%か |
| RQ1 | バグに紐づいたマージ済みの修正 PR のうち、ゲーム資産のファイルを変更しているものは何%か。どの種類が多いか。プロジェクトによってどれだけ違うか |
| RQ2 | ゲーム資産を変更する修正 PR は、変更しない修正 PR と比べて、(a) issue 作成→PR 作成、(b) PR 作成→マージの時間、(c) 変更ファイル数が異なるか。(a)(b) の差は (c) を調整しても残るか |

- **ゲーム資産**: OSSGameBench 著者の分析ノートブックの 7 種類（画像・音声・モデル・フォント/UI・シェーダ/マテリアル・シーン/レベル・エンジン固有の資産）。エンジンが自動生成する付属ファイル（`.meta`・`.uid`・`.import`）は除く。比較相手の「資産を変更しない修正 PR」には、付随的な変更・付属ファイル・ゲームデータだけを変えた PR も入る。翻訳・ドキュメント・ビルドは「付随的な変更」。ゲームデータ・設定（json など）は感度分析で扱う。
- **対象**: 修正 PR とコミットの紐づけの捕捉率が 70% 以上の 78 プロジェクト（紐づいた修正 PR 10,290 件）。
- **感度分析**: title だけで紐づいた PR を除く／上位プロジェクトを除く／ゲームデータを資産に含める／捕捉率の基準を変える（90%・50%）／リポジトリに資産ファイルを持つプロジェクトだけ。
- 「労力」「難しさ」は RQ に入れず、時間や規模の指標と区別して考察で扱う。

指標の定義と分析の方法はレポート（3〜4 章）に書いた。

### ゲーム資産の内訳
**定義**（7 種類。OSSGameBench 著者の分析ノートブック `analysis-1-3-4.ipynb` の `non_code_asset_types` を土台にし、太字の拡張子を本研究で追加。判定は [scripts/07_classify_files.py](scripts/07_classify_files.py)）

| 種類 | 拡張子 |
|---|---|
| 画像・テクスチャ | png, jpg, jpeg, svg, bmp, tga, dds, pcx, icns, psd, xcf, pkm, ora, atlas, spriteatlas, texinfo |
| 音声・音楽 | ogg, wav, mp3, aud, midi, flac, soundinfo |
| モデル・メッシュ | fbx, obj, mesh, gltf, glb, blend, 3ds, dae, j3o, j3m, j3md, md5mesh, md5anim, vxl, t3d |
| フォント・UI | ttf, otf, fnt, font, guiskin, materialpreferences |
| シェーダ・マテリアル | shader, cginc, glsl, hlsl, mat, shadergraph, mtlx, behavior, def, **frag, vert** |
| シーン・レベル | prefab, scene, map, level, tiledmap, oramap, layout, world, controller, anim, **tscn, tres, tmx** |
| エンジン固有の資産 | unity, asset, uasset, umap, rendertexture, physicmaterial, assetbundle |

- 拡張子より先にパスの規則を当てる。ビルド用のディレクトリ、言語ディレクトリ（翻訳）、リポジトリ直下の `docs/` にあるファイルは資産にしない。
- 資産に含めないもの:
  - エンジンが自動生成する付属ファイル: `.meta`, `.uid`, `.import`
  - ゲームデータ・設定: json, yaml など。感度分析では資産に含める。
  - 付随的な変更: 翻訳、ドキュメント、ビルド/CI

**結果**（主な対象 78 プロジェクト、修正 PR 10,290 件。出典は [outputs/tables/rq1_asset_types.csv](outputs/tables/rq1_asset_types.csv) と `fix_files.parquet`。2026-11-20 に紐づけを直した後の値）

ゲーム資産を変更する修正 PR は 407 件（全体の 4.0%）です。1 つの PR が複数の種類を含むことがあるため、下の割合を足すと 100% を超えます。

| 種類 | 修正 PR 数 | 資産 PR に占める割合 | 全修正 PR に占める割合 | 変更ファイル数（延べ） | 多い拡張子（ファイル数） |
|---|--:|--:|--:|--:|---|
| シーン・レベル | 196 | 48.2% | 1.9% | 1,458 | tscn 762, prefab 448, anim 109 |
| 画像・テクスチャ | 151 | 37.1% | 1.5% | 2,725 | png 2,290, svg 352, jpg 71 |
| シェーダ・マテリアル | 60 | 14.7% | 0.6% | 128 | def 49, mat 31, shader 15 |
| エンジン固有の資産 | 51 | 12.5% | 0.5% | 461 | asset 411, unity 49 |
| 音声・音楽 | 13 | 3.2% | 0.1% | 65 | wav 46, ogg 10, mp3 8 |
| モデル・メッシュ | 11 | 2.7% | 0.1% | 44 | mesh 23, fbx 14, glb 5 |
| フォント・UI | 10 | 2.5% | 0.1% | 56 | ttf 46, otf 6, fnt 4 |

- コードを含まず、資産だけを変えた修正 PR は 120 件（1.2%）。
- コミット履歴に資産ファイルが 20 件以上ある 71 プロジェクトだけでも 4.0%。資産 PR の半分（202/407）は上位 3 プロジェクト（thrive・ss3d・aottg-2）のもので、残りの 75 プロジェクトでは 2.1%（[outputs/tables/rq1_by_repo_assets.csv](outputs/tables/rq1_by_repo_assets.csv)）。
- 精度確認では、`def` として資産に入った 3 件すべてがゲーム資産ではなく C/C++ のマクロ定義（コード）だった。
- 2026-11-20 の修正前は 488 件（4.7%）だった。PR の作業中に本流を取り込んだマージコミットの変更ファイルを、PR の変更として数えていたため（[scripts/06_link_fix_commits.py](scripts/06_link_fix_commits.py) の冒頭を参照）。

## どこに何があるか
| ファイル | 内容 |
|---|---|
| report/ | 英語レポートの原稿（report.html、2 段組）と PDF（report.pdf）。PDF は `sh scripts/render_report.sh` で Edge のヘッドレス印刷により作成。レポートは別に提出したので、公開リポジトリには含めていない |
| [scripts/](scripts/) | データの取り出し・前処理・分析のスクリプト（下の「再現手順」） |
| [outputs/](outputs/) | 図（figs/）、表（tables/）、83 プロジェクトのコミットデータを取得したときのログ（logs/）。parquet の中間データは含まない |

### レポートの図・表と出力ファイル
| レポート | 出力ファイル（outputs/） |
|---|---|
| 図 1（RQ2 の分布） | figs/figA1_rq2_ecdf.png |
| 図 2（ハザード比と感度分析） | figs/figA2_rq2_hr.png |
| RQ0（バグの終わり方） | tables/rq0_outcomes.csv, rq0_project_fixed_share.csv |
| RQ1（資産を変更する修正 PR の割合・種類・プロジェクト差） | tables/rq1_asset_share.csv, rq1_asset_types.csv, rq1_project_shares.csv, rq1_project_summary.csv, rq1_by_repo_assets.csv |
| RQ2（時間・変更ファイル数・Cox） | tables/rq2_main.csv, rq2_sensitivity.csv, rq2_ph_test.csv |
| 紐づけの捕捉率 | tables/link_coverage.csv |
| 精度確認 | tables/validation_file_accuracy.csv, validation_bug_accuracy.csv, validation_link_accuracy.csv, validation_corrected.csv |

- プロジェクト数: データセットは 970 プロジェクト、リポジトリ名の変更による重複を除くと 950（レポートの値）。`tables/preprocess_summary.csv` の `projects_total = 944` は、そのうち issue を 1 件以上持つプロジェクトの数。

## 再現手順
**環境**: Windows 11 + PowerShell、Python 3.14.0。リポジトリ直下で次を実行する。
```powershell
py -3.14 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

**データ**: OSSGameBench（Zenodo record 17553539 の `OSSGameBench.zip`、65.8GB）。データ本体は含めていない。次のどちらかで `分析対象データ/` に置く。

- **A. zip 全体をダウンロードした場合**: zip を展開せずに、次の 1 行で取り出しから分析まで実行する。
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1 -Zip <OSSGameBench.zip のパス>
  ```
  zip の中のコミットファイル名は `commits|owner|repo.parquet` で、`|` は Windows のファイル名に使えない。
  そのため zip を普通に展開せず、[scripts/00_extract_zip.py](scripts/00_extract_zip.py) が zip のまま読み、`owner__repo.parquet` の名前で書き出す。
  取り出すのは、表 8 ファイルと、[scripts/analysed_projects.txt](scripts/analysed_projects.txt) の 83 プロジェクトのコミットファイル（展開後 約 6GB）だけ。
- **B. 必要な部分だけをダウンロードする場合**（zip 全体のダウンロードは Zenodo の速度制限で時間がかかるため、本研究ではこちらを使った）:
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\00_fetch_dataset.ps1 -WorkDir <作業フォルダ>   # 表 8 ファイル（約 646MB）
  cd scripts; ..\.venv\Scripts\python.exe -u 05_fetch_commits.py --analysed --work <作業フォルダ>; cd ..   # 83 プロジェクトのコミット（圧縮 5.1GB、数時間）
  powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1
  ```
  `<作業フォルダ>` は絶対パスで指定する（00 と 05 で実行するフォルダが違うため）。作業フォルダには zip の目次（セントラルディレクトリ）のキャッシュなどを置く。省略すると `C:\data\OSSGameBench\_work`（05 は環境変数 `OSSGB_WORK` でも指定できる）。

**分析の手順**（`run_all.ps1` が順に実行する。個別に実行するときは `scripts/` で `..\.venv\Scripts\python.exe <スクリプト>`）

| 手順 | スクリプト | 主な出力 | 所要時間の目安 |
|---|---|---|---|
| 前処理（バグの判定、issue と PR の紐づけ） | 01_preprocess.py | outputs/bugs.parquet, tables/preprocess_summary.csv | 約 20 秒 |
| 修正 PR とコミットの紐づけ | 06_link_fix_commits.py | outputs/fix_commits.parquet, tables/link_coverage.csv | 数分 |
| ファイル分類 | 07_classify_files.py | outputs/fix_files.parquet, fix_prs.parquet, tables/file_category_counts.csv | 約 10 秒 |
| リポジトリの資産ファイル | 07b_repo_assets.py | tables/repo_assets.csv | 数分 |
| RQ0〜2 の分析 | 08_theme_rq.py | tables/rq0_*.csv, rq1_*.csv, rq2_main.csv, rq2_sensitivity.csv | 約 3 分 |
| 図と比例ハザード仮定の検定 | 09_theme_figs.py | figs/figA1_rq2_ecdf.*, figA2_rq2_hr.*, tables/rq2_ph_test.csv | 約 30 秒 |
| 精度確認の集計 | 10_validation.py | tables/validation_*_accuracy.csv, validation_corrected.csv | 約 1 分 |

- 06・07b は `analysed_projects.txt` の 83 プロジェクトのファイルだけを読むので、ほかのコミットファイルが置いてあっても結果は変わらない。ブートストラップの乱数シードは固定（`common.SEED`）。
- 精度確認の目視の判定は `tables/validation_file_sample.csv`・`validation_bug_sample.csv`・`validation_bug_sample_blind.csv`・`validation_link_sample.csv` に記入済み。前の 2 つは 07 が作るが、既にあれば上書きしない。後の 2 つは手で作った入力で、10 が読む。
- ファイル分類の規則は [scripts/07_classify_files.py](scripts/07_classify_files.py)、紐づけの規則は [scripts/06_link_fix_commits.py](scripts/06_link_fix_commits.py) の冒頭にある。
