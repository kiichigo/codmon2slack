# Codomon to Slack 転送ツール 設計書

## 1. 概要
Codomonアプリから保育園の通知（写真・連絡事項）を取得し、Slackに自動投稿するツール

### 目的
- Codomonの写真を拡大表示しやすいSlackで閲覧
- 夫婦間での情報共有を円滑化
- 定期的な自動取得・投稿

## 2. 要件

### 機能要件
- [x] Codomonへのログイン認証（メール・パスワード）
- [x] セッションCookieの管理
- [x] 新着通知の取得（JSON API）
- [x] 写真のダウンロード
- [x] Slackへの投稿（テキスト + 画像）
- [x] 定期実行（スケジューリング）
- [x] 重複投稿の防止（既読管理）
- [x] フェイルセーフ（Codmon APIエラー時はSlack警告を残し、自動実行を停止）
- [x] PDF添付ファイルの画像化・展開投稿
- [x] お知らせ本文のHTMLタグ除去・整形

### 非機能要件
- 認証情報の安全な管理（環境変数 or 設定ファイル）
- エラーハンドリング（ログイン失敗、API エラー等）
- ログ出力（実行履歴の記録）

## 3. 技術選定

### 使用言語・ライブラリ
- **Python 3.9+**
- `requests` - HTTP通信（Codomon API）
- `slack_sdk` - Slack投稿
- `python-dotenv` - 環境変数管理
- `pymupdf` (fitz) - PDFの画像変換
- `schedule` - 定期実行（または GitHub Actions / cron）

### ファイル構成（実績）
```
codomon/
├── .env                    # 認証情報（Git管理外）
├── .gitignore
├── requirements.txt
├── codmon_to_slack.py     # Slack転送スクリプト（旧 main.py）
├── codmon_archiver.py     # データ保存（アーカイブ）スクリプト
├── setup_task.ps1         # タスクスケジューラ登録用スクリプト
├── clean_slack.py         # Slack履歴削除用ツール
├── app.log                # 実行ログ
└── seen_ids.txt           # 投稿済みID管理（テキストファイル）
```

## 4. データフロー

```
1. 起動・設定読み込み
   ↓
2. Codomonログイン
   - POST /api/v2/parent/login (JSON Payload)
   - Cookie セッション取得
   ↓
3. 施設一覧取得
   - GET /api/v2/parent/services/
   - 施設IDを取得
   ↓
4. タイムライン取得
   - GET /api/v2/parent/timeline/
   - 直近3日分を取得
   ↓
5. 新着チェック & フィルタリング
   - seen_ids.txt と比較
   - timeline_kind で処理分岐
     - activities: 日々の様子（写真あり）
     - topics: お知らせ（PDFなど）
     - responses: 欠席連絡（スキップ）
   ↓
6. コンテンツ取得 & Slack投稿
   - 画像ダウンロード -> Slackへアップロード
   - PDFダウンロード -> Slackへアップロード -> 画像変換(全ページ) -> Slackへ連投
   - お知らせ本文(HTML) -> タグ除去・整形 -> Slackへ投稿
   ↓
7. 投稿済みID記録
   - seen_ids.txt に追記
```

## 5. API仕様（実装済み）

### Codomon API
- **Base URL**: `https://ps-api.codmon.com/api/v2/parent`
- **共通ヘッダー**: `Origin`, `Referer` が必須
- **ファイルダウンロード時の注意**:
  - `parents.codmon.com` ドメインのURLは `ps-api.codmon.com` に置換が必要
  - API認証後のリダイレクト（S3等への署名付きURL）を追跡する必要がある

| エンドポイント | メソッド | 用途 | 備考 |
|--------------|---------|------|------|
| `/login` | POST | ログイン | JSON Body: `login_id`, `login_password` <br> Query: `?__env__=myapp` |
| `/services/` | GET | 施設一覧 | 施設IDを取得するために使用 |
| `/timeline/` | GET | タイムライン | `service_id`, `start_date`, `end_date` 指定 |

### タイムラインデータ構造 (`timeline_kind`)
- `activities`: 日々の様子。`photos` 配列に画像URLが含まれる。
- `topics`: お知らせ。`file_url` にPDF等のURLが含まれる。
- `responses`: 保護者からの連絡（遅刻・欠席など）。通知対象外とする。

### Slack API
- **Bot Token (App) 推奨**
  - 理由: 認証が必要なCodomonの画像をSlackサーバーにアップロードして表示させるため（Webhookでは画像URL参照のみで表示できない可能性が高い）
- `files.upload` - 画像アップロード
- `chat.postMessage` - テキスト投稿

