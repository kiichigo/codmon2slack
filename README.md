# Codmon Tools

コドモン（Codomon）のデータを活用するためのツールセットです。
- 🛡️ **フェイルセーフ**: Codmon側でエラーが発生した場合はSlackに警告を出し、自動実行を一時停止します（Slackで任意のメッセージを投稿すれば解除）。
2. **データ保存 (`codmon_archiver.py`)**: 写真・お知らせ・連絡帳をローカルに一括保存（アーカイブ）します。
---

## 🚀 Slack転送ツール (codmon_to_slack.py)

コドモン（Codomon）のタイムライン（写真・お知らせ）を自動で取得し、Slackに転送するツールです。
夫婦や家族間での情報共有をスムーズにするために作成されました。

### 特徴


## 準備

### 1. Slackアプリの作成
1. [Slack API](https://api.slack.com/apps) で新しいアプリを作成します (From scratch)。
2. **OAuth & Permissions** で以下の **Bot Token Scopes** を追加します:
   - `chat:write` (メッセージ投稿)
   - `files:write` (ファイルアップロード)
3. アプリをワークスペースにインストールし、**Bot User OAuth Token** (`xoxb-...`) を取得します。
4. 投稿したいチャンネルのIDを取得します（チャンネル名を右クリック > リンクをコピー > 末尾の `Cxxxxxx`）。
5. そのチャンネルにアプリ（Bot）を招待します (`/invite @アプリ名`)。

### 2. コドモン アカウント情報
- ログイン用メールアドレス
- パスワード

---

## 使い方 (GitHub Actions で自動実行) - 推奨

PCを起動しっぱなしにする必要がなく、無料で運用できます。

1. このリポジトリを **Fork** します（右上のボタン）。
2. Forkしたリポジトリの **Settings** > **Secrets and variables** > **Actions** を開きます。
3. **New repository secret** から以下の4つを登録します。

| Name | Value |
|---|---|
| `SLACK_BOT_TOKEN` | SlackのBotトークン (`xoxb-...`) |
| `SLACK_CHANNEL_ID` | 投稿先チャンネルID (`C...`) |
| `CODMON_EMAIL` | コドモンのログインメールアドレス |
| `CODMON_PASSWORD` | コドモンのパスワード |

4. **Actions** タブを開き、ワークフローが有効になっていることを確認します。
   - デフォルトでは **毎時 0分** に実行されます。
   - `.github/workflows/run_codomon.yml` を編集してスケジュールを変更できます。

---

## 使い方 (ローカルで実行)

1. リポジトリをクローンします。
2. `.env` ファイルを作成し、必要な環境変数を設定します。
3. 以下のコマンドで実行します。

```bash
# 仮想環境のPythonを使用してください
python codmon_to_slack.py
```

手元のPCで試したい場合や、タスクスケジューラで動かしたい場合の手順です。

### インストール

```bash
git clone https://github.com/kiichigo/codmon2slack.git
cd codmon2slack
pip install -r requirements.txt
```

### 設定

プロジェクトルートに `.env` ファイルを作成し、認証情報を記述します。

```env
SLACK_BOT_TOKEN=xoxb-your-token
SLACK_CHANNEL_ID=C012345678
CODMON_EMAIL=your-email@example.com
CODMON_PASSWORD=your-password
```

### 実行

```bash
python codmon_to_slack.py
```

**オプション:**
- `--days N`: 過去N日分を遡ってチェックします（デフォルト: 3日）。
  ```bash
  python codmon_to_slack.py --days 7
  ```
- `--test`: 接続テストモード。SlackとCodmonへのログイン確認のみ行い、投稿はしません。
  ```bash
  python codmon_to_slack.py --test
  ```

---

## 仕様メモ

- **既読管理**: Slackの投稿履歴（直近100件）を取得し、メッセージに含まれる `(ID: xxxxx)` を確認して重複投稿を防ぎます。ファイルによる状態管理は行いません。
- **ファイル名**: ダウンロードした画像は `codmon_YYYYMMDD_HHMMSS_ID.jpg` の形式で保存・アップロードされ、名前順ソートで時系列に並びます。
- **Android対策**: Android版Slackの表示バグ（テキストの残留）回避のため、キャプションがない画像には `.` (ドット) が自動挿入されます。

---

## 💾 データ保存ツール (codmon_archiver.py)

コドモンのデータをローカル（PC）に一括ダウンロードしてアーカイブ（保存）するツールです。
サービスの終了や仕様変更に備えて、大切な成長記録を手元に残すことができます。

### 保存されるデータ
- **タイムライン**: 写真、日々の記録、お知らせ（PDF含む）
- **連絡帳（園から）**: 先生からのメッセージ、食事・睡眠・機嫌などの詳細記録
- **連絡帳（保護者から）**: 遅刻・欠席連絡、お迎え変更などの送信履歴

### 使い方

```bash
# 通常実行（直近のデータと、過去2ヶ月分の連絡帳を取得）
python codmon_archiver.py

# フルスキャン（2019年からの全データを取得・確認）
python codmon_archiver.py -fs

# 期間指定（例: 2024年以降のみ）
python codmon_archiver.py --since 2024-01-01
```

### オプション
- `-fs`, `--full-scan`: 全期間をスキャンします（初回や抜け漏れ確認用）。
- `-na`, `--no-assets`: 写真やPDFをダウンロードせず、テキスト情報（JSON）のみ保存します。
- `--since YYYY-MM-DD`: 指定日以降のデータを取得します。
- `--until YYYY-MM-DD`: 指定日以前のデータを取得します。

### 保存先構造
`codomon_data/` フォルダに以下のように保存されます。

```
codomon_data/
  └─ 施設名/
      └─ YYYY/
          └─ MM/
              ├─ timeline_ID/ ... タイムライン記事
              ├─ contact_ID/ ... 園からの連絡帳
              └─ contact_response_ID/ ... 保護者からの連絡
```

---

## 免責事項
このツールは非公式です。Codomonの仕様変更により動作しなくなる可能性があります。
コドモンのタイムライン（写真・お知らせ）をSlackに自動転送するツール

---

## 更新履歴

- 2025-12-03: フェイルセーフ仕様とSlack上での解除手順の説明を追加。
- 2025-11-20: README 全体を再構成し、GitHub Actions 実行手順を追記。
- 2025-10-05: `codmon_archiver.py` のオプション一覧を整理。
