# HANDOFF.md — vrm_replace 引き継ぎ

## ゴール

実写動画の人物を VRM キャラに置き換えるツール。人間の操作は
「動画と VRM を渡す → 最初のフレームで対象人物をクリック → Enter」だけ。mp4 が出る。
長尺（数十分〜）を回す前提。Autodesk Flow Studio の代替を個人 GPU で。

## 現在の状態（2026-09-03）

**Windows ネイティブで全工程が通り、sample/ の素材で end-to-end 動作確認済み。**

- 環境: Windows 11, Python 3.12, torch 2.10+cu128, RTX 5060 Ti 16GB, Node 24, Chrome（Puppeteer 同梱）
- WSL2 は使わない（README の旧手順から変更）。理由: RTX 50 系は cu128 以降が必須で GVHMR の
  pin（torch 2.3 / cu124）では動かない。SAM2・ProPainter・MediaPipe は Windows で問題なく動いた
- s4 は Blender 版から **three-vrm + Puppeteer（ヘッドレス Chrome, ANGLE D3D11 で GPU）** に差し替え済み。
  720p で約 160 fps。スプリングボーン動作（sample VRM で 14 ジョイント検出）
- ffmpeg は PATH に無ければ imageio-ffmpeg 同梱バイナリを使う

### 検証済み

1. `tools/render_selftest.py`: T ポーズ / 90° 回転 / 左腕上げ → 期待どおりの向きで描画（座標変換の検証）
2. sample 動画（歩く男性, 1280x720, 356 フレーム, 固定カメラ）で s0〜s5 を通した。
   足の接地・進行方向・位置が実写と一致。ProPainter のクリーンプレートも生成
3. SAM2 のチャンク繋ぎ（300 フレーム区切り → マスクプロンプトで継続）

### 未検証・要確認

- **複数ショットの素材**（クリック UI が 2 回以上出る流れ、`--auto`）
- **長尺**（数十分）。設計上は s1 チャンク / s3 セグメント / ショット単位再開で耐えるはずだが未実測。
  ディスク: frames jpg で 30 分 ≒ 5GB、cleanplate png で ≒ 50GB 程度を見込む
- **VRM 0.x** の読み込み（three-vrm が対応、`rotateVRM0` で 180° 回転して同じ数学に乗る設計だが実機未確認）
- **GVHMR バックエンド**（`s2_mocap.py: gvhmr_backend`）。`hmr4d_results.pt` のキー名
  （`smpl_params_incam`, `K_fullimg`）は旧 HANDOFF の記載どおりに実装、未検証
- SMPL 系の `transl` → hips 位置の変換で pelvis オフセット `SMPL_J0` を定数近似している（betas 依存を無視）

## 設計上の決定事項

1. 工程は別プロセス。終了で VRAM 解放。16GB を各工程がフルに使ってよい
2. 長尺はショット単位で処理・再開。ショット内も s1 はチャンク、s3 はセグメントで分割
3. モーキャプは **カメラ座標系** を使い、レンダー側のカメラは原点固定・回転なし。足すべりより「合成のズレなし」を優先
4. **mocap.json** をバックエンド非依存の中間フォーマットにした（`stages/mocap_common.py` 冒頭）。
   VRM humanoid ボーン名 → T ポーズ基準グローバル回転（OpenCV カメラ座標）。s4 はこれしか見ない。
   SMPL 系 / MediaPipe / 将来の推定器はここに合わせればよい
5. 1 点クリックは s1 内で MediaPipe により「人物 bbox + 関節点」に展開してから SAM2 へ（部位選択の回避）
6. VRM のスケールは実写人物の脚長に合わせる（`person_hip_height: auto`）。足の接地を優先
7. 遮蔽・手指・表情は扱わない

## 座標変換と回転の数学（実装済み）

- mocap.json の `rot[f][j]`: OpenCV カメラ座標 (x右 y下 z前) で「カメラを向いて立つ T ポーズ」からのグローバル回転（xyzw）
- three.js へ: C = diag(1,-1,-1)（x 軸まわり 180°）で共役 → 四元数 (x,y,z,w) → (x,-y,-z,w)、位置 (x,y,z) → (x,-y,-z)
- 正規化ボーン（`getNormalizedBoneNode`）はレスト時ワールド回転 = identity なので
  `local = inv(W_parent) · W_bone`（parent = mocap にも VRM にも存在する最も近い祖先）。
  hips は `inv(vrm.scene.quaternion) · W_hips`（VRM0.x の 180° 回転を打ち消す）
- hips 位置 = (C·t + W_hips·(0, hipsAboveLeg·scale, 0)) / scale。t は股関節中点（MediaPipe の world 原点）
- SMPL 系: FK で G_j を作り `W_j = G_j · C`（SMPL 基準姿勢は y上 z正面なので C で cv の T ポーズ基準に揃う）。
  `arm_offset_deg` は upperArm の z 軸まわり補正（SMPL の A 寄り基準姿勢との差）
- カメラ: K から射影行列を直接組む（`renderer.js: projectionFromK`）。主点ずれ対応
- MediaPipe → 回転: 各ボーンに (方向ベクトル, ねじれ基準ベクトル) の rest/current を定義し、直交フレーム同士の
  回転 `F_cur · F_restᵀ` で求める。肘・膝の曲げ軸は上腕×前腕の外積、伸びきり時は胴体の前後方向にフォールバック。
  詳細は `s2_mocap.py: bone_rotations`

## 次にやるなら

1. **GVHMR / WHAM 等の SMPL 系推定器を実機で繋ぐ**（品質が最も上がる）。`smpl_npz` バックエンドで
   `mocap.npz` を置くだけで s4 以降は動くので、推定器側の環境は自由（WSL2 でも別 conda でも）
2. 手指: HaMeR 等の結果を mocap.json のボーン（leftIndexProximal…）に追加すれば s4 は自動で拾う
3. 影: マスクに影を含める（クリックで影も指定）か、s5 でキャラの接地影を合成
4. 遮蔽: s5 で「元マスクの外側に残る手前物体」を戻す仕組み
5. 複数人の同時置換: obj_id を増やして s1〜s4 を人物ごとに回す