## 6. 認証情報管理

### .env ファイル
```env
# Codomon
CODOMON_EMAIL=your-email@example.com
CODOMON_PASSWORD=your-password

# Slack
# Bot User OAuth Token (xoxb-...) を使用
SLACK_BOT_TOKEN=xoxb-xxxxxxxxxxxxx
SLACK_CHANNEL_ID=C01XXXXXXXXX
```

### セキュリティ
- `.env` は `.gitignore` に追加
- パスワードは平文保存（ローカル実行想定）
- 必要に応じて暗号化を検討

## 7. スケジューリング

### 実行タイミング（案）
- 毎日 18:00（保育園終了後）
- または 1時間ごとに新着チェック

### 実装方法
**Option A: Python schedule**
```python
import schedule
schedule.every().day.at("18:00").do(main)
```

**Option B: Windows タスクスケジューラ**
- `codmon_to_slack.py` を定時実行

**Option C: GitHub Actions**
- クラウドで定期実行（要検討）

## 8. 重複投稿防止

### 方法
- `seen_ids.txt` に投稿済み通知IDを記録（1行に1ID）
- 新規取得時にこのファイルを読み込み、セット(Set)として保持
- タイムラインのIDがセットに含まれていればスキップ
- 投稿成功後にファイルに追記

### データ構造例 (seen_ids.txt)
```text
149190852
11317384
11317637
```

## 9. エラーハンドリング

### 想定エラー
- Codomonログイン失敗 → リトライ or 通知
- API接続エラー → ログ記録
- Slack投稿失敗 → リトライ
- 画像ダウンロード失敗 → スキップして続行

### ログ出力
- `logging` モジュール使用
- ファイル出力 + コンソール表示

## 10. フェイルセーフ運用

### 背景
Codomon 側への過剰アクセスを避けるため、Codmon API のエラーやログイン失敗が続いた場合に完全停止する仕組みが必要。

### 仕様
- Slack の「最新メッセージ」をフェイルセーフの状態フラグとして扱う
- `ERROR_MARKER_PREFIX` を持つメッセージを Slack に自動投稿し、以降の実行で検知したら早期終了
- Codomon ログイン失敗・API 異常・JSON 解析失敗など「Codomon へ再アクセスしても無駄」なエラーで使用
- フェイルセーフ状態解除は手動で最新メッセージを差し替える（任意の通常メッセージを投稿）

### 流れ
1. 実行開始時に Slack 最新メッセージがエラーマーカーかチェック。該当する場合は Codomon へアクセスせず終了。
2. Codomon へのアクセス中にクリティカルエラーが発生したらエラーマーカーを Slack に投稿して即終了。
3. 利用者が Slack に通常メッセージを投稿 → 次回ジョブ実行時にフェイルセーフが解除され処理再開。

### 期待効果
- 自動ジョブが Codomon API へ無限リトライすることを防止
- 利用者が Slack 上で状態を一目で把握・解除できる

### 拡張案
- エラーマーカー投稿時に当日の Codomon API 呼び出し回数やタイムスタンプを同梱
- フェイルセーフ解除時に Slack へ再開通知を投稿

## 11. 開発ステップ

### Phase 1: プロトタイプ
- [x] Codomonログイン確認
- [x] JSON取得・パース
- [x] Slack投稿テスト

### Phase 2: 本実装
- [x] 画像ダウンロード・投稿
- [x] 重複防止機能
- [x] エラーハンドリング

### Phase 3: 自動化
- [ ] スケジューラー実装（Windowsタスクスケジューラ等）
- [ ] 長期運用テスト

## 11. TODO・未確認事項

- [x] Codomonの実際のAPIエンドポイントURL
- [x] JSONレスポンスの構造
- [x] 画像URLの取得方法
- [ ] 実行環境（ローカルPC or クラウド）

## 12. 特記事項・バッドノウハウ

### Android版Slackアプリでの表示不具合対策
- **現象**: 連続して画像を投稿した際、キャプション（テキスト）が空の場合に、直前の投稿や別の投稿のテキストが誤って表示される（表示内容が残留する）不具合がAndroid版Slackでのみ確認された。（PCブラウザ版では発生しない）
- **対策**: キャプションが空の場合、明示的に `.` (ドット) などの文字を設定することで、誤表示を防ぐ。
- **ファイル名**: ダウンロードした画像をソートしやすくするため、ファイル名の先頭に `codmon_YYYYMMDD_HHMMSS_` の形式で日時を付与する。

---

**作成日:** 2025-11-28
**更新日:** 2025-11-29 (Android版Slack不具合対策反映)
