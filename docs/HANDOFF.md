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
- 旧 Blender 版 s4 は削除した（bpy 依存のため GPL 扱いになり得る・未検証・揺れもの非対応）。必要なら git 履歴 `a724ee5` 以前の `legacy/s4_blender.py`
- 配布: `run.cmd` → `setup_env.py`（環境チェック・依存/モデル取得・`.setup_done` で高速化）→ `gui.py`。ライセンスは Apache-2.0、依存は THIRD_PARTY_NOTICES.md

### 検証済み

1. `tools/render_selftest.py`: T ポーズ / 90° 回転 / 左腕上げ → 期待どおりの向きで描画（座標変換の検証）
2. sample 動画（歩く男性, 1280x720, 356 フレーム, 固定カメラ）で s0〜s5 を通した。
   足の接地・進行方向・位置が実写と一致。ProPainter のクリーンプレートも生成
3. SAM2 のチャンク繋ぎ（300 フレーム区切り → マスクプロンプトで継続）

### 未検証・要確認

- 複数ショット: 合成 2 ショット素材（`work/_tests/multishot.mp4`, select.json を事前配置）で s0〜s5 が通ることは確認。
  実際にクリック UI が 2 回出る流れと `--auto` は未確認
- **長尺**（数十分）。設計上は s1 チャンク / s3 セグメント / ショット単位再開で耐えるはずだが未実測。
  ディスク: frames jpg で 30 分 ≒ 5GB、cleanplate png で ≒ 50GB 程度を見込む
- **GVHMR バックエンド**（`s2_mocap.py: gvhmr_backend`）。`hmr4d_results.pt` のキー名
  （`smpl_params_incam`, `K_fullimg`）は旧 HANDOFF の記載どおりに実装、未検証
- SMPL 系の `transl` → hips 位置の変換で pelvis オフセット `SMPL_J0` を定数近似している（betas 依存を無視）

### 指トラッキング（feature/finger-tracking, 2026-09-03）

- s2 mediapipe バックエンドに Hand Landmarker を追加。pose の手首・指先から手のクロップ（`hand_crop_scale`）を作り、
  `hand_min_crop_px` 以上に拡大して推定。pose の手首に最も近い検出を採用（左右は pose 側で決めるので handedness 不要）
- Hand の world landmarks も pose と同じカメラ整列座標（実データで xy の向きが画像と一致することを確認）
- 回転: 掌の基準フレーム (d_palm = wrist→middle_mcp, n = (idx_mcp-wrist)×(pinky_mcp-wrist), s = n×d_palm) を使い、
  各セグメント方向 d_seg と s から `rotation_between_frames`。基準（canonical）は全指が手の方向 ±x にまっすぐ
- **VRM ごとのレスト差の補正は s4**（`renderer.js: fingerRestDir / restCorr`）。正規化リグの子ノード位置からレスト方向
  d_vrm を取り、Rc = (±x → d_vrm) の最小回転で `W_vrm = W_can · Rc⁻¹`。末節（子無し）は親の方向を流用
- ノイズ対策: 親指以外の外転を `finger_max_splay_deg`(20°) でクランプ、指は `finger_smooth_sigma`(3) で強めに平滑化
- `tools/render_selftest.py` の f0003 で「両手を握る」合成ポーズを検証済み（掌側に曲がる）
- 限界: sample のように手が 40〜50px の素材では握りが浅め。手が大きく写る素材ほど良い。
  遮蔽側の手（sample の左手）は 60/356 フレームしか取れず、その間はレストの手

### 可動域制限・手の補間・GUI（feature/finger-tracking, 2026-09-03）

- `stages/joint_limits.py`: 親相対回転を「ボーン軸 d・正曲げ方向 e・持ち上げ方向 h=e×d」で swing/twist に分解し、
  bend/side/twist をクランプして FK で戻す。子は親相対の角度を保つ。角度表は `LIMITS`（cv 座標 T ポーズ基準）。
  単体テスト: 肘/膝の逆曲がり → −5°、手首 120° → 80°、上腕 twist 150° → 100° を確認
