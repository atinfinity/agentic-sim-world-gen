# 全部手元で使う（Ubuntu＋RTXの1台）

Isaac Sim・Claude Code・プレビューを、画面のある手元のUbuntu（RTX GPU）1台で動かす手順。
Isaac Simの3D画面・プレビュー・ターミナルを同じ画面で見られる。checkを開いているIsaac Simの画面で行う「ライブモード」も、この形でだけ使える。

GPU機が別の場所にあり、手元（Mac等）からはsshで入る場合は[ssh先で使う](use-ssh.md)。

```mermaid
flowchart LR
  subgraph pc["手元の Ubuntu（RTX GPU、画面あり）"]
    term["ターミナル<br/>claude（Claude Code）"]
    subgraph mcp["agentic-sim-world-gen-mcp（claude の子プロセス）"]
      svc["検索・配置・USD 出力・<br/>ライセンス"]
      preview["プレビューの HTTP サーバー<br/>127.0.0.1:8765"]
    end
    browser["ブラウザ<br/>（プレビュー）"]
    isaac["Isaac Sim<br/>headless：check のたびに起動<br/>GUI：python_server 127.0.0.1:8226"]
    files[("シーンのフォルダ<br/>&lt;作業フォルダ&gt;/scenes/&lt;名前&gt;")]
  end
  subgraph net["インターネット"]
    anthropic["Anthropic API"]
    hf["SimReady Central<br/>（検索 API・USD・サムネイル）"]
    s3["Isaac Sim アセット<br/>（公開 S3）"]
  end
  term -- "MCP（stdio）" --> svc
  term -- "HTTPS" --> anthropic
  svc -- "開く（xdg-open）" --> browser
  browser -- "http://127.0.0.1:8765" --> preview
  svc -- "検索（HTTPS）" --> hf
  svc -- "子プロセス / ライブ :8226" --> isaac
  isaac -- "USD・テクスチャ" --> hf
  isaac --> s3
  svc -- "書く" --> files
  preview -- "読む" --> files
```

ポートは2つで、どちらも127.0.0.1（外には開かない）。

| ポート | 何 | 変える変数 |
|---|---|---|
| 8765 | プレビューのHTTPサーバー（agentic-sim-world-gen-mcpの中で動く） | `AGENTIC_SIM_WORLD_GEN_PREVIEW` |
| 8226 | ライブモードで、開いているIsaac Simにcheckを送る先（python_server） | `AGENTIC_SIM_WORLD_GEN_ISAAC_LIVE` |

## 1. 必要なもの

