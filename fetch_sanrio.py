#!/usr/bin/env python3
"""
サンリオ公式オンラインショップ (shop.sanrio.co.jp) の商品一覧を取得・パースする
決定的スクリプト（LLMを使わない）。標準出力にJSONを1行で返す。

使い方:
  python fetch_sanrio.py --mode character_id --value 7 --new-days 30
  python fetch_sanrio.py --mode freeword --value もんきち
  python fetch_sanrio.py --mode detail --url "https://shop.sanrio.co.jp/item/detail/1_1_2608551783_1/MX_-/-"

出力(成功時、mode=character_id/freeword):
  {"ok": true, "total_count": 5, "items": [{"id": "2608551783", "name": "...", "price": 1760, "url": "..."}]}
出力(成功時、mode=detail):
  {"ok": true, "release_date": "2026-09-15", "found_keyword": "発売予定日"} または release_date: null
出力(失敗時):
  {"ok": false, "error": "説明"}
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
BASE = "https://shop.sanrio.co.jp/item"
MAX_PAGES = 10  # safety cap


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


def parse_listing(html):
    """商品カード(data-goods_params)を抽出し、common_goods_codeで重複排除する。"""
    items = {}
    # 各商品カードのdata-goods_params JSON片を拾う
    for m in re.finditer(
        r'data-goods_params=(\{[^}]*"common_goods_code"\s*:\s*"([^"]+)"[^}]*\})',
        html,
    ):
        raw_json, code = m.group(1), m.group(2)
        # このcodeが既出ならスキップ（色違いバリエーション等の重複）
        if code in items:
            continue

        block_start = m.end()
        # 次のカード境界(次のdata-goods_params=)までを1商品分のブロックとする
        next_m = re.search(r'data-goods_params=', html[block_start:])
        block_end = block_start + next_m.start() if next_m else min(len(html), block_start + 8000)
        block = html[block_start:block_end]

        name_m = re.search(r'data-ga_ec_goods_name="([^"]*)"', block)
        name = urllib.parse.unquote(name_m.group(1)) if name_m else None
        if name:
            # HTMLエンティティの簡易デコード
            name = (
                name.replace("&quot;", '"')
                .replace("&amp;", "&")
                .replace("&#39;", "'")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
            )

        price_m = re.search(r'<span class="price"[^>]*>\s*([\d,]+)円', block)
        price = int(price_m.group(1).replace(",", "")) if price_m else None

        href_m = re.search(r'href="(/item/detail/[^"]+)"', block)
        if href_m:
            url = "https://shop.sanrio.co.jp" + href_m.group(1)
        else:
            # 商品カードの構造が一部違ってhrefを拾えないことがあるため、
            # トラッキング用データ属性に埋まっているURLをフォールバックとして使う。
            ltv_m = re.search(r'&quot;url&quot;:&quot;(https:[^&]*?)&quot;', block)
            url = ltv_m.group(1).replace("\\/", "/") if ltv_m else None

        items[code] = {"id": code, "name": name, "price": price, "url": url}

    return items


def extract_total_count(html):
    m = re.search(r'search-result"><span>([\d,]+)件', html)
    if m:
        return int(m.group(1).replace(",", ""))
    if "該当する商品" in html:
        # 0件時は件数スパン自体が出ず、専用の「該当する商品はございません」テンプレートになる
        return 0
    return None


def run_listing(mode, value, new_days):
    all_items = {}
    total_count = None
    for page in range(1, MAX_PAGES + 1):
        params = {"page": page}
        if mode == "character_id":
            params["new"] = new_days
            params["character_id[]"] = value
        elif mode == "freeword":
            params["freeword"] = value
        else:
            raise ValueError(f"unknown mode: {mode}")

        url = BASE + "?" + urllib.parse.urlencode(params, doseq=True)
        html = fetch(url)

        if total_count is None:
            total_count = extract_total_count(html)
            if total_count is None:
                # 件数表示が見つからない = ページ構造が変わった可能性
                return {"ok": False, "error": "total_count pattern not found (site structure may have changed)"}

        page_items = parse_listing(html)
        before = len(all_items)
        all_items.update(page_items)

        if total_count == 0:
            break
        if len(all_items) >= total_count:
            break
        if len(page_items) == 0:
            # このページに商品カードが見つからない＝末尾に到達 or パース失敗
            break
        if len(all_items) == before:
            # 新規アイテムが増えなかった＝ページングが効いていない
            break

    if total_count is not None and total_count > 0 and len(all_items) == 0:
        return {"ok": False, "error": "site reported nonzero total_count but no items parsed (parser likely broken)"}

    return {"ok": True, "total_count": total_count, "items": list(all_items.values())}


def run_detail(url):
    try:
        html = fetch(url)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"detail fetch failed: {e}"}

    for keyword in ["発売予定日", "先行予約", "予約受付", "発売日"]:
        idx = html.find(keyword)
        if idx == -1:
            continue
        window = html[idx: idx + 200]
        date_m = re.search(r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})日?", window)
        if date_m:
            y, mo, d = date_m.groups()
            return {
                "ok": True,
                "release_date": f"{int(y):04d}-{int(mo):02d}-{int(d):02d}",
                "found_keyword": keyword,
            }
    return {"ok": True, "release_date": None, "found_keyword": None}


def enrich_new_item(item):
    """新規アイテム1件について予約状況を補足する。

    サンリオ公式ショップは一覧に発売予定日を持たないため、詳細ページを1回だけ取得する。
    新規アイテムにしか呼ばれない（かつ閾値ガードで最大5件）ので、リクエスト数は抑えられる。
    """
    result = run_detail(item["url"])
    if not result.get("ok"):
        return {"is_preorder": False, "preorder_date": None}
    release_date = result.get("release_date")
    return {"is_preorder": release_date is not None, "preorder_date": release_date}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["character_id", "freeword", "detail"])
    ap.add_argument("--value")
    ap.add_argument("--url")
    ap.add_argument("--new-days", type=int, default=30)
    args = ap.parse_args()

    try:
        if args.mode == "detail":
            if not args.url:
                result = {"ok": False, "error": "--url is required for mode=detail"}
            else:
                result = run_detail(args.url)
        else:
            if not args.value:
                result = {"ok": False, "error": "--value is required"}
            else:
                result = run_listing(args.mode, args.value, args.new_days)
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "error": f"unhandled exception: {e}"}

    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