- 注意: 前腕の twist 基準は肘の曲げ軸、手の twist 基準は掌法線なので、回内外が「前腕 twist」と「手 twist」に
  任意に分かれる。手の twist は ±110° と広め。陸上素材ではクランプ前の手首 bend が −113〜133°、twist −145〜61° と
  暴れていて（= パタつき）、クランプ + 補間で安定した
- 手の補間 (`hand_interpolate`): `reject_hand_outliers` で直前採用フレームから (hand_outlier_deg × 経過フレーム) 以上
  掌が飛ぶ検出を捨て、`fill_gaps` の上限を全長にして線形補間。OFF 時は従来（0.5 秒まで補間、以降レスト）
- オプションは `work/<video>/options.json`（`common.write_options` / `load_config(work)`）。s2 は mocap.json に
  `options` を記録し、変わっていれば .done_2 があってもやり直す。s4 は mocap.json が .done_4 より新しければ再レンダー
- `gui.py`: 当初は tkinter のランチャー（2026-11-04 に pywebview へ置き換え。下の「GUI の作り直し」）
- 残課題: MediaPipe の奥行き推定が甘く、肘を深く曲げる動き（走り）で肘が浅く出る。可動域では直らない

### ProPainter の置き換え・処理モード（feature/biz_fix, 2026-11-04）

検討の経緯は `docs/update_20261104.md`。
- ProPainter（非商用）を、**torchvision RAFT（BSD-3）+ LaMa（Apache-2.0, IOPaint の TorchScript 版 big-lama.pt）** に置き換えた
  - SEA-RAFT ではなく torchvision の RAFT にした。追加の依存やリポジトリの取得が要らず、重みも download.pytorch.org から取れるため
  - 各画素の「元の画素（フレーム番号, x, y）」を座標のまま運ぶので、運ぶ回数が増えてもぼけない。人物の上のフローは
    1/4 に縮小して Telea で補間する。どこにも映っていない穴は、キーフレーム（`cleanplate_lama_interval`）で
    人物の穴全体を LaMa で埋めてから運ぶ。双方向のときは、実際に映った背景を LaMa の結果より優先する
- **工程の順番を 4 → 3 に変えた**。埋めるのは「太らせた人物マスク − アバターの不透明部分（縁から 6px 内側まで）」だけ。
  歩行素材では人物マスクの 62% だけを埋めればよい。s3 は `.done_4` が `.done_3` より新しければ作り直す
- **プリセット** `preset: quality | speed`（`config.yaml: presets`）。品質優先 = 通常レベル（以前と同じ設定）、速度優先 = 軽量化
  （SAM2 small, MediaPipe Pose full, 長辺 720, RAFT small ×8, 過去からのみ）。GUI のラジオボタン / `run.py --preset`
- **設定の変更検知**: `.done_N` に工程の結果に効く設定（`common.STAGE_KEYS`）を JSON で記録。run.py が今の設定と比べて、
  違えばその工程から作り直す。s3 の区間単位の途中結果は `cleanplate/_sig.txt` で同じ設定のものだけ使い回す
- 終了時の異常終了: LaMa の TorchScript を CUDA で使った後、プロセス終了時の後片付けで 0xC0000409 / 127 で落ちることがある。
  `torch._C._jit_set_profiling_executor(False)` を使うと起きやすかったので外し、代わりに入力を 64 の倍数にそろえて
  再最適化を減らした。さらに s3 は最後に `os._exit(0)` で終わる（モデルを持ったまま後片付けを飛ばす）
