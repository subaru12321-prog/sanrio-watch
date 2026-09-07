#!/usr/bin/env python3
"""
sanrio_watch の日次チェック本体（決定的処理のみ、通知・カレンダー登録などLLM/MCP側の
作業は一切行わない）。

やること:
  1. config.json の有効なキャラクターごとに fetch_sanrio.py 相当のロジックで一覧取得
  2. state/<key>.json の既知ID一覧と突き合わせて新規アイテムを検出
  3. 初回(baseline未確立)は通知対象にせずベースラインとして保存するだけ
  4. 新規アイテムが閾値(THRESHOLD)を超えたら個別処理をせず「要確認」フラグを立てる
  5. state ファイルは一時ファイル→rename で原子的に書き込み、壊れていたら
     ベースライン再構築(=通知なし)にフォールバックする
  6. 結果をJSONで標準出力に返す。実際の通知(プッシュ/カレンダー/Gmail)はこのJSONを見て
     呼び出し側(Claude)が行う。

使い方:
  python run_check.py            # 全キャラクターをチェック
  python run_check.py --weekly   # 上記に加えて週次サマリ用の集計も含める
"""
import argparse
import datetime
import json
import os
import sys
import tempfile

import fetch_sanrio
import fetch_munyugurumi
import notify

SITE_MODULES = {
    "sanrio_shop": fetch_sanrio,
    "munyugurumi": fetch_munyugurumi,
}

THRESHOLD = 5  # これを超える新規アイテムは個別通知せず要確認扱い
SEEN_RETENTION_DAYS = 90  # 30日窓の3倍。これより古いseenエントリは削除してよい

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_DIR = os.path.join(BASE_DIR, "state")


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def state_path(key):
    return os.path.join(STATE_DIR, f"{key}.json")


def load_state(key):
    path = state_path(key)
    if not os.path.exists(path):
        return {"baseline_established": False, "seen": {}, "last_checked": None}, False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if "seen" not in data or "baseline_established" not in data:
            raise ValueError("missing required keys")
        return data, False
    except Exception:  # noqa: BLE001
        # 破損している場合はベースライン再構築(=今回は通知しない)にフォールバック
        return {"baseline_established": False, "seen": {}, "last_checked": None}, True


def save_state_atomic(key, data):
    path = state_path(key)
    fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, prefix=f".{key}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def prune_old_seen(seen, today):
    cutoff = today - datetime.timedelta(days=SEEN_RETENTION_DAYS)
    pruned = {}
    for item_id, rec in seen.items():
        try:
            first_seen = datetime.date.fromisoformat(rec.get("first_seen", ""))
        except ValueError:
            pruned[item_id] = rec
            continue
        if first_seen >= cutoff:
            pruned[item_id] = rec
    return pruned


def build_seen_record(it, today_iso, **extra):
    rec = {
        "first_seen": today_iso,
        "name": it["name"],
        "price": it["price"],
        "url": it["url"],
        "calendar_event_id": None,
        "is_preorder": it.get("is_preorder", False),
        "preorder_date": it.get("preorder_date"),
    }
    rec.update(extra)
    return rec


def check_character(char_cfg, today_iso, today, weekly):
    key = char_cfg["key"]
    result = {
        "key": key,
        "display_name": char_cfg["display_name"],
        "status": None,
        "new_items": [],
        "total_count": None,
        "recent_7d": [],
    }

    state, was_corrupted = load_state(key)
    if was_corrupted:
        result["state_was_corrupted"] = True

    site = char_cfg.get("site", "sanrio_shop")
    module = SITE_MODULES.get(site)
    if module is None:
        result["status"] = "fetch_error"
        result["error"] = f"unknown site: {site}"
        return result

    # 1サイトの失敗が他のキャラクターのチェックまで巻き込まないよう、ここで必ず捕まえる
    try:
        fetch_result = module.run_listing(
            char_cfg["lookup_mode"], char_cfg["lookup_value"], 30
        )
    except Exception as e:  # noqa: BLE001
        fetch_result = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    if not fetch_result.get("ok"):
        result["status"] = "fetch_error"
        result["error"] = fetch_result.get("error")
        # 取得失敗時はstateを一切変更しない(暴発防止)
        return result

    items = {it["id"]: it for it in fetch_result["items"]}
    result["total_count"] = fetch_result.get("total_count")

    if not state["baseline_established"]:
        # 初回: 通知せずベースラインとして保存するだけ
        seen = {
            item_id: build_seen_record(it, today_iso, from_baseline=True)
            for item_id, it in items.items()
        }
        state = {
            "baseline_established": True,
            "seen": seen,
            "last_checked": today_iso,
        }
        save_state_atomic(key, state)
        result["status"] = "baseline_established"
        result["baseline_count"] = len(items)
        return result

    known_ids = set(state["seen"].keys())
    new_ids = [i for i in items.keys() if i not in known_ids]

    if len(new_ids) > THRESHOLD:
        # 暴発ガード: 個別通知せず、まとめて既知として記録し「要確認」だけ返す
        for item_id in new_ids:
            it = items[item_id]
            state["seen"][item_id] = build_seen_record(
                it, today_iso, needs_manual_review=True, from_baseline=False
            )
        state["seen"] = prune_old_seen(state["seen"], today)
        state["last_checked"] = today_iso
        save_state_atomic(key, state)
        result["status"] = "threshold_exceeded"
        result["new_item_count"] = len(new_ids)
        return result

    for item_id in new_ids:
        it = items[item_id]
        # 新規アイテムだけ予約状況を補足する（サイトによっては詳細ページを1回取得する）
        try:
            enriched = module.enrich_new_item(it)
        except Exception:  # noqa: BLE001
            enriched = {"is_preorder": False, "preorder_date": None}
        it.update(enriched)
        # 予約日が取れたものは、Claude側がカレンダー登録するまで pending として残す
        calendar_pending = bool(enriched.get("preorder_date"))
        state["seen"][item_id] = build_seen_record(
            it, today_iso, from_baseline=False, calendar_pending=calendar_pending
        )
        result["new_items"].append(it)

    if weekly:
        cutoff = today - datetime.timedelta(days=7)
        for item_id, rec in state["seen"].items():
            try:
                fs = datetime.date.fromisoformat(rec["first_seen"])
            except (ValueError, KeyError):
                continue
            if fs >= cutoff and not rec.get("from_baseline"):
                result["recent_7d"].append({"id": item_id, **rec})

    state["seen"] = prune_old_seen(state["seen"], today)
    state["last_checked"] = today_iso
    save_state_atomic(key, state)

    result["status"] = "ok"
    return result


