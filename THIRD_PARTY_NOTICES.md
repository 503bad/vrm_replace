# Third-party notices / サードパーティのライセンス

vrm_replace 本体は Apache License 2.0（`LICENSE`）です。
以下はこのリポジトリに **同梱していない** が、`run.cmd`（`setup_env.py`）が利用者の環境に取得・導入する
ソフトウェアとモデルです。それぞれのライセンスは各配布元の条件に従います。

| コンポーネント | 用途 | ライセンス | 取得元 |
|---|---|---|---|
| [SAM 2](https://github.com/facebookresearch/sam2) + チェックポイント `sam2.1_hiera_base_plus.pt` | 人物マスクの追跡 | Apache-2.0 | GitHub / dl.fbaipublicfiles.com |
| [MediaPipe](https://github.com/google-ai-edge/mediapipe) Pose Landmarker / Hand Landmarker | モーションキャプチャ・指 | Apache-2.0 | pip / storage.googleapis.com |
| [ProPainter](https://github.com/sczhou/ProPainter) + 重み (`ProPainter.pth`, `recurrent_flow_completion.pth`, `raft-things.pth`) | 人物を消したクリーンプレート | **S-Lab License 1.0（非商用・研究目的に限定）** | GitHub |
| [three.js](https://github.com/mrdoob/three.js) | レンダリング | MIT | npm |
| [@pixiv/three-vrm](https://github.com/pixiv/three-vrm) | VRM の読み込み・MToon・スプリングボーン | MIT | npm |
| [Puppeteer](https://github.com/puppeteer/puppeteer) + Chrome for Testing | ヘッドレス GPU レンダリング | Apache-2.0 / Chrome の利用規約 | npm |
| [PyTorch](https://pytorch.org/) | SAM 2 / ProPainter の実行 | BSD-3-Clause | pip (download.pytorch.org) |
| [PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | ショット分割 | BSD-3-Clause | pip |
| [OpenCV](https://opencv.org/) | 画像処理 | Apache-2.0 | pip |
| [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | ffmpeg バイナリ | BSD-2-Clause（同梱 ffmpeg バイナリは GPL/LGPL） | pip |
| NumPy / SciPy / Pillow / PyYAML / tqdm / einops / PyAV | 各種 | BSD / MIT / HPND | pip |

## ProPainter について（重要）

ProPainter とその重みは S-Lab License 1.0 で配布されており、**非商用・研究目的**に限って利用できます。
クリーンプレート工程（工程 3）は ProPainter を使うため、商用利用を想定する場合は
この工程を別のインペインタに置き換えるか、`--no-cleanplate`（GUI の「クリーンプレート」OFF）で無効化してください。
このリポジトリには ProPainter のコードも重みも含まれておらず、`setup_env.py` が利用者の環境で
GitHub から取得します。

## モデルの再配布について

上記のモデル・重みはこのリポジトリに含めていません。`setup_env.py` が配布元から直接ダウンロードします。
VRM モデルと入力動画は利用者が用意するものであり、それぞれの利用条件（VRM のメタ情報に記載の許諾など）に従ってください。
