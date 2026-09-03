# vrm_replace

実写動画の人物を VRM キャラクターに置き換えるツール（Windows / NVIDIA GPU）。

*Replace a person in a video with a VRM avatar: click the person once, get an mp4. Windows + NVIDIA GPU. Pipeline: SAM 2 (mask) → MediaPipe (body + finger mocap) → ProPainter (clean plate) → three-vrm in headless Chrome (render, spring bones) → ffmpeg (composite).*

```
git clone <このリポジトリ>
cd vrm_replace
run.cmd      ← ダブルクリックでも可
```

1. `run.cmd` が Python / Node.js / NVIDIA ドライバを確認し、無ければ導入先を表示して止まります
2. 初回は依存ライブラリと **再配布不可のモデル**（SAM 2、MediaPipe、ProPainter の重み、Chrome）を自動で取得します（合計 1〜4GB、PyTorch が無い場合はさらに約 3GB）
3. GUI が開くので、動画と VRM を「参照...」で選んで「実行」
4. 最初のフレームが表示されるので、置き換える人物を **左クリック → Enter**（右クリックで除外点、`r` でやり直し）
5. あとは放置。進捗は GUI のログに出ます。終わると出力先に mp4 ができます

入力: mp4 / avi（ffmpeg が読めるものなら何でも）。3D モデル: VRM 1.0 / 0.x。

## 動作環境

