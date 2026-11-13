# ssh先で使う（GPU機はLANのUbuntu、手元はMacなど）

Isaac Sim・Claude Code・agentic-sim-world-genはssh先のGPU機（LANのUbuntu＋RTXなど）で動かし、手元のPC（Macなど、GPU不要）からは
sshのターミナルで頼み、ブラウザでプレビューだけを見る手順。手元には何もインストールしない（sshとブラウザだけ）。

GPU機の前に座って使うなら[全部手元で使う](use-local.md)。

```mermaid
flowchart LR
  subgraph local["手元の PC（Mac など）"]
    term["ターミナル<br/>（ssh クライアント）"]
    browser["ブラウザ"]
  end
  subgraph gpu["ssh 先の GPU 機（Ubuntu＋RTX）"]
    sshd["sshd :22"]
    subgraph tmux["tmux"]
      claude["claude（Claude Code）"]
    end
    subgraph mcp["agentic-sim-world-gen-mcp（claude の子プロセス）"]
      svc["検索・配置・USD 出力"]
      preview["プレビューの HTTP サーバー<br/>127.0.0.1:8765"]
    end
    isaac["Isaac Sim（headless）<br/>check のたびに起動して終了"]
    files[("&lt;作業フォルダ&gt;/scenes/&lt;名前&gt;")]
  end
  subgraph net["インターネット"]
    anthropic["Anthropic API"]
    hf["SimReady Central / S3"]
  end
  term -- "ssh :22" --> sshd
  sshd --> claude
  browser -- "http://127.0.0.1:8765<br/>（ssh -L で sshd 経由）" --> preview
  browser -- "サムネイル（HTTPS）" --> hf
  claude -- "HTTPS" --> anthropic
  claude -- "MCP（stdio）" --> svc
  svc -- "子プロセス" --> isaac
  svc -- "検索" --> hf
  isaac -- "USD・テクスチャ" --> hf
  svc --> files
  preview --> files
```

| つながり | ポート | 中身 |
|---|---|---|
| 手元のターミナル → GPU機 | 22（ssh） | tmuxの中のclaudeに日本語で頼む |
| 手元のブラウザ → GPU機のプレビュー | 8765を`ssh -L`で転送 | preview.html・配置図・checkの画像。GPU機の127.0.0.1にだけ開き、LANには公開しない |
| 手元のブラウザ → Hugging Face / S3 | 443 | 候補のサムネイル（URLのものはブラウザが直接取る） |

GPU機に入ってくる通信はsshだけでよい。

## 1. 必要なもの

| 場所 | 要るもの |
|---|---|
| GPU機 | Ubuntu＋RTX GPUとドライバー、Isaac Sim 6.1（pip版かスタンドアロン版）、Python 3.12以上とuv、Claude Code（ログイン済み）、tmux。画面は無くてよい |
| 手元 | sshクライアントとブラウザ |

## 2. GPU機の準備

以下の`<gpu-host>`は、手元からGPU機にsshするときのホスト名（`user@192.168.0.10`や`~/.ssh/config`の`Host`名）に読み替える。

