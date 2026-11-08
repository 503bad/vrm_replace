# Replace2VRM（vrm_replace）

実写動画の人物を VRM キャラクターに置き換えるツール（Windows / NVIDIA GPU）。

*Replace a person in a video with a VRM avatar: click the person once, get an mp4. Windows + NVIDIA GPU. Pipeline: MediaPipe (person mask, body + finger mocap) → three-vrm in headless Chrome (render, spring bones) → NVIDIA Optical Flow (GPU hardware) + OpenCV inpainting (clean plate) → ffmpeg (composite). Two presets: quality (standard) / speed (lightweight). AI models are limited to ones whose weights and training data both allow commercial use (or whose training data is undisclosed and the weights allow commercial use).*

```
git clone https://github.com/503bad/vrm_replace.git
cd vrm_replace
run.cmd      ← ダブルクリックでも可
```

1. `run.cmd` が Python / Node.js / NVIDIA ドライバを確認し、無ければ導入先を表示して止まります
2. 初回は依存ライブラリと **再配布不可のモデル**（MediaPipe のモデル、Chrome）を自動で取得します（合計 1GB 程度、PyTorch が無い場合はさらに約 3GB）
3. GUI が開くので、動画と VRM を「参照...」で選んで「実行」
4. 最初のフレームが表示されるので、置き換える人物を **左クリック → Enter**（右クリックで除外点、`r` でやり直し）
5. あとは放置。進捗は GUI のログに出ます。終わると出力先に mp4 ができます

入力: mp4 / avi（ffmpeg が読めるものなら何でも）。3D モデル: VRM 1.0 / 0.x。

## 動作環境

