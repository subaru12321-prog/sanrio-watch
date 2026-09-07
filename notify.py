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


def is_configured():
    return bool(os.environ.get("NTFY_TOPIC"))


def send(message, title=None, tags=None, priority=None, click=None, timeout=15):
    """1件通知を送る。成功可否をdictで返す（例外は投げない）。"""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return {"ok": False, "error": "NTFY_TOPIC is not set"}

    server = os.environ.get("NTFY_SERVER", DEFAULT_SERVER).rstrip("/")
    payload = {"topic": topic, "message": message}
    if title:
        payload["title"] = title
    if tags:
        payload["tags"] = tags
    if priority:
        payload["priority"] = priority
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
