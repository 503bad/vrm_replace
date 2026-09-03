# vrm_replace — 実写の人物を VRM キャラに置き換えるパイプライン（Windows ネイティブ）

```
python run.py --video input.mp4 --vrm chara.vrm --out result.mp4
```

1. 最初のショットの1フレーム目が表示されるので、置き換えたい人物を **左クリック → Enter**
   （右クリックで「除外したい場所」も指定可。`r` でやり直し）
2. あとは放置。`result.mp4` が出力される

入力: mp4 / avi（ffmpeg が読めるものなら何でも）。3D モデル: VRM 1.0 / 0.x。

## 構成（工程ごとに別プロセス。落ちた工程から再開できる）

| 工程 | スクリプト | 使うもの | GPU |
|---|---|---|---|
| 0 ショット分割・フレーム展開 | `stages/s0_shots.py` | PySceneDetect, ffmpeg | - |
| 1 人物マスク（クリック→追跡） | `stages/s1_mask.py` | SAM 2 | 〜7GB |
| 2 モーキャプ | `stages/s2_mocap.py` | MediaPipe Pose（CPU）/ SMPL 系（任意） | - |
| 3 クリーンプレート | `stages/s3_cleanplate.py` | ProPainter | 〜15GB（設定で削減可） |
| 4 VRM にモーション適用→透過レンダー | `stages/s4_render.py` → `render/s4_threevrm.js` | three-vrm + ヘッドレス Chrome | 少 |
| 5 合成・音声・結合 | `stages/s5_final.py` | ffmpeg | - |

中間成果物はすべて `work/<video名>/shot_NNN/` に書き出される（`--work DIR` で変更可）。各工程の完了で `.done_N` を置き、再実行時はスキップ。
`--from N` で N 以降をやり直し。`--no-cleanplate` で ProPainter を飛ばして元映像の上に重ねる（確認用、数分で終わる）。
`--max-frames N` で先頭 N フレームだけ処理（確認用）。クリック UI は tkinter（OpenCV の GUI 有無に依存しない）。

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

## セットアップ（Windows 11, Python 3.12, Node.js 18+）

```
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
python setup_env.py
```

`setup_env.py` が pip 依存・SAM 2・ProPainter・モデル DL・`render/` の npm install・GPU 確認までやる。
`python setup_env.py --check` で状態確認。RTX 50 系（Blackwell）は cu128 以降の torch が必要。

動作確認:

```
python tools/render_selftest.py --vrm sample/xxx.vrm        # VRM を読んで 3 枚レンダー（GPU・座標系の確認）
python run.py --video sample/xxx.mp4 --vrm sample/xxx.vrm --out out/test.mp4 --no-cleanplate
```

## 動作の考え方

- **モーキャプは全てカメラ座標系**。カメラが動いていても「カメラから見た体の位置・向き」がそのまま出るので合成のズレが出ない。
  代償として足すべりは起こり得る（合成向けの割り切り）。
- `mocap.json` はバックエンド非依存の中間フォーマット（VRM humanoid ボーン名ごとの **グローバル回転（T ポーズ基準, OpenCV カメラ座標）** と腰位置）。
  s4 はこれを three.js 座標に変換し、three-vrm の正規化ボーンに「存在する最も近い祖先からの相対回転」として流し込む。
  VRM に無いボーン（upperChest, 肩, つま先, 指）は自動で飛ばす。
- **スプリングボーン（髪・スカート等の揺れもの）は three-vrm 標準で動く**。各フレーム `vrm.update(1/fps)`。
  ショット開始時は最初のポーズで `render_warmup_frames` 分だけ空回しして物理を落ち着かせる。
- VRM のスケール: 実写人物の脚長（MediaPipe の world landmark から推定）に VRM の股関節高さを合わせる（`person_hip_height: auto`）。
  足の接地を優先するため、体型が違うと頭の高さはズレるが、実写人物はクリーンプレートで消えるので目立たない。
