# CLAUDE.md — agentic-sim-world-gen

新しいセッションはまずこのファイルを読むこと。詳細は docs/ 以下。利用者向けの手順は docs/getting-started.md。

## 目的

自然言語・写真・間取り図の指示から、エージェントがアセットを選んで配置し、Isaac Sim 用ワールド（USD）を生成する。
アセット源：SimReady Central（HF）、Isaac Sim 同梱アセット、サードパーティ USD フォルダ（`local:<dir>`）。

## 方針

- 形態は MCP サーバー＋スキル（Claude Code 用）。CLI も同じ処理（`service.py`）を呼ぶ。
- 用途はロボットシム・デジタルツイン・デモ・合成データ生成。シーンは屋内・倉庫・工場・データセンター・屋外。
- 入力は自然言語・写真・動画・間取り図（JSON / DXF / IFC / 画像）。
- 該当アセットが無い → 近いもので代用し、報告する（3D の生成はしない）。
- **ライセンスは必ず記録する**（アセットごとに違う。非商用の物もある）。
- 指示は日本語でも英語でもよい。検索クエリはエージェントが **英訳＋同義語** にする（英語の方が精度が高い）。
- プレビュー：候補アセットのサムネイル一覧と 2D の上面図（ブラウザ）。

## アーキテクチャ