- 実測と残課題:
  - 品質優先 0.25 秒/フレーム・VRAM 4.8GB、速度優先 0.05 秒/フレーム・1.9GB（別の重い処理と並行して測った値）
  - 速度優先は人物の通った跡ににじみが出やすい。前後両方向にしても改善は小さく、時間は 3 割増えたので過去からのみにした
  - SAM2 が白い靴を取りこぼすことがあり、その部分はクリーンプレートに残る（工程 1 の課題）
  - 区間（300 フレーム）の境目で埋め方が変わる。継ぎ目のちらつきは未評価
  - LaMa と RAFT の学習データの条件は THIRD_PARTY_NOTICES.md に注記した。商用で使う前に法務の確認が必要

### VRM 0.x 対応の修正（feature/biz_fix, 2026-11-04）

- 0.x で手足の回転が逆になり、体も後ろを向いていた。原因: 0.x は `rotateVRM0` で vrm.scene が Y 軸 180° 回るが、
  補正していたのは hips だけで、子ボーンの親相対回転はワールド系のまま入れていた
- `renderer.js: applyPose` で、全ボーンのグローバル回転を scene の回転 Q で共役（G = Q⁻¹·W·Q）してから親相対にする。
  1.0 は Q = identity なので結果は変わらない。0.x では四元数の x, z の符号反転と同じ（three-vrm 公式サンプルと同じ扱い）
- 確認: `tools/render_selftest.py` を 1.0 と 0.x の両方で実行し、T ポーズ / 右向き / 左腕上げが期待どおり。
  歩行素材でも 1.0・0.x とも腕を自然に振って進行方向を向く
- テスト用 VRM は `docs/` に置いてある（1.0 と 0.x を 1 体ずつ）。VRM は git 管理外。権利のある VRM だけを使うこと

### GUI の作り直し（feature/biz_fix, 2026-11-04）

- tkinter → **pywebview（WebView2）+ HTML/CSS/JS**。画面 `app/ui`、処理 `app/backend.py`（js_api）、ファイル情報 `app/media.py`
- 見た目は emo_alp（`plugin/ui/Theme.h`）の言語に合わせた：地色 #0F1114・インク #E9E7E1・アンバー 1 色、赤は警告だけ、
  角丸・枠なしの平行四辺形と面取り、等幅大文字＋字間のラベル、数字は Bahnschrift、snap / expo の動き。フォントは同梱しない
- ドラッグ＆ドロップ: Python 側で `window.dom.document.events.drop` を受け、`pywebviewFullPath` でフルパスを得る
  （ブラウザの D&D ではパスが取れず、動画を丸ごとコピーすることになるため）。拡張子で動画 / VRM に振り分ける
- 人物のクリックは GUI の中で行う: `run.py --until 0`（ショット分割だけ）→ select.json が無いショットの最初のフレームを
  画面に出す → クリックを select.json に書く → run.py 本体。「2 ショット目以降も同じ位置」は GUI 側で全ショットに書く。
  「やり直す工程 = 1」のときは select.json を消してクリックからやり直す。CLI だけで使う場合は従来どおり s1 の tkinter 画面
- 進み具合はログの行から読む（`app/backend.py: RE_PROG`）。工程の出力の書式を変えたらここも合わせる
- VRM のメタ情報（0.x: `extensions.VRM.meta`、1.0: `extensions.VRMC_vrm.meta`）から利用条件を表示する。止めはしない
- 確認: 処理側は Api を直接呼んで 準備→選択→実行→完了 を通した。画面はヘッドレス Chrome で状態ごとに撮影。
  実機のドロップ操作は未確認（自動では試せない）

### 商用利用の基準の厳格化（feature/biz_fix, 2026-11-04）

