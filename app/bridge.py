"""
Electron 版の処理側の入口。electron/main.js が子プロセスとして起動し、標準入出力で app/backend.py の Api を呼ぶ。

  要求（1 行 1 JSON）: {"id": 1, "method": "poll", "args": [3, 120]}
  応答（1 行 1 JSON）: {"id": 1, "result": ...}  または  {"id": 1, "error": "..."}

pywebview 版（gui.py）と同じく、呼び出しはそれぞれ別スレッドで処理する（poll と start などが重なってよい）。
標準出力は応答専用にし、print などの出力は標準エラーへ回す。
"""
import json
import sys
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    out = open(sys.stdout.fileno(), "w", encoding="utf-8", buffering=1, closefd=False)
    sys.stdout = sys.stderr
    from app.backend import Api
    api = Api()
    wlock = threading.Lock()

    def reply(msg):
        with wlock:
            out.write(json.dumps(msg, ensure_ascii=False) + "\n")
            out.flush()

    def handle(req):
        rid = req.get("id")
        try:
            name = req["method"]
            if name.startswith("_") or not callable(getattr(api, name, None)):
                raise AttributeError(f"unknown method: {name}")
            reply({"id": rid, "result": getattr(api, name)(*req.get("args", []))})
        except Exception as e:
            traceback.print_exc()
            reply({"id": rid, "error": f"{type(e).__name__}: {e}"})

    reply({"id": 0, "result": "ready"})
    for line in sys.stdin.buffer:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line.decode("utf-8"))
        except ValueError:
            continue
        threading.Thread(target=handle, args=(req,), daemon=True).start()
    # Electron が終了した（標準入力が閉じた）: 実行中の処理も止める
    try:
        api.stop()
    except Exception:
        pass


if __name__ == "__main__":
    main()