| 項目 | 要件 |
|---|---|
| OS | Windows 10 / 11（64bit） |
| GPU | NVIDIA のみ（Turing / RTX 20 系以降が必須。NVIDIA 以外の GPU では起動時に終了します。VRAM 4GB 以上。RTX 50 系対応） |
| Python | 3.10 以上（3.12 推奨）。[python.org](https://www.python.org/downloads/windows/) のインストーラで「Add python.exe to PATH」を ON |
| Node.js | 18 以上（[LTS](https://nodejs.org/)） |
| git | 任意（無ければ zip でダウンロードします） |
| ディスク | 依存 + モデルで約 6GB、作業領域は動画 1 分あたり数 GB |

CUDA Toolkit のインストールは不要です（PyTorch の CUDA 12.8 版を pip で入れます。NVIDIA ドライバは最新にしてください）。

## 使い方

### GUI（`run.cmd` / `python gui.py`）

- **動画と VRM をウィンドウにドロップ**するだけ（どこに落としても拡張子で振り分けます）。カードをクリックしてファイルを選ぶこともできます
  - 動画は解像度・fps・長さとサムネイル、VRM はバージョン（0.x / 1.0）・作者・**利用条件（商用利用・アバターの利用の可否）**を表示します。不可の項目は赤
  - 出力先は `out/<動画名>_vrm.mp4` に自動で決まります（「変更」で指定も可）
- 「START」を押すと、ショット分割のあと**画面の中で人物をクリック**します（左クリック: 人物、右クリック: 除外、R: やり直し、Enter: 確定、Esc: 中止）。あとは放置で完了します
- 右側に工程ごとの進み具合・経過時間・残り時間、完了すると結果のプレビューと「再生」「フォルダを開く」が出ます。下の LOG で詳しいログを開けます
- 「STOP」で中断しても、同じ設定で「START」すれば済んだ工程は飛ばして続きから進みます
- 画面は `app/ui`（HTML/CSS/JS、pywebview + WebView2）、処理は `app/backend.py`。見た目の決まりは `app/ui/app.css` の冒頭
- オプション

  | オプション | 内容 |
  |---|---|
  | 関節を人間の可動域に制限する | 肘・膝の逆曲がり、手首の折れ、上腕のねじれ等をクランプ（`stages/joint_limits.py`） |
  | 手が検出できない区間を前後の検出で補間する | 手のひらがフレームごとに別方向を向く「パタつき」対策。外れ値の検出も捨てる |
  | 指をトラッキングする | MediaPipe Hand Landmarker で指の曲げを推定 |
  | 処理モード | **品質優先（通常）** / **速度優先（軽量）**。下の「処理モード」参照 |
  | クリーンプレート | 実写の人物を消す。OFF だと元映像の上に重ねるだけ（確認用、数分で終わる） |
  | 先頭 N フレームだけ処理 | 設定を試すとき |
  | やり直し開始工程 | 指定した工程以降を作り直す。処理モードや上 3 つの ON/OFF を変えただけなら不要（必要な工程から自動で再計算） |

### 処理モード（プリセット）

| | 品質優先（通常） | 速度優先（軽量） |
|---|---|---|
| MediaPipe Pose（人物マスク・モーキャプ） | heavy | full |
| クリーンプレートの処理解像度（長辺） | 960 | 720 |
| オプティカルフロー（NVIDIA Optical Flow） | 高精度（slow）、画素ごと、前後両方向から背景を運ぶ | 高速（fast）、4×4 ごと、過去からのみ |

値は `config.yaml` の `presets:` にあります。同じキーを `config.yaml` の上位に書くと、プリセットより優先されます。
選んだモードは `work/<動画名>/options.json` に保存され、変えると結果に影響する工程から作り直します。

### CLI

```
python run.py --video input.mp4 --vrm chara.vrm --out result.mp4
    [--preset quality|speed] [--no-cleanplate] [--max-frames N] [--from N] [--auto] [--work DIR]
    [--joint-limits on|off] [--hand-interpolate on|off] [--fingers on|off]
```

```
python setup_env.py --check                    # 環境の状態確認
python setup_env.py --force                    # 依存・モデルを再導入
python tools/render_selftest.py --vrm x.vrm    # VRM を読んで 4 枚描画（GPU と座標系の確認。T ポーズ / 回転 / 腕上げ / 握り）
```

## 配布用 exe（Electron 版）

`electron/` で Windows のインストーラーを作れます。利用者は Python・Node.js を入れる必要がありません。

```
cd render && npm install          # three.js / three-vrm（exe に同梱する分）
cd ../electron && npm install
npm run dist                      # → electron/dist/Replace2VRM-setup-<version>.exe
```

- exe に入るのは Electron、uv、画面と Python のコード、three.js / three-vrm だけです（約 130MB）
- 初回起動時に、同梱の uv が Python 3.12 を用意し、PyTorch・MediaPipe・OpenCV などと MediaPipe のモデルを各配布元から
  `%LOCALAPPDATA%\Replace2VRM` にダウンロードします（約 4GB。2 回目からはすぐ起動）。出力の既定は「ビデオ」フォルダの `Replace2VRM`
- レンダー（工程 4）は Electron の見えないウィンドウで行います（Node.js・Chrome は使いません）
- NVIDIA 以外の GPU では起動時に「NVIDIA 以外の GPUは利用できません」と表示して終了します
- コード署名はしていません。初回起動時に「Windows によって PC が保護されました」と出たら、「詳細情報」→「実行」を押してください
- 開発中の確認: `cd electron && npm start`（プロジェクトのフォルダの Python 環境・モデルを使うには環境変数 `VRM_REPLACE_PYTHON` に python.exe を指定）

## 出力の形式

出力は mp4 です。映像は AV1（RTX 40 以降は GPU の NVENC で高速）か VP9（RTX 20 / 30、CPU）、音声は Opus です。いずれも特許料を求めない方針で提供されている形式で、特許のライセンス条件がある H.264 / AAC は使いません。画質は `config.yaml` の `video_quality`（小さいほど高画質）。

## 構成（工程ごとに別プロセス。落ちた工程から再開できる）

| 工程 | スクリプト | 使うもの | GPU |
|---|---|---|---|
| 0 ショット分割・フレーム展開 | `stages/s0_shots.py` | PySceneDetect, ffmpeg | - |
| 1 人物マスク（クリック→追跡） | `stages/s1_mask.py` | MediaPipe Pose の人物マスク（CPU） | - |
| 2 モーキャプ（体・手首・指） | `stages/s2_mocap.py` | MediaPipe Pose / Hand（CPU） | - |
| 4 VRM にモーション適用→透過レンダー | `stages/s4_render.py` → `render/s4_threevrm.js` | three-vrm + ヘッドレス Chrome | 少 |
| 3 クリーンプレート（4 の後に実行） | `stages/s3_cleanplate.py` | NVIDIA Optical Flow + OpenCV | 1GB 程度（下の「実測」） |
| 5 合成・音声・結合 | `stages/s5_final.py` | ffmpeg | - |

```
vrm_replace/
  run.cmd            Windows 用ランチャー（環境チェック → setup_env.py → gui.py）
  setup_env.py       依存・モデルの導入（冪等）
  gui.py / run.py    GUI / CLI のエントリ。run.py が各工程を subprocess で順に呼ぶ
  app/               GUI（backend.py: 処理側、media.py: 動画・VRM の情報、ui/: 画面の HTML/CSS/JS）
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
- s1 のクリックは 1 点で十分です。MediaPipe Pose で複数人を検出し、クリック位置にいる人物（右クリックの位置にいる人物は除外）を選んで、以降は前フレームと最も重なる人物を追います。一時的に見失ったら直前のマスクでつなぎ（`mask_hold_frames`）、長く見失ったら最後の位置の近くで再出現を待ちます。
- **クリーンプレート**（工程 3）は工程 4 のレンダーの後に実行します。埋めるのは「太らせた人物マスク − アバターの不透明部分（縁から `cleanplate_band_margin_px` 内側まで）」だけです（`cleanplate_avatar_band`）。アバターで隠れる部分は埋めないので速く、見える部分に処理が集中します。代わりに VRM を変えるとクリーンプレートも作り直しになります（自動）。
  - 隣のフレームとのオプティカルフローを NVIDIA Optical Flow（GPU 内蔵の専用ハードウェア。ドライバーに同梱され、追加のダウンロードは不要。Turing 以降）で求め（人物の上のフローは周囲から補間）、他のフレームに映っている背景を「元の画素の座標」として運びます。座標で運ぶので、何フレーム運んでもぼけが溜まりません
  - どのフレームにも映っていない穴は、キーフレームで OpenCV の ShiftMap（周囲の模様をずらして貼る、学習データを使わない従来手法）で埋めてから以降のフレームに運びます（毎フレーム埋めるより速く、ちらつかない）
  - `cleanplate_segment_frames`（既定 300）ごとに分割し、`cleanplate_max_side` に縮小して処理、**埋めた領域だけ**を元フレームに貼り戻します

### モーキャプのバックエンド（`config.yaml: mocap_backend`）

| backend | 精度 | 導入 | 備考 |
|---|---|---|---|
| `mediapipe`（既定） | 中。奥行き方向の腕・脚の曲げが甘い、遮蔽に弱い | 不要（同梱） | CPU。720p で 15〜20 fps。指も推定 |
| `smpl_npz` | SMPL 系推定器の品質そのまま | 自前 | `shot_NNN/mocap.npz`（`pose (F,22,3)` axis-angle, `transl (F,3)` カメラ座標, `K (3,3)`）を置く |
| `gvhmr` | 高 | GVHMR を別環境に構築 | `gvhmr_cmd` / `gvhmr_result` を設定。未検証 |

### 実測（RTX 5060 Ti 16GB、1280x720・356 フレームの歩行素材、カメラはパン）

| | 品質優先（通常） | 速度優先（軽量） |
|---|---|---|
| クリーンプレート | 0.07 秒/フレーム（28 秒） | 0.04 秒/フレーム（14 秒） |
| クリーンプレートの VRAM（ピーク） | 1.1GB | 0.6GB |
| 全工程（0〜5） | 約 1 分 33 秒 | 約 1 分 |

※ 参考値です。速度優先はフローの精度が落ちるぶん、人物の通った跡ににじみが出やすくなります。

## config.yaml の主な項目

- `scene_threshold` ショット分割感度 / `min_shot_frames` 短すぎるショットの吸収
- `mask_threshold`, `mask_temporal_smooth`, `mask_hold_frames`, `mask_num_poses`（人物マスク）
- `focal_scale`（焦点距離 = focal_scale × max(幅, 高さ)。望遠素材なら上げる）, `mocap_smooth_sigma`, `use_hands`, `crop_margin`
- `use_fingers`, `hand_crop_scale`, `hand_min_crop_px`, `finger_smooth_sigma`, `finger_max_splay_deg`（指）
- `joint_limits`, `hand_interpolate`, `hand_outlier_deg`（GUI / CLI で上書き可。可動域の角度表は `stages/joint_limits.py: LIMITS`）
- `preset`（quality / speed）と `presets:`（モードごとの値）
- `mask_dilate_px`, `cleanplate_segment_frames`, `cleanplate_avatar_band`, `cleanplate_band_margin_px`, `cleanplate_key_interval`, `cleanplate_fill`, `cleanplate_flow_batch`（クリーンプレート）
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
- クリーンプレートが極端に遅い: VRAM が溢れて共有メモリに退避しています。速度優先モードにするか、`cleanplate_flow_batch` を 1〜2 に、`cleanplate_segment_frames` を 150 に下げてください
- セットアップに失敗した: `logs/setup.log` を確認。`python setup_env.py --force` で再導入できます

## ライセンス

Replace2VRM（vrm_replace）本体は [Apache License 2.0](LICENSE) です。

Copyright 2026 Igarashi Date/503 bad gateway（[NOTICE](NOTICE)）

- X: https://x.com/@503_bad
- YouTube: https://youtube.com/@503badgateway

`run.cmd` が取得する MediaPipe、three-vrm などはそれぞれのライセンスに従います（[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)）。
AI モデルは、学習データの条件まで含めて商用利用できるものだけを使います（詳しくは THIRD_PARTY_NOTICES.md）。