| 項目 | 要件 |
|---|---|
| OS | Windows 10 / 11（64bit） |
| GPU | NVIDIA（VRAM 12GB 以上推奨。RTX 50 系対応） |
| Python | 3.10 以上（3.12 推奨）。[python.org](https://www.python.org/downloads/windows/) のインストーラで「Add python.exe to PATH」を ON |
| Node.js | 18 以上（[LTS](https://nodejs.org/)） |
| git | 任意（無ければ zip でダウンロードします） |
| ディスク | 依存 + モデルで約 6GB、作業領域は動画 1 分あたり数 GB |

CUDA Toolkit のインストールは不要です（PyTorch の CUDA 12.8 版を pip で入れます。NVIDIA ドライバは最新にしてください）。

## 使い方

### GUI（`run.cmd` / `python gui.py`）

- 動画 / VRM / 出力先を選んで「実行」。進捗ログが下に流れ、上部のステータスに現在の工程が出ます
- 「停止」で中断しても、同じ設定で「実行」すれば済んだ工程は飛ばして続きから進みます
- オプション

  | オプション | 内容 |
  |---|---|
  | 関節を人間の可動域に制限する | 肘・膝の逆曲がり、手首の折れ、上腕のねじれ等をクランプ（`stages/joint_limits.py`） |
  | 手が検出できない区間を前後の検出で補間する | 手のひらがフレームごとに別方向を向く「パタつき」対策。外れ値の検出も捨てる |
  | 指をトラッキングする | MediaPipe Hand Landmarker で指の曲げを推定 |
  | クリーンプレート | ProPainter で実写の人物を消す。OFF だと元映像の上に重ねるだけ（確認用、数分で終わる） |
  | 先頭 N フレームだけ処理 | 設定を試すとき |
  | やり直し開始工程 | 指定した工程以降を作り直す。上 3 つの ON/OFF を変えただけなら不要（自動でモーキャプから再計算） |

### CLI

```
python run.py --video input.mp4 --vrm chara.vrm --out result.mp4
    [--no-cleanplate] [--max-frames N] [--from N] [--auto] [--work DIR]
    [--joint-limits on|off] [--hand-interpolate on|off] [--fingers on|off]
```

```
python setup_env.py --check                    # 環境の状態確認
python setup_env.py --force                    # 依存・モデルを再導入
python tools/render_selftest.py --vrm x.vrm    # VRM を読んで 4 枚描画（GPU と座標系の確認。T ポーズ / 回転 / 腕上げ / 握り）
```

## 構成（工程ごとに別プロセス。落ちた工程から再開できる）

| 工程 | スクリプト | 使うもの | GPU |
|---|---|---|---|
| 0 ショット分割・フレーム展開 | `stages/s0_shots.py` | PySceneDetect, ffmpeg | - |
| 1 人物マスク（クリック→追跡） | `stages/s1_mask.py` | SAM 2 | 〜7GB |
| 2 モーキャプ（体・手首・指） | `stages/s2_mocap.py` | MediaPipe Pose / Hand（CPU） | - |
| 3 クリーンプレート | `stages/s3_cleanplate.py` | ProPainter | 〜15GB（設定で削減可） |
| 4 VRM にモーション適用→透過レンダー | `stages/s4_render.py` → `render/s4_threevrm.js` | three-vrm + ヘッドレス Chrome | 少 |
| 5 合成・音声・結合 | `stages/s5_final.py` | ffmpeg | - |

```
vrm_replace/
  run.cmd            Windows 用ランチャー（環境チェック → setup_env.py → gui.py）
  setup_env.py       依存・モデルの導入（冪等）
  gui.py / run.py    GUI / CLI のエントリ。run.py が各工程を subprocess で順に呼ぶ
  common.py          設定読み込み・共通ユーティリティ
  config.yaml        既定の設定（しきい値、VRAM 対策、ライト等）
  stages/            各工程。mocap_common.py に mocap.json の仕様、joint_limits.py に可動域表
  render/            three-vrm レンダラ（Node + Puppeteer）
  tools/             セルフテスト
  docs/HANDOFF.md    設計の詳細・数学・未検証事項（開発者向け）
  models/ third_party/ work/ out/   setup と実行時に作られる（git 管理外）
```

中間成果物は `work/<video名>/shot_NNN/` に書き出されます（`--work DIR` で変更可）。各工程の完了で `.done_N` を置き、再実行時はスキップします。

```
work/<video>/shot_000/
  frames/%06d.jpg      元フレーム
  select.json          クリック位置
  mask/%06d.png        人物マスク
  mocap.json           ボーン回転 + 腰位置（フォーマットは stages/mocap_common.py 冒頭）
  cleanplate/%06d.png  人物を消した背景
  render/f%04d.png     VRM の透過レンダー
  composite.mp4        合成済み
```

## 動作の考え方

- **モーキャプは全てカメラ座標系**。カメラが動いていても「カメラから見た体の位置・向き」がそのまま出るので合成のズレが出ません。代償として足すべりは起こり得ます（合成向けの割り切り）。
- `mocap.json` はバックエンド非依存の中間フォーマット（VRM humanoid ボーン名ごとの **グローバル回転（T ポーズ基準, OpenCV カメラ座標）** と腰位置）。s4 はこれを three.js 座標に変換し、three-vrm の正規化ボーンに「存在する最も近い祖先からの相対回転」として流し込みます。VRM に無いボーンは自動で飛ばします。
- **スプリングボーン（髪・スカート等の揺れもの）は three-vrm 標準で動きます**。ショット開始時は最初のポーズで `render_warmup_frames` 分だけ空回しして物理を落ち着かせます。
- VRM のスケールは実写人物の脚長（MediaPipe の world landmark から推定）に VRM の股関節高さを合わせます（`person_hip_height: auto`）。足の接地を優先するため、体型が違うと頭の高さはズレますが、実写人物はクリーンプレートで消えるので目立ちません。
- **指**: 各手首まわりをクロップして Hand Landmarker を掛け、片手 15 本の回転を mocap.json に入れます。mocap 側の基準は「手の方向にまっすぐ伸びた指」で、VRM ごとの実際のレスト方向（親指の角度・指の開き）との差は s4 が正規化リグから読んで補正します。
- **可動域制限**: 親相対の回転をボーン軸まわりの swing/twist に分解し、関節ごとの範囲でクランプして FK で戻します。全バックエンド共通です。
- s1 のクリックは 1 点で十分です。内部で MediaPipe が人物を特定し、bbox + 関節点に展開して SAM 2 に渡します。長尺は `sam2_chunk_frames` ごとに区切り、マスクで繋ぎます。
- ProPainter は全フレームを GPU/RAM に持つので `propainter_segment_frames`（既定 300）ごとに分割し、`propainter_max_side`（既定 960）に縮小して処理、**インペイントした領域だけ**を元フレームに貼り戻します。VRAM が 16GB を超えそうなら `propainter_subvideo` を下げてください。

### モーキャプのバックエンド（`config.yaml: mocap_backend`）

| backend | 精度 | 導入 | 備考 |
|---|---|---|---|
| `mediapipe`（既定） | 中。奥行き方向の腕・脚の曲げが甘い、遮蔽に弱い | 不要（同梱） | CPU。720p で 15〜20 fps。指も推定 |
| `smpl_npz` | SMPL 系推定器の品質そのまま | 自前 | `shot_NNN/mocap.npz`（`pose (F,22,3)` axis-angle, `transl (F,3)` カメラ座標, `K (3,3)`）を置く |
| `gvhmr` | 高 | GVHMR を別環境に構築 | `gvhmr_cmd` / `gvhmr_result` を設定。未検証 |

## config.yaml の主な項目

- `scene_threshold` ショット分割感度 / `min_shot_frames` 短すぎるショットの吸収
- `sam2_chunk_frames` SAM2 に一度に渡すフレーム数（RAM/VRAM）
- `focal_scale`（焦点距離 = focal_scale × max(幅, 高さ)。望遠素材なら上げる）, `mocap_smooth_sigma`, `use_hands`, `crop_margin`
- `use_fingers`, `hand_crop_scale`, `hand_min_crop_px`, `finger_smooth_sigma`, `finger_max_splay_deg`（指）
- `joint_limits`, `hand_interpolate`, `hand_outlier_deg`（GUI / CLI で上書き可。可動域の角度表は `stages/joint_limits.py: LIMITS`）
- `propainter_subvideo`, `propainter_neighbor`, `propainter_max_side`, `propainter_segment_frames`, `mask_dilate_px`
- `person_hip_height`（auto / 数値[m]）, `arm_offset_deg`（SMPL 系のみ）, `render_warmup_frames`, ライト各種
- `chrome_exe` GPU が効かない場合にインストール済み Chrome を指定 / `allow_software_gl`

## 既知の限界

- MediaPipe は奥行き方向の推定が甘く、走りのように肘を深く曲げて体の前に構える動きでは肘の曲げが浅く出ます（可動域制限では直りません。SMPL 系バックエンドで改善する種類の問題です）
- 手が小さく写る（〜50px）素材では指の握りが浅め・開き気味になります。奥側で見えない手はレストのままです
- 複数ショットがある場合、ショットごとにクリックを求めます（`--auto` で最初の点を使い回し）
- 人物がフレームアウトして再登場した場合、同一ショット内では追跡が切れます
- 人物の遮蔽（手前に物が来る）は扱いません。影は消えません（マスクは人物のみ）
- 表情は未対応。ライティングは平行光 1 灯 + 環境光（`config.yaml` の `light_dir` 等で調整）

## トラブルシュート

- `run.cmd` が「Python が見つからない」で止まる: python.org のインストーラで「Add python.exe to PATH」を ON にして入れ直すか、`py` ランチャーが使える状態にしてください
- GPU レンダリングが効かない（SwiftShader と表示される）: `config.yaml` の `chrome_exe` にインストール済み Chrome のパスを指定。それでも駄目なら `allow_software_gl: true`（遅くなります）
- ProPainter が極端に遅い: VRAM が溢れて共有メモリに退避しています。`propainter_subvideo` を 20 に、`propainter_max_side` を 720 に下げてください
- セットアップに失敗した: `logs/setup.log` を確認。`python setup_env.py --force` で再導入できます

## ライセンス

vrm_replace 本体は [Apache License 2.0](LICENSE) です。

`run.cmd` が取得する SAM 2、MediaPipe、three-vrm、ProPainter などはそれぞれのライセンスに従います（[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)）。
**ProPainter は非商用・研究目的限定（S-Lab License 1.0）** です。商用利用する場合はクリーンプレート工程を差し替えるか無効化してください。
