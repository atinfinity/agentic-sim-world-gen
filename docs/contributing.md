# 開発に参加する

Issue・プルリクエストは日本語でも英語でも歓迎します。
個人が空き時間に保守しているプロジェクトなので、返事・レビュー・リリースの時期は約束できません。

使うだけなら [はじめに](getting-started.md) で足ります。このページはコードを変える人向けです。

## 1. セットアップとテスト

```bash
git clone https://github.com/atinfinity/agentic-sim-world-gen.git
cd agentic-sim-world-gen
uv sync --extra usd --extra mcp --extra dxf --extra ifc --extra video --extra hf --extra dev
uv run pytest -q              # オフラインのテスト（約 2 分）
uv run pytest -q -m network   # 任意：SimReady Central・Poly Haven の実 API を呼ぶ
```

CI はテストを 2 通りで流す：README の手順で入れる extra（`usd`・`hf`・`mcp`・`dev`）だけと、`taste` 以外の全部。
ほかの extra が要るテストは、それが無ければ飛ばす（`pytest.importorskip`）。

Isaac Sim でのシーンのチェック（`check`）には NVIDIA RTX の GPU と Linux の Isaac Sim 6.1 が要る。それ以外
（検索・レイアウト・USD・プレビュー）は GPU 無しで動く。Isaac Sim で確かめられないプルリクエストでも構わない
（その旨を説明に書く）。

MCP ツールの引数や docstring を変えたら、一覧を作り直す：`uv run python scripts/make_tool_reference.py`
（[tools.md](https://github.com/atinfinity/agentic-sim-world-gen/blob/main/.claude/skills/world-gen/tools.md)
が最新かはテストが確かめる）。

## 2. どこに何があるか

[仕組み](architecture.md) が流れをコードと同じ名前で図にしている。規約（座標系・USD の構造・環境変数）は
[CLAUDE.md](https://github.com/atinfinity/agentic-sim-world-gen/blob/main/CLAUDE.md)。

| 何 | コード |
|---|---|
| SceneSpec（全入力が収束する形式。例は `examples/`） | `src/agentic_sim_world_gen/scene/spec.py` |
| 間取り図（JSON / DXF / IFC） | `src/agentic_sim_world_gen/scene/plan.py`・`src/agentic_sim_world_gen/plan/` |
| 屋外 | `src/agentic_sim_world_gen/scene/site.py` |
| 床・壁・照明 | `src/agentic_sim_world_gen/scene/looks.py` |
| 足りなかったアセット | `src/agentic_sim_world_gen/missing.py` |
| 合成データ | `src/agentic_sim_world_gen/sdg.py`・`scripts/isaac/sdg.py` |
| Isaac Sim のチェック | `scripts/isaac/check_scene.py` |
| CLI と MCP ツール（同じ操作） | `src/agentic_sim_world_gen/service.py`・`src/agentic_sim_world_gen/mcp_server.py` |
| Claude の対話の手順 | `.claude/skills/world-gen/` |

`agentic-sim-world-gen check` は Isaac Sim の python で `scripts/isaac/check_scene.py` を動かしている。直接呼ぶなら：

```bash
~/isaacsim/python.sh scripts/isaac/check_scene.py out/study/scene.usda \
    --screenshot out/study/check.png --report out/study/check.json [--gif out/study/check.gif]
```

## 3. プルリクエスト

- 1 つのプルリクエストに 1 つの話題。`tests/` のテストを足すか直す。
- 見て分かる変更（レイアウト・プレビュー・レンダリング）は、説明にスクリーンショットか GIF を貼る。
- **サードパーティのアセットファイル**（USD・テクスチャ・MDL・メッシュ）や、再配布してよいか分からない物
  （非商用のアセット・人の写った写真を含む）は**コミットしない**。アセットは URL か `local:` のフォルダで参照する。
- プルリクエストを出すと、その変更は Apache License 2.0
  （[LICENSE](https://github.com/atinfinity/agentic-sim-world-gen/blob/main/LICENSE)）で提供することに同意したことになる。
  CLA や sign-off（DCO）は要らない。

## 4. 問題の報告

Issue のテンプレートを使ってほしい。指示の文、アセット源（`AGENTIC_SIM_WORLD_GEN_SOURCES`）、バージョン、OS を書く。
人の写った写真・動画や、共有してよいか分からないアセットのスクリーンショットは添付しない。セキュリティの問題は
[SECURITY.md](https://github.com/atinfinity/agentic-sim-world-gen/blob/main/SECURITY.md)。

## 5. プロジェクトページ

プロジェクトページ（<https://atinfinity.github.io/agentic-sim-world-gen/ja/>）は [Zensical](https://zensical.org/) で作り、
`main` への push のたびに `.github/workflows/pages.yml` が公開する。同じページの 2 つのサイトがある：英語
（`zensical.en.toml`：`README.md` と `docs/en/` の手引き）と、`/ja/` の日本語（`zensical.toml`：`README.ja.md` と `docs/` の手引き）。
`scripts/build_site.py` が `build/` に組み立てるので、直すのは README と `docs/`。手引きを直したら両方の言語を直す。手元で見るには：

```bash
uv pip install zensical==0.1.1
python scripts/build_site.py --serve   # http://127.0.0.1:8000/agentic-sim-world-gen/ja/
```

## 6. GitHub Actions

`.github/workflows/` が使う action は `.github/workflows/actions.lock`
（[workflow lockfile](https://gh.io/actions-lockfile)）でコミットに固定している。作るのは GitHub CLI の拡張
[gh-actions-lock](https://github.com/github/gh-actions-lock)。手では直さない。`uses:` を足す・変える（ワークフローを足す）ときは

```bash
gh extension install github/gh-actions-lock   # 初回だけ
gh actions-lock            # 版のタグを省略せずに書き（@v7 -> @v7.0.1）、actions.lock を作り直す
gh actions-lock --verify   # 確かめる
```

を流し、ワークフローと `actions.lock` を一緒にコミットする。CI の `lockfile` ジョブがプルリクエストごとに
`gh actions-lock --verify` を流し、リポジトリの Require lockfile のポリシーが lockfile に無い action のワークフローを止める。

Dependabot（`.github/dependabot.yml`）は週に 1 回、エコシステム（action と `uv.lock`）ごとに 1 本のプルリクエストを作る。
action の更新では同じプルリクエストで `actions.lock` も直す。