図での説明は [docs/architecture.md](docs/architecture.md)（コードと名前を合わせてある。仕組みを変えたらそちらも直す）。手引き（docs/*.md）には英語版 docs/en/ があり、直すときは両方直す。

```
入力（NL / 写真 / 間取り図） → SceneSpec(JSON) → AssetProvider で候補検索 → ResolvedScene
  → プレビュー(1)(2) → USD 合成(pxr) → 静的チェック → Isaac Sim チェック(物理 settle + スクショ) → 修正ループ
```

- `src/agentic_sim_world_gen/service.py`：CLI と MCP 共通の操作（シーン = scene.resolved.json のあるディレクトリ）
- `src/agentic_sim_world_gen/mcp_server.py`：MCP サーバー（mcp SDK 2.x の `MCPServer`。1.x の FastMCP ではない）。`.mcp.json` で登録
- `src/agentic_sim_world_gen/scene/spec.py`：SceneSpec / ResolvedScene（全入力が収束する中間形式）
- `src/agentic_sim_world_gen/assets/providers/`：`AssetProvider` プロトコル。`simready_central.py`（HF の検索 API）、
  `isaac_index.py`（Isaac 同梱アセットのオフライン索引 `src/agentic_sim_world_gen/data/isaac_6.1.jsonl.gz`。
  作り直しは `scripts/build_isaac_index.py`）、`local_folder.py`（サードパーティ USD フォルダ）、`composite.py`（複数ソースを統合、同名ファイルは先勝ちで重複排除）
- `src/agentic_sim_world_gen/layout/room.py`：RoomGeometry（矩形の部屋も間取りも同じ形：壁・壁面・障害物・ゾーン）
- `src/agentic_sim_world_gen/scene/plan.py`・`src/agentic_sim_world_gen/plan/`：間取り（壁中心線・開口・ゾーン）と JSON / DXF / IFC 読み込み
- `src/agentic_sim_world_gen/layout/validate.py`：はみ出し・重なり（3D, SAT）・天井高・壁/ドア前チェック
- `src/agentic_sim_world_gen/layout/solver.py`：関係配置。`frame.py`（正面方向の規約）、`array.py`（繰り返し配置）
- `src/agentic_sim_world_gen/usd/compose.py`：USD 合成
- `src/agentic_sim_world_gen/preview/`：candidates.html / layout.svg
- `scripts/isaac/check_scene.py`：Isaac Sim の python.sh（pip 版はその python）で実行する検証スクリプト（6.1.0 で動作確認済み）

## 規約

- ステージ：**Z-up、meters（mpu=1）**、defaultPrim `/World`。部屋内部は x∈[0,width], y∈[0,depth]、床上面 z=0。
- オブジェクト：`/World/Objects/<id>`（Xform）の子 `Asset` に参照とバリアント選択（アセットのルート変換を残すため）。外側の xformOps = translate, rotateZ(yaw), scale(mpu), rotateX:upAxis(Y-up の時 90°), translate:pivot(-pivot)。
  pivot は bbox 底面の footprint 中心。
- サイズ指定 `min_size` / `max_size` は (footprint 長辺, 短辺, 高さ)。向きに依存しない（`fits()`）。
- アセット prim の customData `agentic_sim_world_gen:{source, uri, license, label}` に出典・ライセンスを記録。
- PhysX バリアント（`PhysX=Enabled`）を選択。SimReady には MuJoCo / Newton バリアントもある。
- usd-core は **Isaac Sim の Python に入れない**（Isaac Sim 側は自前の pxr を使う）。
- テスト：`pytest`（オフライン）、`pytest -m network`（実 API）。
- 環境変数：`AGENTIC_SIM_WORLD_GEN_SOURCES`（既定 `simready-central,isaac,polyhaven`。`+local:<dir>` で既定に足す・`default` は既定の 3 つ・`+` なしの並びは置き換え。status の `sources` に使った物・外した物）、`ISAAC_ASSETS_ROOT`（Isaac アセットの
  ローカル/Nucleus ルート。未設定なら公開 S3 の URL を参照）、`AGENTIC_SIM_WORLD_GEN_ISAAC_INDEX`（索引ファイルの差し替え）、`AGENTIC_SIM_WORLD_GEN_MDL_INDEX`（MDL 索引の差し替え）・`AGENTIC_SIM_WORLD_GEN_MDL=0`（Isaac Sim 同梱の MDL を検索しない。床・壁は CC0 の PBR か色だけ）、`AGENTIC_SIM_WORLD_GEN_PBR_INDEX`（CC0 PBR 索引の差し替え）・`AGENTIC_SIM_WORLD_GEN_PBR=0`（PBR を検索しない）、`AGENTIC_SIM_WORLD_GEN_POLYHAVEN_INDEX`（Poly Haven のモデル索引の差し替え）、`AGENTIC_SIM_WORLD_GEN_ASSET_CARDS`（アセットごとのカード＝見て確かめた種類・正面・取り付け方と落下テストの結果、の差し替え。`0` で使わない）、
  `AGENTIC_SIM_WORLD_GEN_AUTO_FIX=0`（保存時の自動の手直しをしない）、`AGENTIC_SIM_WORLD_GEN_MISSING=0`（不足アセットの記録をしない）、`AGENTIC_SIM_WORLD_GEN_OUT`、`AGENTIC_SIM_WORLD_GEN_EXAMPLES`（ギャラリーの作例のフォルダ。既定はリポジトリの `examples/`）、`ISAACSIM_PATH`、`ISAACSIM_PYTHON`（pip 版 Isaac Sim の python。python.sh より優先）、`AGENTIC_SIM_WORLD_GEN_CACHE`（既定 `~/.cache/agentic-sim-world-gen`）、`AGENTIC_SIM_WORLD_GEN_SEARCH_CACHE_HOURS`（SimReady Central の検索結果を残す時間、既定 24・`0` で残さない）・`AGENTIC_SIM_WORLD_GEN_SEARCH_WORKERS`（同時に投げる検索の数、既定 4・`1` で順番に）、`AGENTIC_SIM_WORLD_GEN_ISAAC_LIVE`（ライブ check の接続先、既定 `127.0.0.1:8226`）、`AGENTIC_SIM_WORLD_GEN_ISAAC_WORKER=1`（Isaac Sim を起動したままにして check を続けて流す）・`AGENTIC_SIM_WORLD_GEN_ISAAC_WORKER_IDLE`（仕事が無いと止まるまでの秒数、既定 600）・`AGENTIC_SIM_WORLD_GEN_ISAAC_WORKER_JOBS`（この回数で起動し直す、既定 40）、`AGENTIC_SIM_WORLD_GEN_PREVIEW`（プレビューサーバー、既定 `127.0.0.1:8765`）、`AGENTIC_SIM_WORLD_GEN_BROWSER`（プレビューを開くコマンド）、`AGENTIC_SIM_WORLD_GEN_TASTE`（テイスト検査のモデル名・パス、`0` で無効）・`AGENTIC_SIM_WORLD_GEN_TASTE_DOWNLOAD=1`（モデルの取得を許す）・`AGENTIC_SIM_WORLD_GEN_TASTE_DEVICE`（既定 cpu）、`AGENTIC_SIM_WORLD_GEN_DEPTH_PYTHON`（MoGe の venv の python）・`AGENTIC_SIM_WORLD_GEN_DEPTH_DOWNLOAD=1`・`AGENTIC_SIM_WORLD_GEN_DEPTH_MODEL`。

## 開発の進め方

- 手順は [docs/contributing.md](docs/contributing.md)（英語 [docs/en/contributing.md](docs/en/contributing.md)、CONTRIBUTING.md はその案内）。変更ごとにブランチを切って PR を作る（PR に GIF・画像などの動作の様子を貼る）。
- 仕組みを変えたら docs/architecture.md も直す。MCP ツールの説明を変えたら `python scripts/make_tool_reference.py` で
  `.claude/skills/world-gen/tools.md` を作り直す（`tests/test_mcp_schema.py` が確かめる）。
- Isaac Sim での確認は RTX GPU の Linux 機（pip 版なら `ISAACSIM_PYTHON=<Isaac Sim の venv>/bin/python`、`OMNI_KIT_ACCEPT_EULA=YES`）。
- ワールドを作るだけなら、このリポジトリの外の作業フォルダ（`agentic-sim-world-gen workspace <dir>`、docs/getting-started.md 4）で起動する
  （ここで起動するとこのファイルを毎ターン読む）。
