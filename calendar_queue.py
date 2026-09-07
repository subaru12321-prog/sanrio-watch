#!/usr/bin/env python3
"""
Googleカレンダー登録の待ち行列を扱うヘルパー。

役割分担:
  - クラウド(GitHub Actions)は検知とプッシュ通知を担当し、予約日が取れたアイテムに
    `calendar_pending: true` を立てて state/*.json をコミットする。
  - Claude(このPCでアプリが起動しているとき)がそれを拾ってGoogleカレンダーに登録する。
    カレンダー登録はOAuthが要るためクラウド側からは打てない。

`state/*.json` はクラウドが所有する（git経由で更新される）ので、Claude側は書き換えない。
代わりに「登録済み」の記録はローカル専用の calendar_done.json に持つ（.gitignore済み）。
こうするとgitの競合が起きず、二重登録も防げる。

使い方:
  python calendar_queue.py pending
      → カレンダー未登録の予約アイテムをJSONで一覧表示
  python calendar_queue.py done --item-id 1_1_2609191621 --event-id abc123
      → 登録済みとして記録する
"""
import argparse
import json
import os
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "state")
DONE_PATH = os.path.join(BASE_DIR, "calendar_done.json")


def load_done():
    if not os.path.exists(DONE_PATH):
        return {}
    try:
        with open(DONE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        # 壊れていたら空として扱う（最悪カレンダーに重複が入るが、処理は止めない）
        return {}


def save_done_atomic(data):
    fd, tmp_path = tempfile.mkstemp(dir=BASE_DIR, prefix=".calendar_done.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, DONE_PATH)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def cmd_pending():
    done = load_done()
    pending = []

    if not os.path.isdir(STATE_DIR):
        return {"ok": True, "pending": []}

    for fname in sorted(os.listdir(STATE_DIR)):
        if not fname.endswith(".json") or fname.startswith("."):
            continue
        key = fname[:-5]
        try:
            with open(os.path.join(STATE_DIR, fname), encoding="utf-8") as f:
                state = json.load(f)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"failed to read {fname}: {e}"}

        for item_id, rec in state.get("seen", {}).items():
            if not rec.get("calendar_pending"):
                continue
            if item_id in done:
                continue
            if not rec.get("preorder_date"):
                continue
            pending.append({
                "key": key,
                "item_id": item_id,
                "name": rec.get("name"),
                "price": rec.get("price"),
                "url": rec.get("url"),
                "preorder_date": rec.get("preorder_date"),
            })

    return {"ok": True, "pending": pending}


def cmd_done(item_id, event_id):
    done = load_done()
    done[item_id] = {"event_id": event_id}
    save_done_atomic(done)
    return {"ok": True, "recorded": item_id}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("pending")

    p_done = sub.add_parser("done")
    p_done.add_argument("--item-id", required=True)
    p_done.add_argument("--event-id", required=True)

    args = ap.parse_args()

    if args.command == "pending":
        result = cmd_pending()
    else:
        result = cmd_done(args.item_id, args.event_id)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
