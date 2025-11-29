# Codomon to Slack Forwarder

コドモン（Codomon）のタイムライン（写真・お知らせ）を自動で取得し、Slackに転送するツールです。
夫婦や家族間での情報共有をスムーズにするために作成されました。

## 特徴

- 📸 **写真付き投稿**: 日々の様子を写真付きでSlackに投稿します。
- 📄 **PDF対応**: お知らせ（PDF）を画像に変換して展開表示します（要 PyMuPDF）。
- ☁️ **クラウド対応**: GitHub Actions を使って完全無料で定期自動実行できます。

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
python main.py
```

**オプション:**
- `--days N`: 過去N日分を遡ってチェックします（デフォルト: 3日）。
  ```bash
  python main.py --days 7
  ```

---

## 仕様メモ

- **既読管理**: Slackの投稿履歴（直近100件）を取得し、メッセージに含まれる `(ID: xxxxx)` を確認して重複投稿を防ぎます。ファイルによる状態管理は行いません。
- **ファイル名**: ダウンロードした画像は `codmon_YYYYMMDD_HHMMSS_ID.jpg` の形式で保存・アップロードされ、名前順ソートで時系列に並びます。
- **Android対策**: Android版Slackの表示バグ（テキストの残留）回避のため、キャプションがない画像には `.` (ドット) が自動挿入されます。

## 免責事項
このツールは非公式です。Codomonの仕様変更により動作しなくなる可能性があります。
コドモンのタイムライン（写真・お知らせ）をSlackに自動転送するツール