def format_price(price):
    return f"¥{price:,}" if isinstance(price, int) else "価格不明"


def send_notifications(results, weekly, today):
    """ntfy経由でプッシュ通知を送る（Claudeを介さない経路）。送信結果の一覧を返す。"""
    sent = []

    for r in results:
        name = r["display_name"]
        status = r["status"]

        if status == "baseline_established":
            sent.append(notify.send(
                f"{name} の監視を開始しました（現在 {r.get('baseline_count', 0)} 件を記録）",
                title="サンリオ新商品ウォッチ 監視開始",
                tags=["white_check_mark"],
            ))
        elif status == "fetch_error":
            sent.append(notify.send(
                f"{name} の取得に失敗しました（要確認）: {r.get('error')}",
                title="⚠️ サンリオ監視エラー",
                tags=["warning"],
                priority="high",
            ))
        elif status == "threshold_exceeded":
            sent.append(notify.send(
                f"{name} で一度に {r.get('new_item_count')} 件の新商品を検知しました。"
                "通常と異なるため個別通知を保留しています。サイトで直接確認してください。",
                title="⚠️ サンリオ監視 要確認",
                tags=["warning"],
                priority="high",
            ))
        elif status == "ok":
            for it in r["new_items"]:
                body = f"{it['name']}\n{format_price(it.get('price'))}"
                if it.get("preorder_date"):
                    body += f"\n予約商品・発送予定: {it['preorder_date']}"
                elif it.get("is_preorder"):
                    body += "\n予約商品"
                sent.append(notify.send(
                    body,
                    title=f"🎀 新商品: {name}",
                    tags=["ribbon"],
                    click=it.get("url"),
                ))

    # 週次サマリ（月曜日のみ）。新商品ゼロの週も「生きている」ことを知らせるために送る。
    if weekly and today.weekday() == 0:
        lines = []
        for r in results:
            recent = r.get("recent_7d", [])
            total = r.get("total_count")
            lines.append(f"■ {r['display_name']}（監視中 {total if total is not None else '?'} 件）")
            if recent:
                for rec in recent:
                    lines.append(f"  ・{rec.get('name')} {format_price(rec.get('price'))}")
            else:
                lines.append("  今週の新商品はありません")
        sent.append(notify.send(
            "\n".join(lines),
            title=f"📋 週次サマリ（{today.isoformat()}）",
            tags=["calendar"],
        ))

    return sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--notify", action="store_true", help="ntfy経由でプッシュ通知も送る")
    args = ap.parse_args()

    today = datetime.date.today()
    today_iso = today.isoformat()

    config = load_config()
    results = []
    for char_cfg in config["characters"]:
        if not char_cfg.get("enabled", True):
            continue
        results.append(check_character(char_cfg, today_iso, today, args.weekly))

    output = {"checked_at": today_iso, "results": results}

    if args.notify:
        if not notify.is_configured():
            output["notify"] = {"ok": False, "error": "NTFY_TOPIC is not set; no push sent"}
        else:
            sent = send_notifications(results, args.weekly, today)
            output["notify"] = {
                "attempted": len(sent),
                "failed": [s for s in sent if not s.get("ok")],
            }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
