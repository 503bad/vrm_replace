"""
Replace2VRM（vrm_replace）の GUI（pywebview + WebView2）。

  python gui.py          # run.cmd からは pythonw で起動される

画面は app/ui（HTML/CSS/JS）、処理は app/backend.py。動画と VRM はウィンドウにドロップするだけ（種類は拡張子で判別）。
「START」で、ショット分割 → 人物の選択（画面内でクリック）→ 残りの工程を放置で実行し、終わったら結果を表示する。
設定は gui_settings.json に保存。
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main():
    from common import gpu_error
    err = gpu_error()
    if err:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, err, "Replace2VRM", 0x10)   # MB_ICONERROR。pythonw なのでダイアログで知らせる
        sys.exit(1)

    import webview
    from webview.dom import DOMEventHandler

    from app.backend import Api

    api = Api()
    window = webview.create_window(
        "Replace2VRM", url=str(ROOT / "app" / "ui" / "index.html"), js_api=api,
        width=1280, height=860, min_size=(1100, 720), background_color="#0F1114", text_select=False)
    api._window = window

    def on_drop(e):
        for f in e.get("dataTransfer", {}).get("files", []):
            p = f.get("pywebviewFullPath")
            if p:
                api.set_file(p)

    def on_loaded():
        window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True, stop_propagation=False)

    def on_closing():
        if api.is_busy():
            if not window.create_confirmation_dialog("Replace2VRM", "実行中です。停止して終了しますか？"):
                return False
            api.stop()
        return True

    window.events.loaded += on_loaded
    window.events.closing += on_closing
    webview.start(gui="edgechromium", private_mode=False, storage_path=str(ROOT / "logs" / "webview"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        (ROOT / "logs").mkdir(exist_ok=True)
        (ROOT / "logs" / "gui_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