1. Isaac Sim 6.1とagentic-sim-world-genを入れる（[全部手元で使う「2. インストール」](use-local.md#2-インストール)の1・2と同じ）。
2. 環境変数を`~/.profile`か、`~/.bashrc`の**冒頭**（`case $- in`で対話シェル以外を抜ける行より前）に書く。
   末尾に書くと`ssh <gpu-host> 'コマンド'`のような対話でないシェルには効かない：

   ```bash
   export ISAACSIM_PYTHON=~/isaacsim_venv/bin/python   # スタンドアロン版なら ISAACSIM_PATH=~/isaacsim
   export OMNI_KIT_ACCEPT_EULA=YES
   ```

   プレビューのポートは8765。使用中（別の作業フォルダのClaudeなど）なら、そのフォルダで前回使った番号（`~/.cache/agentic-sim-world-gen/preview_ports.json`）、
   それも使用中なら次の空き番号になる。作業フォルダが1つなら毎回8765なので、`ssh -L`は同じでよい。
   フォルダごとに番号を固定したいときは、`agentic-sim-world-gen workspace … --env AGENTIC_SIM_WORLD_GEN_PREVIEW=127.0.0.1:8766`のように .mcp.jsonに書く。
3. 作業フォルダを作る（リポジトリの外に）：

   ```bash
   cd agentic-sim-world-gen && .venv/bin/agentic-sim-world-gen workspace ~/agentic-sim-world-gen-ws
   ```

4. 確かめる（ssh越しに）：`out/study/check.png`ができればheadlessの描画が動いている。

   ```bash
   ssh <gpu-host> 'cd agentic-sim-world-gen && .venv/bin/agentic-sim-world-gen resolve examples/p0_study_room.json -o out/study \
     && .venv/bin/agentic-sim-world-gen compose out/study/scene.resolved.json && .venv/bin/agentic-sim-world-gen check out/study'
   ```

## 3. 最初のワールド

1. 手元から、プレビューのポートを転送して入り、tmuxの中でClaude Codeを起動する：

   ```bash
   ssh -L 8765:127.0.0.1:8765 <gpu-host>
   tmux new -s agentic-sim-world-gen          # 回線が切れても Claude の作業が止まらないように
   cd ~/agentic-sim-world-gen-ws && claude
   ```

   初回に`.mcp.json`の`agentic-sim-world-gen` MCPサーバーを承認する。
2. 頼む：

   > 6畳の書斎を作って。机と椅子、壁際に本棚

3. **プレビュー**：GPU機ではブラウザを開けないので、Claudeの返答にURL（`http://127.0.0.1:8765/<名前>/preview.html`）と`ssh -L`の手順が出る。
   そのURLを手元のブラウザで開く。ページはツールを呼ぶたびに自動で書き換わる（1秒ごとに確かめる）。
   ポートが8765でなければ、その番号で`ssh -L`し直す（別のターミナルで`ssh -N -L 8766:127.0.0.1:8766 <gpu-host>`でもよい）。
4. **check**：Isaac SimはGPU機でheadlessで動く（1回35〜50秒）。結果はプレビューの「Isaac Simチェック」に出る。
   物理のあと周りから撮った24枚をドラッグ（← → キー）で回せるので、3Dの画面の代わりになる。
5. **結果を開く**：GPU機の画面でIsaac Simを開くか、シーンを手元に持ってくる（次の節）。

## 4. 入力を渡す・結果を持ってくる

```bash
# 写真・動画・図面を GPU 機へ（Claude にはそのパスを渡す）
scp -r ~/photos/living <gpu-host>:~/agentic_sim_world_gen_inputs/living
# できたシーンを手元へ。手元の Isaac Sim で開くなら、アセットも同梱して出し直してから
ssh <gpu-host> 'cd agentic-sim-world-gen && .venv/bin/agentic-sim-world-gen compose ~/agentic-sim-world-gen-ws/scenes/study/scene.resolved.json --localize'
rsync -av <gpu-host>:~/agentic-sim-world-gen-ws/scenes/study ./
```

Claudeに「USDをローカル参照で出し直して」と頼んでもよい（MCP `compose_scene(localize=true)`）。
`--localize`なしのscene.usdaはアセットをURLで参照していて、ネットにつながればどこでも開ける。
写真から部屋を作るときは、Claudeが写真を切り出して画素の位置を読むので、写真はGPU機に置く（手元のパスは読めない）。

## 5. できないこと・注意

- **ライブモードは使えない**（checkを開いているIsaac Simの画面で行う）。GPU機の画面の前にいないと見えないので、headlessのcheckと、プレビューで回して見る画像で確かめる。
  頼んでもheadlessに切り替えて続ける（報告の`live_fallback`）。GPU機の前でも作業するなら[全部手元で使う](use-local.md)の形で。
- sshが切れるとプレビューも見えなくなるが、tmuxの中のClaudeは動き続ける。`ssh -L …`でつなぎ直して`tmux attach -t agentic-sim-world-gen`。
- プレビューのサーバーはClaude Codeのセッションの中で動く。`claude`を終えると止まるので、起動し直したら「プレビューを開いて」と頼む。
- 作業フォルダを複数同時に使うと、後から起動した方が別のポート（8766 …）になる。それぞれ`ssh -L`する（番号を固定するなら上の`AGENTIC_SIM_WORLD_GEN_PREVIEW`）。

## 6. トラブルシューティング

| 症状 | 対処 |
|---|---|
| ブラウザで「接続できない」 | `ssh -L`のポートが返答のURLと同じか。sshが切れていないか。`claude`が起動中か |
| プレビューが更新されない | ページ上部の更新が止まっていれば、sshかclaudeが止まっている。つなぎ直して「プレビューを開いて」 |
| `Address already in use`（`ssh -L`のとき） | 手元でそのポートが使用中（別の転送など）。手元側の番号だけ変える：`ssh -L 9765:127.0.0.1:8765 <gpu-host>`で`http://127.0.0.1:9765/…` |
| `Isaac Sim not found` / 止まったまま | `ISAACSIM_PYTHON`（か`ISAACSIM_PATH`）と`OMNI_KIT_ACCEPT_EULA=YES`が、`claude`を起動したシェルに入っているか（`echo $ISAACSIM_PYTHON`） |
| GPU機の画面にブラウザが開いた | `DISPLAY`を引き継いだシェル（`ssh -X`など）で起動した。`unset DISPLAY`してから`claude` |
| スクショが真っ黒 | `nvidia-smi`でGPUが見えるか。ドライバーがRTX描画に対応しているか |
| ほかの症状 | [Getting Started「9. トラブルシューティング」](getting-started.md#9-トラブルシューティング) |
