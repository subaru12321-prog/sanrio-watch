# サンリオ新商品ウォッチ (sanrio_watch)

特定のサンリオキャラクターの新商品だけを検知して、スマホにプッシュ通知する個人用ツール。

監視対象（`config.json`で管理）:

| キャラクター | サイト | 絞り込み方法 |
|---|---|---|
| けろっぴ | サンリオ公式ショップ (shop.sanrio.co.jp) | `character_id=7` |
| おさるのもんきち | サンリオ公式ショップ | `character_id=30` |
| けろっぴ | むにゅぐるみパティオ (munyugurumi.jp) | `character=KR` |

## 動く仕組み

```
Windowsタスクスケジューラ (毎日 09:00)
   ↓ run_check.py --weekly --notify
   ├─ 各サイトの一覧を取得（fetch_sanrio.py / fetch_munyugurumi.py）
   ├─ state/*.json の既知IDと突き合わせて新規だけ抽出
   └─ ntfy.sh 経由でスマホにプッシュ通知
```

**Claudeが起動していなくても動く。** 取得・差分検知・通知はすべて標準ライブラリだけの
Pythonスクリプトで完結しており、LLMは介在しない（＝壊れにくく、実行コストもゼロ）。

ただし **PCの電源は入っている必要がある。** クラウド(GitHub Actions)での実行も試したが、
**監視対象3サイトすべてがGitHub ActionsのIPレンジからのアクセスに403を返す**ため断念した
（2026-09-07実測。ブラウザ相当のヘッダを付けても変わらないので、UAではなくIPベースの遮断。
同じスクリプトがローカルPCからは正常に動く）。相手が意図的に設けているアクセス制限なので、
プロキシ等での迂回はしない方針。`.github/workflows/watch.yml` は手動実行だけ残してある。

例外はGoogleカレンダー登録だけ。OAuthが必要でクラウドから打てないため、**予約商品が
見つかったときだけ** `calendar_pending` フラグを立てて溜めておき、Claudeが起動している
ときにまとめて登録する（`calendar_queue.py`）。通知自体は必ず飛ぶので、カレンダー登録が
遅れても取りこぼしにはならない。

## セットアップ

### 1. スマホでntfyを購読する（プッシュ通知の受け取り側）

1. スマホに **ntfy** アプリを入れる（[Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iOS](https://apps.apple.com/us/app/ntfy/id1625396347)）
2. アプリで「＋」→ 購読するトピック名を入力する

トピック名は `sanrio-watch-` + ランダムな英数字20文字程度にしておく。未設定なら
以下で生成できる:

```bash
python -c "import secrets,string; print('sanrio-watch-' + ''.join(secrets.choice(string.ascii_lowercase+string.digits) for _ in range(20)))"
```

> **⚠️ トピック名は実質的な合言葉。このリポジトリはPublicなので、ここには絶対に書かないこと。**
> 知っている人は誰でも通知を読めるし、偽の通知を送ることもできる。実際の値はスマホのntfyアプリと
> GitHubのSecretにだけ入れる。漏れたと思ったら、新しい名前を生成してアプリの購読とSecretを
> 差し替えれば無効化できる（アカウントが無いぶん、名前を変えるだけで済む）。

### 2. トピック名をローカルに置く

このディレクトリに `ntfy_topic.txt` を作り、トピック名だけを書く（改行や空白が混ざると
ntfyが400を返すので、スクリプト側で除去している）。このファイルは`.gitignore`済みで
コミットされない。

```bash
echo -n "あなたのトピック名" > ntfy_topic.txt
```

環境変数 `NTFY_TOPIC` があればそちらが優先される（GitHub Actions用）。

### 3. タスクスケジューラに登録する

毎日1回 `run_check.py --weekly --notify` を実行するよう登録する。登録済みなら:

```powershell
schtasks /query /tn "sanrio-watch"      # 確認
schtasks /run /tn "sanrio-watch"        # 手動実行
schtasks /change /tn "sanrio-watch" /disable   # 一時停止
```

### 4. 動作確認

```bash
python notify.py "テスト"
```

スマホに届けば設定完了。新商品が無い日は通知が飛ばないのが正常（月曜だけ週次サマリが届く）。

## ローカルで手動実行する

```bash
# 検知だけ（通知しない）
python run_check.py

# 通知も送る（要 NTFY_TOPIC 環境変数）
python run_check.py --weekly --notify

# 通知の単体テスト
python notify.py "テスト"
```

## 監視対象を追加する

`config.json` の `characters` に1ブロック追加する。

```json
{
  "key": "一意なID（stateファイル名になる）",
  "display_name": "通知に出る表示名",
  "enabled": true,
  "site": "sanrio_shop または munyugurumi",
  "lookup_mode": "character_id / freeword / character_code",
  "lookup_value": "7"
}
```

追加後の初回実行は自動的にベースライン登録（＝既存商品を大量通知しない）として扱われる。

**サンリオ公式のcharacter_idの調べ方:** `https://shop.sanrio.co.jp/item?character_id=<id>`
を開いて `<title>` を見る。キャラ選択UIには人気35キャラしか出ないが、URLパラメータとしては
それ以外のIDも有効（例: もんきち=30）。

## 安全側に倒している点

- **暴発ガード**: 一度に6件以上の新規を検知したら、個別通知せず「要確認」1通に切り替える
  （サイト構造変化やstate破損で大量誤通知が飛ぶのを防ぐ）
- **原子的書き込み**: stateは一時ファイル→renameで保存。壊れていたら通知せずベースライン再構築
- **取得失敗時はstateを更新しない**: 失敗を「全部消えた＝全部新商品」と誤認しないため
- **生存確認**: 毎週月曜に「監視中N件」のサマリを送る。通知が来ない＝正常なのか壊れたのか
  区別できるようにするため
- サイトへの書き込み操作は一切しない（読み取り専用）。アクセスは1日1回のみ

## 既知の制約

- GitHub Actionsのスケジュールは仕様上、数分〜数十分遅れることがある
- もんきちは商品自体がほとんど流通していない（公式ショップで現在1件、それも複数キャラ収録の
  ボードゲーム）。「通知が来ない」のが正常な状態
- むにゅぐるみパティオに「もんきち」のキャラクターコードは存在しない
- アベイル(しまむらパーク)はAkamai Bot Managerで保護されており、この方式では取得できない
  ため対象外（フルブラウザ自動操作が必要になり、壊れやすく検知リスクもあるため見送り）