- 方針: AI モデルは「学習データを公開していない、かつ商用可」または「学習データも学習済みモデルも商用可」のものだけを使う
- 一次情報で確認した結果:
  - 満たす: MediaPipe Pose / Hand / Selfie・Multiclass Segmentation（モデルの説明書に Apache-2.0、学習データは同意を得た撮影画像で非公開）
  - 満たさない: SAM 2（SA-1B が研究用）、RAFT（FlyingChairs ほかが研究用）、LaMa（Places が研究・教育用）、Mitsua Diffusion CC0（文章の処理部分と VAE が SD 2.1 由来）
  - 候補: TAPIR（重みは Apache-2.0、学習は Kubric MOVi-E のみ。素材は GSO = CC BY 4.0 と Poly Haven = CC0。
    生成されたデータセット自体のライセンス表記は未発見）、NVIDIA Optical Flow（学習データの問題なし。実行部分 nvofapi64.dll は
    GPU ドライバーに同梱、API 定義のヘッダーは BSD-3）
- 穴埋めは LaMa をやめ、OpenCV に置き換えた: キーフレームの大きな穴 = xphoto ShiftMap（穴のまわりを切り出して 960px で 3 秒程度）、
  小さな穴 = Telea。ShiftMap は opencv-contrib-python が必要。opencv-python / -headless と同時に入っていると上書きし合って
  xphoto が消える（この開発機がその状態。Telea に自動で切り替わる）。setup_env.py は警告と直し方を出すだけで、共用環境は自動で書き換えない
- 置き換え完了（同日）:
  - 人物マスク: SAM 2 → **MediaPipe Pose の人物マスク**（`output_segmentation_masks`、num_poses=4）。クリック位置の人物を選び、
    前フレームと bbox の IoU が最大の人物を追う。見失ったら `mask_hold_frames` だけ直前のマスク、以降は最後の位置の近くで再出現を待つ。
    歩行素材で 356/356 フレーム追跡。SAM 2 で取りこぼしていた白い靴も入る
  - フロー: RAFT → **NVIDIA Optical Flow**（`stages/nvof.py`、ctypes で nvofapi64.dll を直接呼ぶ。SDK 不要）。
    960x536 で 1 組 2.6ms（slow, grid 1）。NVIDIA 以外の GPU は対象外（起動時に「NVIDIA 以外の GPUは利用できません」で終了）。向きは「入力 → 参照」
  - 結果: 品質優先 全工程 93 秒（以前 190 秒）、クリーンプレート 0.07 秒/フレーム・VRAM 1.1GB。速度優先 60 秒、0.04 秒/フレーム・0.6GB
  - SAM 2 / RAFT / LaMa のモデルと SAM 2 の取得・導入をセットアップから削除。torch は残す（クリーンプレートの GPU 処理）。torchvision は不要に
  - 残課題: 品質優先の一部フレームで、頭の上の空に小さな黒い点が残る（髪の色を運んだか、キーフレームの穴埋め由来。未調査）

### 配布用 exe（Electron 版）（feature/biz_fix, 2026-11-08）

- `electron/`: Electron 45 + electron-builder（NSIS）。画面は `app/ui` をそのまま使う（`preload.js` が `window.pywebview.api` と同じ形を用意）
- 処理側は Python のまま。`electron/main.js` が `app/bridge.py` を子プロセスで起動し、標準入出力（1 行 1 JSON）で `app/backend.py` の Api を呼ぶ。
  ファイル選択ダイアログとドロップのパス取得（`webUtils.getPathForFile`）だけ Electron 側
- 書き込み先は `common.DATA`（環境変数 `VRM_REPLACE_DATA`。配布版は `%LOCALAPPDATA%\Replace2VRM`、開発時はプロジェクト）。
  コード（`resources/vrm_replace`）とモデル・作業ファイル・ログ・設定を分けた
- 初回起動: `electron/setup.js` が同梱の uv で venv（Python 3.12、uv が公式ビルドを取得）を作り、`setup_env.py --app` を実行
  （pip の代わりに uv、Node・Chrome の手順は飛ばす、pywebview は入れない）。2 回目以降は `.setup_done` のハッシュで即終了
- レンダー: `Replace2VRM.exe --render ...`（`electron/render.js`）を `stages/s4_render.py` が呼ぶ（環境変数 `VRM_REPLACE_ELECTRON`）。
  page.html / renderer.js は Puppeteer 版と共通（共有部分は `render/server.js`）。30 フレームで Puppeteer 版と画素単位で一致、速度は約 3 倍