- カメラ内部パラメータは `focal_scale × max(幅, 高さ)` を焦点距離として仮定（`config.yaml`）。望遠素材なら上げる。
- **指**: 各手首まわりを正方形クロップ（`hand_min_crop_px` 以上に拡大）して Hand Landmarker を掛け、
  親指 3 + 4 指 × 3 = 片手 15 本の回転を mocap.json に入れる。mocap 側の基準は「手の方向にまっすぐ伸びた指」で、
  VRM ごとの実際のレスト方向（親指の角度・指の開き）との差は s4 が正規化リグから読んで補正する（`renderer.js: restCorr`）。
  手が検出できないフレームは `fingers_valid=false` として VRM のレストの手のまま。
- s1 のクリックは 1 点でよい。内部で MediaPipe が人物を特定し、bbox + 関節点に展開して SAM 2 に渡す
  （1 点だけだと「腕」など部位が選ばれるため）。長尺は `sam2_chunk_frames` ごとに区切り、マスクで繋ぐ。
- ProPainter は全フレームを GPU/RAM に持つので `propainter_segment_frames`（既定 300）ごとに分割して回す。
  処理は `propainter_max_side`（既定 960）に縮小して行い、**インペイントした領域だけ**を元フレームに貼り戻すので背景全体はぼけない。
  VRAM が 16GB を超えそう（共有メモリにあふれて極端に遅くなる）なら `propainter_subvideo` を下げる。

### モーキャプのバックエンド（`config.yaml: mocap_backend`）

| backend | 精度 | 導入 | 備考 |
|---|---|---|---|
| `mediapipe`（既定） | 中。奥行き方向の腕・脚の曲げが甘い、遮蔽に弱い | 不要（同梱） | CPU。720p で 15〜20 fps。**指も推定**（Hand Landmarker） |
| `smpl_npz` | SMPL 系推定器の品質そのまま | 自前 | `shot_NNN/mocap.npz`（`pose (F,22,3)` axis-angle, `transl (F,3)` カメラ座標, `K (3,3)`）を置く |
| `gvhmr` | 高 | GVHMR を WSL2/conda に別途構築 | `gvhmr_cmd` / `gvhmr_result` を設定。**未検証**（HANDOFF 参照） |

## config.yaml の主な項目

- `scene_threshold` ショット分割感度 / `min_shot_frames` 短すぎるショットの吸収
- `sam2_chunk_frames` SAM2 に一度に渡すフレーム数（RAM/VRAM）
- `focal_scale`, `mocap_smooth_sigma`, `use_hands`, `crop_margin`
- `use_fingers`, `hand_crop_scale`, `hand_min_crop_px`, `finger_smooth_sigma`, `finger_max_splay_deg`（指）
- `propainter_subvideo`, `propainter_neighbor`, `propainter_max_side`, `propainter_segment_frames`, `mask_dilate_px`
- `person_hip_height`（auto / 数値[m]）, `arm_offset_deg`（SMPL 系のみ）, `render_warmup_frames`, ライト各種
- `chrome_exe` GPU が効かない場合にインストール済み Chrome を指定 / `allow_software_gl`

## 既知の限界

- 複数ショットがある場合、ショットごとにクリックを求める（`--auto` で最初の点を使い回し）
- 人物がフレームアウトして再登場した場合、同一ショット内では追跡が切れる（マスク空 → その区間は透明）
- 人物の遮蔽（手前に物が来る）は扱わない。影は消えない（マスクは人物のみ）
- 表情は未対応。指は MediaPipe Hand Landmarker で推定するが、手が小さく写る（〜50px）素材では握りが浅め・開き気味になる。
  `finger_max_splay_deg` / `finger_smooth_sigma` で抑制、`use_fingers: false` で無効化（VRM のレストの手のまま）
- ライティングは平行光 1 灯 + 環境光。背景に合わせて `config.yaml` の `light_dir` 等を調整
- Blender 版 s4（`legacy/s4_blender.py`）は残してあるが、揺れものは出ない
