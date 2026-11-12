# 通しのベンチマーク

決まった指示を `claude -p` で最初から最後まで流し、1 本ごとに「いくら掛かって、何ができたか」を記録する。
変更の前後で同じ指示を流して比べるための物（ターン数・費用・言語ずれ・check の結果）。

> [!WARNING]
> 実際に Claude Code（`claude -p`）を動かすので、**利用料が掛かる**（1 本で数ドル、全部を流すと数十ドルになることがある）。
> 上限は 1 本あたり `--max-budget`（既定 $15）。まず `--cases` で 1 本だけ流して額を確かめること。

```bash
python scripts/e2e_eval/run.py --list                                   # 指示の一覧
python scripts/e2e_eval/run.py ~/agentic-sim-world-gen-e2e/main --cases nl_study     # 1 本だけ
python scripts/e2e_eval/run.py ~/agentic-sim-world-gen-e2e/main --repeat 2           # 全部を 2 回ずつ（ばらつきを見る）
python scripts/e2e_eval/report.py ~/agentic-sim-world-gen-e2e/branch --before ~/agentic-sim-world-gen-e2e/main --tools --context
```

- 実行先（`run_dir`）は **リポジトリの外**。指示ごとに `<run>/<指示>__<回>/ws` に作業フォルダ（`agentic-sim-world-gen workspace`）を作り、
  好みのファイルも別にする（`AGENTIC_SIM_WORLD_GEN_PREFERENCES`。利用者の好みが試行に効かず、試行が好みを書き換えない）。ブラウザは開かない（`AGENTIC_SIM_WORLD_GEN_BROWSER=true`）。
- 1 本 = Claude Code の 1 セッション。1 ターン目は `--session-id`、2 ターン目からは `--resume`。各ターンの stream は `turn<k>.jsonl`（標準エラーは `.err`）。
- 結果は `<run>/results.jsonl` に 1 行ずつ、実行の条件（commit・未コミットの有無・モデル・claude のバージョン・Isaac Sim の有無）は `run.json`。
  済んだ指示は飛ばすので、途中で止めてもう一度流してよい（やり直すなら results.jsonl のその行を消す）。
- 使えるツールは agentic-sim-world-gen の MCP と Read・Skill・Glob・Grep だけ。それ以外を使おうとした回数は `permission_denials`。
- 既定の上限は 1 ターン 3600 秒・$15（`--timeout`・`--max-budget`）。モデルは `--model`、サーバーの環境変数は `--env KEY=VALUE`。
- `--judge`：`check.png` を別の `claude -p` に見せて依頼との合い方を 0〜5 で採点する（任意。採点者に渡すのは画像と依頼だけ）。
- editable install なので、**測るのは今チェックアウトしているブランチ**。比べる時はブランチを切り替えて別の `run_dir` に流す。

## 数字の意味（`metrics.py`）

| 列 | 意味 |
|---|---|
| ターン | `result.num_turns` の合計（API の往復） |
| ツール / エラー | ツール呼び出しの数、`is_error` の結果の数（名前別は `tools`・`tool_errors_by`） |
| 費用 $ | `total_cost_usd`。セッションの累計なので、ターンごとの差を足す |
| 時間 s | `duration_ms` の合計 |
| 最大 context | 1 回の要求の input + cache トークンの最大（`context_first` は最初の要求） |
| 圧縮 | `compact_boundary` の数 |
| 言語ずれ | 利用者の言語でない途中報告の数（仮名・漢字が無く英単語が 3 語以上。コード・パスは数えない）。 |
| check | 最後の Isaac Sim チェックの判定（draft / needs_fix / passed。走っていなければ —） |
| issues / usability | 最後の status の件数 |
| 充足率 | `requirements_summary` の met / total |
| 代用 | `substitute` の付いた物の数（うちカタログに無い物は `not_in_catalog`） |

`report.py --context`：会話に入った文字の内訳を `turn<k>.jsonl` から出す（`metrics.context_breakdown`）。ツールの結果は
ツール名別、Read はファイル名別、ほかにエージェントの文・ツールの引数・利用者とハーネスの文（スキルの本文はここ）。単位は文字と画像の枚数で、
トークンではない（システムプロンプトとツール定義はログに無く、`context_first` に入っている）。圧縮で消えた分も数える。

`cases_auto_fix.json`（`--file`）は自動の手直しが起きやすい 3 本。あり・なしは `--env AGENTIC_SIM_WORLD_GEN_AUTO_FIX=0` で比べる。

指示は `cases.json`（12 本：文章 9・参考画像 1・写真 2、うち複数ターン 4）。足す時は `{id, lang, kind, turns[], inputs?}`、
写真は `inputs` にリポジトリ内のパスを書き、指示の中では `{inputs}/<ファイル名>` と書く。
