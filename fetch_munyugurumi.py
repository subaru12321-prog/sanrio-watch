#!/usr/bin/env python3
"""
むにゅぐるみパティオ (munyugurumi.jp) の商品一覧を取得・パースする決定的スクリプト。
fetch_sanrio.py と同じ出力契約({"ok":..., "total_count":..., "items":[...]})を守る。

サンリオ公式ショップと違い、この店は「発売予定日」を別ページではなく商品名自体に
【予約販売】【10月上旬頃発送予定】のような形で埋め込む方式。そのため detail モードは無く、
一覧取得だけで予約情報まで判定できる。

使い方:
  python fetch_munyugurumi.py --character KR --sort new
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
BASE = "https://munyugurumi.jp/itemlist"
MAX_PAGES = 10
PAGE_LIMIT = 120  # サイト側の最大表示件数オプション

# 上旬/中旬/下旬 のようなあいまいな日付表記をカレンダー登録用に丸めるための目安日
DECADE_DAY = {"上旬": 1, "中旬": 11, "下旬": 21}


def fetch(url, retries=2, timeout=15):
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"fetch failed after retries: {last_err}")


def extract_preorder_date(name):
    """商品名から予約発送予定日を推定する。見つからなければNoneを返す。"""
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", name)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    m = re.search(r"(\d{1,2})月(上旬|中旬|下旬)", name)
    if m:
        mo, decade = m.groups()
        mo = int(mo)
        today = time.localtime()
        year = today.tm_year
        # 「1月」等が現在より過去の月名なら来年と解釈(年またぎの予約表記対策)
        if mo < today.tm_mon:
            year += 1
        return f"{year:04d}-{mo:02d}-{DECADE_DAY[decade]:02d}"

    return None


def parse_listing(html):
    items = {}
    for m in re.finditer(
        r'href="(/itemdetail\?ItemID=(\d+)[^"]*)"[^>]*><div class="mod-goods-detail">.*?alt="([^"]*)".*?<p class="price">([\d,]*)円',
        html,
        re.S,
    ):
        href, item_id, name, price_raw = m.groups()
        if item_id in items:
            continue
        price = int(price_raw.replace(",", "")) if price_raw else None
        is_preorder = "予約" in name
        items[item_id] = {
            "id": item_id,
            "name": name,
            "price": price,
            "url": "https://munyugurumi.jp" + href,
            "is_preorder": is_preorder,
            "preorder_date": extract_preorder_date(name) if is_preorder else None,
        }
    return items


def extract_total_count(html):
    m = re.search(r'class="count">全([\d,]+)件', html)
    if m:
        return int(m.group(1).replace(",", ""))
    if "該当する商品" in html or "見つかりませんでした" in html:
        return 0
    return None


def run_listing(mode, value, new_days=None):
    if mode != "character_code":
        return {"ok": False, "error": f"unsupported mode for munyugurumi: {mode}"}

    all_items = {}
    total_count = None
    for page in range(1, MAX_PAGES + 1):
        params = {
            "character": value,
            "sort": "01",
            "order": "02",  # 新着順
            "limit": PAGE_LIMIT,
            "page": page,
        }
        url = BASE + "?" + urllib.parse.urlencode(params)
        html = fetch(url)

        if total_count is None:
            total_count = extract_total_count(html)
            if total_count is None:
                return {"ok": False, "error": "total_count pattern not found (site structure may have changed)"}

        page_items = parse_listing(html)
        before = len(all_items)
        all_items.update(page_items)

        if total_count == 0:
            break
        if len(all_items) >= total_count:
            break
        if len(page_items) == 0 or len(all_items) == before:
            break

    if total_count is not None and total_count > 0 and len(all_items) == 0:
        return {"ok": False, "error": "site reported nonzero total_count but no items parsed (parser likely broken)"}

    return {"ok": True, "total_count": total_count, "items": list(all_items.values())}


def enrich_new_item(item):
    """予約情報は一覧の商品名から既に判定済みなので、追加のHTTPリクエストは不要。"""
    return {
        "is_preorder": item.get("is_preorder", False),
        "preorder_date": item.get("preorder_date"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--character", required=True, help="サイトのキャラクターコード(例: KR = けろっぴ)")
    args = ap.parse_args()

    try:
        result = run_listing("character_code", args.character)
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "error": f"unhandled exception: {e}"}

    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
