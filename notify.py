#!/usr/bin/env python3
"""
ntfy.sh 経由でスマホにプッシュ通知を送る小さなモジュール。
Claude(MCP)を介さないので、GitHub ActionsやWindowsタスクスケジューラから直接使える。

トピック名は実質的な合言葉なので、コードにも設定ファイルにも書かず、環境変数
`NTFY_TOPIC` から読む（GitHub Actionsでは Secrets に登録する）。

ヘッダにタイトルを入れる方式は非ASCII(日本語)が壊れることがあるため、
JSON publish方式( POST https://ntfy.sh/ にJSON本文 )を使う。
"""
import json
import os
import urllib.error
import urllib.request

DEFAULT_SERVER = "https://ntfy.sh"


# ntfyのJSON APIは priority を 1〜5 の数値で受け取る。文字列を渡すと400になるため、
# 呼び出し側が名前で書けるようにここで変換する。
PRIORITY_NAMES = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5, "max": 5}


TOPIC_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ntfy_topic.txt")


def get_topic():
    """トピック名を取得する。環境変数 → ローカルファイル の順で探す。

    - GitHub Actions では Secrets から環境変数で渡す。
    - Windowsタスクスケジューラからの実行では環境変数を引き継ぎにくいので、
      ローカルの ntfy_topic.txt（.gitignore済み）を読む。
    どちらも末尾の改行・空白が混入すると ntfy が400を返すため、必ず削る。
    """
    topic = os.environ.get("NTFY_TOPIC")
    if topic and topic.strip():
        return topic.strip()

    try:
        with open(TOPIC_FILE, encoding="utf-8") as f:
            content = f.read().strip()
            return content or None
    except OSError:
        return None


def is_configured():
    return bool(get_topic())


def send(message, title=None, tags=None, priority=None, click=None, timeout=15):
    """1件通知を送る。成功可否をdictで返す（例外は投げない）。"""
    topic = get_topic()
    if not topic:
        return {"ok": False, "error": "NTFY_TOPIC is not set"}

    server = os.environ.get("NTFY_SERVER", DEFAULT_SERVER).rstrip("/")
    payload = {"topic": topic, "message": message}
    if title:
        payload["title"] = title
    if tags:
        payload["tags"] = tags
    if priority:
        payload["priority"] = PRIORITY_NAMES.get(priority, priority) if isinstance(priority, str) else priority
    if click:
        payload["click"] = click

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        server + "/",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": 200 <= resp.status < 300, "status": resp.status}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    import sys

    msg = sys.argv[1] if len(sys.argv) > 1 else "sanrio_watch テスト通知"
    print(json.dumps(send(msg, title="サンリオ新商品ウォッチ", tags=["test_tube"]), ensure_ascii=False))