- Electron（Windows の GUI アプリ）は親から受け継いだ標準出力に書けないので、s4_render.py はパイプで受けて中継する
- ffmpeg.dll は Electron 公式リリースの ffmpeg zip 版に差し替え（`scripts/after-pack.js`）。2026-11-08 に実測: 差し替え版は avcodec_find_decoder で H.264 / AAC が無い（元の版は両方あり）。ただし Electron 本体は Windows・GPU のデコードで H.264 を再生できる（GPU デコードを切っても再生できた）ので、「アプリが H.264 を含まない」とは書かないこと。LGPL-2.1 のため、対応するソース（Chromium DEPS の ffmpeg_revision の tarball + Electron の patches/ffmpeg）と、Chromium・Electron のビルドの仕組みの入手先を README.txt に書いて `resources/third_party_sources/ffmpeg` に同梱。uv は `scripts/fetch-uv.js` が SHA-256 を照合して取得
- 出力形式（2026-11-08 改訂）: AV1 / VP9 は X や Premiere で読めなかったため（ユーザー確認）、出力ファイルの拡張子で選ぶ形にした。mp4 = H.264（h264_nvenc、`h264_cq`）/ AAC、mov = CineForm（cfhd、yuv422p10le）/ PCM。GUI の FORMAT。フレームレートは分数（30000/1001 など）で渡す（小数だとタイムベースが 979001 のような半端な値になっていた）。アプリは無償配布（有償販売しない、ユーザー決定）。H.264 / AAC の条件と利用者の責任は README の「利用上の注意」
- ffmpeg 9 で `-vsync` が削除されたため、s0 は `-fps_mode passthrough` を使う（配布版は imageio-ffmpeg の最新 = ffmpeg 9.0.2 が入る）
- 確認用: 環境変数 `VRM_REPLACE_SELFTEST=<フォルダ>`（+ `VRM_REPLACE_SELFTEST_FILES`、`VRM_REPLACE_SELFTEST_RUN=x,y`）で
  画面を出さずに起動し、画面の画像と結果を保存して終わる。`VRM_REPLACE_PYTHON` で既存の Python を使い、初回導入を飛ばせる
- 未確認: 新しい PC での初回導入（uv での Python・PyTorch の導入）、インストーラーでのインストール・アンインストール

## 設計上の決定事項

1. 工程は別プロセス。終了で VRAM 解放。16GB を各工程がフルに使ってよい
2. 長尺はショット単位で処理・再開。ショット内も s1 はチャンク、s3 はセグメントで分割
3. モーキャプは **カメラ座標系** を使い、レンダー側のカメラは原点固定・回転なし。足すべりより「合成のズレなし」を優先
4. **mocap.json** をバックエンド非依存の中間フォーマットにした（`stages/mocap_common.py` 冒頭）。
   VRM humanoid ボーン名 → T ポーズ基準グローバル回転（OpenCV カメラ座標）。s4 はこれしか見ない。
   SMPL 系 / MediaPipe / 将来の推定器はここに合わせればよい
5. 1 点クリックは s1 内で MediaPipe により「人物 bbox + 関節点」に展開してから SAM2 へ（部位選択の回避）
6. VRM のスケールは実写人物の脚長に合わせる（`person_hip_height: auto`）。足の接地を優先
7. 遮蔽・表情は扱わない（手指は feature/finger-tracking で追加）

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
2. 手指の精度: HaMeR 等の結果を mocap.json の指ボーンに入れれば s4 はそのまま拾う（restCorr も効く）
3. 影: マスクに影を含める（クリックで影も指定）か、s5 でキャラの接地影を合成
4. 遮蔽: s5 で「元マスクの外側に残る手前物体」を戻す仕組み
5. 複数人の同時置換: obj_id を増やして s1〜s4 を人物ごとに回す