| | 用途 |
|---|---|
| Ubuntu＋RTX GPUとドライバー、デスクトップ（X / Wayland） | Isaac Simの描画、ブラウザ |
| Isaac Sim 6.1（pip版かスタンドアロン版） | 物理チェック・スクショ・写真比較・合成データ |
| Python 3.12以上と[uv](https://docs.astral.sh/uv/) | agentic-sim-world-gen本体 |
| [Claude Code](https://claude.com/claude-code)（ログイン済み） | 日本語の指示からシーンを作る |
| ffmpeg（任意、`sudo apt install ffmpeg`） | 動画からコマを抜く |

## 2. インストール

1. Isaac Sim 6.1を入れる。pip版なら専用のvenvに`pip install "isaacsim[all,extscache]==6.1.0.0"`（NVIDIAの手順に従う）。
   スタンドアロン版なら`~/isaacsim`に展開する。
2. agentic-sim-world-genを入れる（[Getting Started「2. インストール」](getting-started.md#2-インストール)と同じ）：

   ```bash
   git clone https://github.com/atinfinity/agentic-sim-world-gen.git
   cd agentic-sim-world-gen
   uv sync --extra usd --extra hf --extra mcp --extra dev
   ```

   **Isaac SimのPythonには何もpip installしない**（Isaac Simは自前のpxrを持っていて、usd-coreが混ざると壊れる）。
3. 環境変数を`~/.bashrc`などに書く：

   ```bash
   export ISAACSIM_PYTHON=~/isaacsim_venv/bin/python   # pip 版の venv の python。スタンドアロン版なら ISAACSIM_PATH=~/isaacsim
   export OMNI_KIT_ACCEPT_EULA=YES                       # 非対話で Isaac Sim を動かすのに必要（NVIDIA Omniverse EULA に同意）
   ```

   この形で要るのはこの2つだけ。アセットの検索元を変えるなら`AGENTIC_SIM_WORLD_GEN_SOURCES`（[Getting Started「3. 環境変数」](getting-started.md#3-環境変数)）。

4. 確かめる：`out/study/check.png`ができればIsaac Simが動いている（初回はアセットの取得とシェーダーのコンパイルで数分）。

   ```bash
   source .venv/bin/activate
   agentic-sim-world-gen resolve examples/p0_study_room.json -o out/study
   agentic-sim-world-gen compose out/study/scene.resolved.json
   agentic-sim-world-gen check out/study
   ```

## 3. 作業フォルダを作る

ワールドを作るときは、リポジトリの外の作業フォルダでClaude Codeを起動する（リポジトリの中だと開発用のCLAUDE.mdを毎ターン読むので、料金が高く圧縮も早い）。

```bash
agentic-sim-world-gen workspace ~/agentic-sim-world-gen-ws     # .mcp.json・スキル world-gen（リポジトリへのリンク）・短い CLAUDE.md
cd ~/agentic-sim-world-gen-ws && claude
```

初回に`.mcp.json`の`agentic-sim-world-gen` MCPサーバーを承認する。シーンは`~/agentic-sim-world-gen-ws/scenes/<名前>/`にできる。
オプション（`--env`・`--allow-tools`など）は[Getting Started「4.」](getting-started.md#4-claude-codeで最初のワールド)。

## 4. 最初のワールド

1. Claudeに頼む：

   > 6畳の書斎を作って。机と椅子、壁際に本棚

2. **プレビュー**：候補が出たところで、Claudeがブラウザでプレビュー（`http://127.0.0.1:8765/<名前>/preview.html`）を開く。
   開かないときはClaudeに「プレビューを開いて」と頼むか、返答のURLを開く。ページはツールを呼ぶたびに自動で書き換わる。
   別のブラウザで開きたいときは`AGENTIC_SIM_WORLD_GEN_BROWSER="chromium --app={url}"`のように指定する。
3. **check**：ClaudeがUSDを出して（`compose_scene`）、Isaac Simで物理チェックとスクショを撮る（`check_in_isaac`、headlessで約35秒）。
   結果はプレビューの「Isaac Simチェック」に出る（周りから撮った24枚をドラッグで回せる）。
4. **結果を開く**：Isaac Simで`scene.usda`を開く。

   ```bash
   isaacsim isaacsim.exp.full.kit          # pip 版。スタンドアロン版は ~/isaacsim/isaac-sim.sh
   ```

   File → Openで`~/agentic-sim-world-gen-ws/scenes/<名前>/scene.usda`。Playで物理が動く（Z-up、メートル単位、物は`/World/Objects/<id>`）。

### ライブモード（この形だけ）

Isaac Simをpython_server付きで起動しておくと、checkがその画面で行われ、問題の物が選択状態になる。再チェックは約8秒（headlessは毎回約34秒）。

```bash
isaacsim isaacsim.exp.full.kit --enable isaacsim.code_editor.python_server   # 127.0.0.1:8226 で待ち受け
```

Claudeには「開いているIsaac Simでチェックして」と頼む（MCP `check_in_isaac(live=true)`、CLI `agentic-sim-world-gen check <scene> --live`）。
起動していないとき、物が重なっているとき（GUIのGPU物理が壊れることがある）はheadlessで実行される（報告の`live_fallback`）。
python_serverは任意のコードを実行するので、127.0.0.1のまま使う。

## 5. 入力と結果

- 写真・動画・図面は、手元のパスをそのまま渡す（「~/photos/living.jpgの部屋を作って」）。出典は隣の`<stem>.source.json`（[Getting Started「6. 他の入力」](getting-started.md#6-他の入力)）。
- できたシーンはそのまま手元のIsaac Simで開ける。別のPCに渡すときは、アセットも同梱したUSDにしてからフォルダごと渡す：
  「USDをローカル参照で出し直して」（MCP `compose_scene(localize=true)`、CLI `agentic-sim-world-gen compose <scene>/scene.resolved.json --localize`）。

## 6. トラブルシューティング

| 症状 | 対処 |
|---|---|
| プレビューが開かない | 返答のURLを手で開く。`DISPLAY`の無いシェル（sshで入った端末など）から`claude`を起動すると開けない |
| プレビューのURLのポートが8765でない | 8765が使用中（別のClaudeのセッションなど）なら、このフォルダで前に使った番号か、次の空き番号で起動する。`AGENTIC_SIM_WORLD_GEN_PREVIEW=127.0.0.1:8770`で固定できる |
| プレビューが更新されない | Claude Codeを終えるとプレビューのサーバーも止まる。起動し直して「プレビューを開いて」 |
| ライブcheckがheadlessで動いた | `live_fallback`の理由を見る。Isaac Simを`--enable isaacsim.code_editor.python_server`で起動したか、物が重なっていないか |
| checkで「physics did not run」 | 物どうしが接したりめり込んだりして吹き飛び、GPU物理が止まった（CUDA error）。同じシーンは何度やっても同じ。`blew_up`の物を数cm離す（キャスター付きの椅子を隙間なしで並べた等）。ライブならIsaac Simも再起動する |
| ほかの症状 | [Getting Started「9. トラブルシューティング」](getting-started.md#9-トラブルシューティング) |
