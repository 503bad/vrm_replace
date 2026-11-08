# Third-party notices / サードパーティのライセンス

Replace2VRM（vrm_replace）本体は Apache License 2.0（`LICENSE`）です。
Copyright 2026 Igarashi Date/503 bad gateway（X: https://x.com/@503_bad / YouTube: https://youtube.com/@503badgateway。`NOTICE`）
以下はこのリポジトリに **同梱していない** が、`run.cmd`（`setup_env.py`）が利用者の環境に取得・導入する
ソフトウェアとモデルです。それぞれのライセンスは各配布元の条件に従います。

| コンポーネント | 用途 | ライセンス | 取得元 |
|---|---|---|---|
| [MediaPipe](https://github.com/google-ai-edge/mediapipe) Pose Landmarker（heavy / full）/ Hand Landmarker | 人物マスク・モーションキャプチャ・指 | Apache-2.0（学習データは Google が同意を得て撮影したもので非公開。各モデルの説明書による） | pip / storage.googleapis.com |
| NVIDIA Optical Flow（`nvofapi64.dll`、NVIDIA の GPU ドライバーに同梱） | クリーンプレートのオプティカルフロー（学習データを使わない専用ハードウェア） | ドライバーの一部。このリポジトリは同梱・配布しない | NVIDIA ドライバー |
| [NVIDIA Optical Flow SDK のヘッダー](https://github.com/NVIDIA/NVIDIAOpticalFlowSDK)（API 2.0） | `stages/nvof.py` の関数・構造体の定義の元 | BSD-3-Clause, Copyright (c) 2020 NVIDIA CORPORATION | GitHub |
| [three.js](https://github.com/mrdoob/three.js) | レンダリング | MIT | npm |
| [@pixiv/three-vrm](https://github.com/pixiv/three-vrm) | VRM の読み込み・MToon・スプリングボーン | MIT | npm |
| [Puppeteer](https://github.com/puppeteer/puppeteer) + Chrome for Testing | ヘッドレス GPU レンダリング | Apache-2.0 / Chrome の利用規約 | npm |
| [PyTorch](https://pytorch.org/) | クリーンプレートの GPU 処理 | BSD-3-Clause | pip (download.pytorch.org) |
| [PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | ショット分割 | BSD-3-Clause | pip |
| [OpenCV](https://opencv.org/)（opencv-contrib-python） | 画像処理、クリーンプレートの穴埋め（Telea / xphoto ShiftMap）。いずれも学習データを使わない従来手法 | Apache-2.0 | pip |
| [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | ffmpeg バイナリ | BSD-2-Clause（同梱 ffmpeg バイナリは GPL/LGPL） | pip |
| NumPy / SciPy / Pillow / PyYAML / tqdm | 各種 | BSD / MIT / HPND | pip |
| [pywebview](https://github.com/r0x0r/pywebview)（+ pythonnet / clr_loader / bottle / proxy_tools） | GUI のウィンドウ | BSD-3-Clause（依存は MIT） | pip |
| Microsoft Edge WebView2 ランタイム | GUI の描画 | Microsoft のライセンス（Windows に標準搭載、同梱しない） | OS |

### 配布用 exe（Electron 版）に同梱するもの

配布用 exe（`electron/`）には次のものだけを同梱します。Python・PyTorch・MediaPipe・OpenCV・ffmpeg（imageio-ffmpeg）・
モデルは同梱せず、上の表のとおり初回起動時に各配布元から利用者の環境（`%LOCALAPPDATA%\Replace2VRM`）へ取得します。
Node.js・Puppeteer・Chrome for Testing は使いません（レンダーは Electron が行います）。

| コンポーネント | 用途 | ライセンス | 同梱物の中のライセンス文 |
|---|---|---|---|
| [Electron](https://github.com/electron/electron) | アプリのウィンドウ、VRM のレンダー | MIT（同梱の Chromium・Node.js などは各ライセンス） | `LICENSE.electron.txt`、`LICENSES.chromium.html` |
| Electron の ffmpeg.dll（H.264 / AAC のデコーダーを含まない版） | Chromium の動作に必要な部品（このアプリは動画を再生しない） | LGPL-2.1 | `LICENSES.chromium.html`。対応するソースコードを `resources/third_party_sources/ffmpeg/` に同梱 |
| [uv](https://github.com/astral-sh/uv) | 初回起動時に Python と依存パッケージを導入 | MIT または Apache-2.0 | `resources/uv/LICENSE-MIT`、`LICENSE-APACHE` |
| [three.js](https://github.com/mrdoob/three.js)、[@pixiv/three-vrm](https://github.com/pixiv/three-vrm) | VRM のレンダー | MIT | `resources/vrm_replace/render/node_modules/*/LICENSE` |

ffmpeg.dll は、Electron の公式リリースにある ffmpeg.dll（H.264 / AAC のデコーダーを含まない版）に差し替えています（`electron/scripts/after-pack.js`）。これは ffmpeg.dll 単体についてのことで、Electron 本体は Windows や GPU のデコード機能を使ってH.264 の動画を再生できます。このアプリは画面で動画を再生しません。
LGPL-2.1 の条件に従い、その元になったソースコード（Electron が使う Chromium の DEPS で固定された FFmpeg のリビジョンと、Electron が当てているパッチ。Chromium 用のビルド設定を含む）を配布物に同梱しています。入手元・リビジョンと、ビルドに使う Chromium・Electron のビルドの仕組みの入手先は `resources/third_party_sources/ffmpeg/README.txt` にあります。ffmpeg.dll は別のファイルなので、利用者が自分でビルドしたものに差し替えられます。

## 商用利用について

既定の構成（上の表）は、コードと重みがいずれも商用利用を許すライセンスで配布されているものだけを使います。
ただし次の点は利用者側で確認してください（このリポジトリはそれを保証しません）。

- **AI モデルの採用基準**（2026-11-04 決定）: 「学習データを公開していない、かつ商用可」または
  「学習データも学習済みモデルも商用可」のものだけを使う。重みのライセンスが商用可でも、学習データが研究用のものは使わない。
  - 使っている AI モデル: MediaPipe（前者。モデルの説明書で Apache-2.0、学習データは同意を得た撮影画像で非公開）
  - 学習データの条件で外したもの: SAM 2（SA-1B）、RAFT（FlyingChairs / FlyingThings3D / KITTI）、LaMa（Places）、ProPainter（非商用ライセンス）
  - 学習データを使わない処理: NVIDIA Optical Flow（ハードウェア）、OpenCV（Telea / ShiftMap）
- **NVIDIA Optical Flow**: NVIDIA は「商用アプリを含むあらゆるアプリで無料」としています。実行部分はドライバーに含まれ、
  このリポジトリは SDK を同梱・配布しません（公開ヘッダー（BSD-3）に合わせた定義を `stages/nvof.py` に書いています）。
- **出力動画の形式**: mp4 は H.264（NVIDIA の GPU に内蔵された NVENC で書き出し）/ AAC、mov は GoPro CineForm / PCM です。H.264 / AAC には特許のライセンス条件があります（Via LA）。作った動画の利用は利用者の責任で、条件の概要は README の「利用上の注意」にあります。CineForm は GoPro が Apache-2.0 で公開している、特許料のない形式です
- **SMPL 系のモーキャプ**: `mocap_backend: smpl_npz` / `gvhmr` は外部で作った SMPL の結果を読みます。
  SMPL モデルとそれを使う推定器（GVHMR など）は多くが非商用ライセンスです。既定の `mediapipe` は該当しません。
- 以前のバージョンで使っていた ProPainter（S-Lab License 1.0、非商用）は使わなくなりました。
  古い環境に `third_party/ProPainter` が残っていても参照されません。

## モデルの再配布について

上記のモデル・重みはこのリポジトリに含めていません。`setup_env.py` が配布元から直接ダウンロードします。
VRM モデルと入力動画は利用者が用意するものであり、それぞれの利用条件（VRM のメタ情報に記載の許諾など）に従ってください。
