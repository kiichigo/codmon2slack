import os
import logging
import requests
import datetime
import fitz  # PyMuPDF
import re
import argparse
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv
import time

"""
Codmon Timeline Fetcher

コドモン（Codmon）の保護者用タイムラインから「日々の様子」や「お知らせ」を取得し、
Slackに転送・通知するスクリプト。

主な機能:
- Codmon APIへのログインとタイムライン取得
- 未読記事の抽出（Slackの投稿履歴を確認して重複排除）
- 画像およびPDFのダウンロードとSlackへのアップロード
- PDFの全ページ画像化とアップロード
- Android版Slackの表示バグ対策（ドット挿入）

Usage:
    python codmon_to_slack.py [--days 3]
"""

# ログ設定
log_filename = "app.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 環境変数読み込み
load_dotenv()

# 設定値
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID")
CODMON_EMAIL = os.getenv("CODMON_EMAIL")
CODMON_PASSWORD = os.getenv("CODMON_PASSWORD")

ERROR_MARKER_PREFIX = "⚠️ Codmon接続エラー"
RECOVERY_INSTRUCTION = "Slackに任意のメッセージを投稿すると自動実行が再開します。"


def is_error_marker_message(text):
    """Slackメッセージがフェイルセーフ用のエラーマーカーか判定"""
    if not text:
        return False
    return text.strip().startswith(ERROR_MARKER_PREFIX)


def post_slack_error_marker(client, detail):
    """Slackにエラーマーカーを投稿し、ユーザーに復旧操作を促す"""
    if not client:
        return

    message = f"{ERROR_MARKER_PREFIX} {detail}\n{RECOVERY_INSTRUCTION}"
    try:
        client.chat_postMessage(channel=SLACK_CHANNEL_ID, text=message)
        logger.warning("Slackにエラーマーカーを投稿しました。ユーザー操作待ちで停止します。")
    except SlackApiError as e:
        logger.error(f"Slackアラート投稿失敗: {e.response['error']}")


def slack_is_in_error_state(client):
    """Slackの最新メッセージがエラーマーカーかどうかを確認"""
    try:
        response = client.conversations_history(channel=SLACK_CHANNEL_ID, limit=1)
        if not response.get('ok'):
            logger.error(f"Slack履歴確認失敗: {response.get('error')}")
            return False
        messages = response.get('messages', [])
        if not messages:
            return False
        latest_text = messages[0].get('text', '')
        if is_error_marker_message(latest_text):
            logger.warning("Slack最新投稿がエラーマーカーのため、Codmonへの接続をスキップします。")
            return True
        return False
    except SlackApiError as e:
        logger.error(f"Slack APIエラー (エラーステータス確認): {e.response['error']}")
        return False
    except Exception as e:
        logger.error(f"Slackエラーステータス確認エラー: {e}")
        return False


def fetch_seen_ids_from_slack(client):
    """
    Slackの履歴から処理済みのIDリストを取得する。
    
    重複投稿を防ぐため、Slackチャンネルの直近の投稿を確認し、
    既に投稿されている記事のIDを収集する。
    
    ロジック:
    1. 指定チャンネルの直近100件のメッセージを取得 (conversations_history)
    2. 各メッセージの本文(text)およびファイルコメント(initial_comment)を検査
    3. 正規表現 r'\\(ID:\\s*(\\d+)\\)' にマッチするIDを抽出
    
    Returns:
        set: 既読（投稿済み）の記事IDの集合
    """
    seen_ids = set()
    try:
        # 直近100件のメッセージを取得
        response = client.conversations_history(channel=SLACK_CHANNEL_ID, limit=100)
        if not response['ok']:
            logger.error(f"Slack履歴取得失敗: {response['error']}")
            return seen_ids

        messages = response.get('messages', [])
        has_more = response.get('has_more', None)
        next_cursor = response.get('response_metadata', {}).get('next_cursor', '')
        logger.info(
            f"Slack履歴取得: {len(messages)}件 (limit=100, has_more={has_more}, next_cursor={'あり' if next_cursor else 'なし'})"
        )
        # メッセージ内の (ID: xxxxx) を検索
        pattern = re.compile(r'\(ID:\s*(\d+)\)')
        
        for msg in messages:
            text = msg.get('text', '')
            # テキスト内のIDを探す
            match = pattern.search(text)
            if match:
                seen_ids.add(match.group(1))
            
            # ファイルのコメント（initial_comment）もチェック
            if 'files' in msg:
                for file in msg['files']:
                    if 'initial_comment' in file:
                        comment = file['initial_comment'].get('comment', '')
                        match = pattern.search(comment)
                        if match:
                            seen_ids.add(match.group(1))

        logger.info(f"Slackから取得した既読ID数: {len(seen_ids)}")
        return seen_ids

    except SlackApiError as e:
        logger.error(f"Slack APIエラー: {e.response['error']}")
        return seen_ids
    except Exception as e:
        logger.error(f"既読ID取得エラー: {e}")
        return seen_ids


def download_content(session, url):
    """コンテンツ（画像・PDF）をダウンロードする"""
    try:
        # allow_redirects=True はデフォルトだが明示的に指定
        response = session.get(url, stream=True, allow_redirects=True)
        
        # リダイレクトされた場合の最終URLをログに出す
        if response.history:
            logger.info(f"リダイレクトされました: {url} -> {response.url}")

        if response.status_code == 200:
            content_type = response.headers.get('Content-Type', '')
            logger.info(f"ダウンロード成功: {response.url} (Size: {len(response.content)} bytes, Type: {content_type})")
            return response.content
        else:
            logger.error(f"ダウンロード失敗: {response.status_code} {url}")
            return None
    except Exception as e:
        logger.error(f"ダウンロードエラー: {e} {url}")
        return None


def convert_pdf_to_images(pdf_content):
    """PDFバイナリから画像を抽出（レンダリング）してリストで返す"""
    images = []
    try:
        doc = fitz.open(stream=pdf_content, filetype="pdf")
        for i, page in enumerate(doc):
            # 解像度を指定 (zoom=2くらいが適当。72dpi * 2 = 144dpi)
            # alpha=Falseを指定して背景を白にする（透過対策）
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            images.append(pix.tobytes("jpg"))  # JPGとして取得
        return images
    except Exception as e:
        logger.error(f"PDF変換エラー: {e}")
        return []


def upload_file_to_slack(client, file_content, filename, title, initial_comment=None):
    """
    Slackにファイルをアップロードする。
    
    files_upload_v2 メソッドを使用してファイルをアップロードする。
    
    Args:
        client (WebClient): Slack WebClient
        file_content (bytes): ファイルのバイナリデータ
        filename (str): Slack上でのファイル名
        title (str): Slack上でのファイルタイトル
        initial_comment (str, optional): ファイルと一緒に投稿するコメント。
                                       Android版Slackのバグ対策として `.` を推奨。
    
    Returns:
        bool: アップロード成功ならTrue
    """
    try:
        # files_upload_v2 は initial_comment で mrkdwn が効かない場合があるため
        # 明示的にテキストメッセージとして送るか、Block Kitを使うのが確実だが
        # ここでは簡易的に files_upload_v2 を使い続ける。
        
        # パラメータを構築
        upload_params = {
            "channel": SLACK_CHANNEL_ID,
            "file": file_content,
            "filename": filename,
            "title": title
        }
        
        # コメントがある場合のみ追加（空文字やNoneの場合は送らない）
        if initial_comment:
            upload_params["initial_comment"] = initial_comment
            
        client.files_upload_v2(**upload_params)
        return True
    except SlackApiError as e:
        logger.error(f"Slackアップロード失敗: {e.response['error']}")
        return False


def test_slack_connection():
    """Slackへの接続テスト"""
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        logger.error("Slackの設定が不足しています。.envを確認してください。")
        return False

    client = WebClient(token=SLACK_BOT_TOKEN)
    try:
        response = client.chat_postMessage(
            channel=SLACK_CHANNEL_ID,
            text="🤖 Codmon通知ボットのテスト投稿です。接続成功！"
        )
        logger.info(f"Slack投稿成功: {response['ts']}")
        return True
    except SlackApiError as e:
        logger.error(f"Slack投稿失敗: {e.response['error']}")
        return False


def login_codmon():
    """Codmonへのログイン試行"""
    if not CODMON_EMAIL or not CODMON_PASSWORD:
        logger.error("Codmonのログイン情報が設定されていません。.envを確認してください。")
        return None

    session = requests.Session()
    
    # 共通ヘッダーをセッションに設定 (すべてのリクエストで有効にする)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Origin": "https://parents.codmon.com",
        "Referer": "https://parents.codmon.com/",
    })

    # API設定
    base_url = "https://ps-api.codmon.com/api/v2/parent"
    login_url = f"{base_url}/login"
    
    # クエリパラメータ
    params = {"__env__": "myapp"}

    # JSONペイロード
    payload = {
        "login_id": CODMON_EMAIL,
        "login_password": CODMON_PASSWORD,
        "use_db_replica": 1
    }

    # ログイン時専用のヘッダー
    login_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=UTF-8"
    }

    try:
        logger.info("Codmonにログインを試みています...")
        
        # JSONとして送信
        response = session.post(login_url, json=payload, params=params, headers=login_headers)
        
        if response.status_code == 200:
            logger.info("Codmonログイン成功！")
            # logger.info(f"Response Cookies: {session.cookies.get_dict()}")
            return session
        else:
            logger.error(f"Codmonログイン失敗: Status Code {response.status_code}")
            logger.error(f"Response: {response.text[:200]}")
            return None

    except Exception as e:
        logger.error(f"ログインエラー: {e}")
        return None


def get_services(session):
    """施設一覧を取得"""
    url = "https://ps-api.codmon.com/api/v2/parent/services/"
    params = {
        "use_image_edge": "true",
        "__env__": "myapp"
    }
    
    try:
        response = session.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            # logger.info(f"Services Response: {data}")
            return data
        else:
            logger.error(f"施設一覧取得失敗: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"施設一覧取得エラー: {e}")
        return None


def get_timeline(session, service_id, days=3):
    """タイムラインを取得"""
    url = "https://ps-api.codmon.com/api/v2/parent/timeline/"
    
    # 指定された日数分を取得
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days)
    
    params = {
        "listpage": 1,
        "search_type[]": "new_all",
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "service_id": service_id,
        "current_flag": 0,
        "use_image_edge": "true",
        "bookmark_only": "false",
        "__env__": "myapp"
    }
    
    try:
        response = session.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            return data
        else:
            logger.error(f"タイムライン取得失敗: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"タイムライン取得エラー: {e}")
        return None


def remove_html_tags(text):
    """HTMLタグをSlack用mrkdwn形式に変換しつつ除去する"""
    if not text:
        return ""
    
    # 1. 改行系
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = text.replace("</p>", "\n").replace("</div>", "\n")
    
    # 2. 装飾系 (Slack mrkdwn)
    # 太字
    text = re.sub(r'<b>(.*?)</b>', r'*\1*', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<strong>(.*?)</strong>', r'*\1*', text, flags=re.IGNORECASE | re.DOTALL)
    # 斜体 (<u>はSlackにないので斜体で代用)
    text = re.sub(r'<i>(.*?)</i>', r'_\1_', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<em>(.*?)</em>', r'_\1_', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<u>(.*?)</u>', r'_\1_', text, flags=re.IGNORECASE | re.DOTALL)
    # 取り消し線
    text = re.sub(r'<s>(.*?)</s>', r'~\1~', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<strike>(.*?)</strike>', r'~\1~', text, flags=re.IGNORECASE | re.DOTALL)
    
    # 3. リスト
    text = text.replace("<li>", "• ")
    
    # 4. 残りのタグを除去
    clean = re.compile('<.*?>')
    text = re.sub(clean, '', text)
    
    # 5. 連続する改行を整理
    text = re.sub(r'\n\s*\n', '\n\n', text)
    
    return text.strip()


def process_timeline(session, client, timeline_data):
    """
    タイムラインデータを処理してSlackに投稿する。
    
    取得したタイムラインデータ（JSON）を解析し、未読のアイテムをSlackに投稿する。
    
    ロジック:
    1. Slackから既読IDリストを取得し、重複を排除
    2. タイムラインを古い順（reversed）に処理
    3. 投稿タイプ（kind）に応じて処理を分岐
       - activities: 日々の様子（写真付き）。写真は1枚ずつアップロード。
       - topics: お知らせ（PDFなど）。PDFは画像化して全ページアップロード。
    4. ファイル名には日時プレフィックスを付与してソート可能にする
    5. Android版Slack対策として、キャプションがない画像には `.` を付与
    
    Args:
        session (requests.Session): Codmonログイン済みセッション
        client (WebClient): Slack WebClient
        timeline_data (dict): Codmonから取得したタイムラインデータ
    """
    if not timeline_data or 'data' not in timeline_data:
        return

    # Slackから既読IDを取得
    seen_ids = fetch_seen_ids_from_slack(client)
    items = timeline_data['data']

    # Codmon側のタイムラインは「新しい順」で並ぶ前提なので、
    # 既読IDに当たるまで新規アイテムを収集し、最後に古い順へ反転して投稿する。
    items_to_post = []

    for item in items:
        item_id = str(item.get('id'))
        kind = item.get('timeline_kind')
        
        if item_id in seen_ids:
            logger.info(f"既読ID {item_id} に到達したため残りはスキップします。")
            break
            
        if kind == 'responses':
            # 欠席連絡などはスキップ
            continue

        items_to_post.append(item)

    # 収集した未読アイテムを古い順で処理
    for item in reversed(items_to_post):
        item_id = str(item.get('id'))
        kind = item.get('timeline_kind')
            
        logger.info(f"新規アイテム処理中: {item.get('title')} ({kind})")
        
        try:
            if kind == 'activities':
                # 日々の様子（写真あり）
                title = item.get('title', '無題')
                overview = item.get('overview', '')
                photos = item.get('photos', [])
                display_date = item.get('display_date', '')
                delivery_date = item.get('delivery_start_datetime', '')
                
                # ファイル名用の日時プレフィックスを作成
                file_date_prefix = ""
                if delivery_date:
                    # 2025-11-25 18:15:38 -> 20251125_181538
                    clean_date = re.sub(r'[^\d]', '', delivery_date)
                    if len(clean_date) >= 14:
                        file_date_prefix = f"{clean_date[:8]}_{clean_date[8:14]}_"
                    else:
                        file_date_prefix = f"{clean_date}_"
                
                # まずタイトルと本文を投稿 (IDを埋め込む)
                # 縦の長さを節約するため、日付の後ろにIDを表示
                main_message = f"{display_date} (ID: {item_id})\n📸 *{title}*\n{overview}"
                client.chat_postMessage(channel=SLACK_CHANNEL_ID, text=main_message)

                posted_any_photo = False
                
                for i, photo in enumerate(photos):
                    photo_url = photo.get('url')
                    # キャプションを取得
                    caption = photo.get('caption')
                    # Android版Slackでキャプションが空の場合に別の投稿のテキストが表示されるバグ対策
                    # 何らかの文字を入れることでキャッシュ表示を防ぐ
                    if not caption:
                        caption = "."

                    # 写真IDを取得（なければ連番）
                    photo_id = photo.get('id', str(i))
                    
                    if photo_url:
                        content = download_content(session, photo_url)
                        if content:
                            # ファイル名を生成 (codmon_YYYYMMDD_HHMMSS_記事ID_写真ID.jpg)
                            # 日本語タイトルを避け、ソート可能な形式にする
                            safe_filename = f"codmon_{file_date_prefix}{item_id}_{photo_id}.jpg"
                            
                            upload_file_to_slack(
                                client,
                                content,
                                safe_filename,
                                safe_filename,
                                caption
                            )
                            posted_any_photo = True
                            # 連続投稿による表示乱れを防ぐために少し待つ
                            time.sleep(1)

                # 画像が複数投稿に分かれるため、最後にマーカー専用投稿を追加する
                # Slack履歴取得がlimit未満でも直近でID検出できるようにする
                if posted_any_photo:
                    client.chat_postMessage(channel=SLACK_CHANNEL_ID, text=f"(ID: {item_id})")
            
            elif kind == 'topics':
                # お知らせ（PDFなど）
                title = item.get('title', '無題')
                content_html = item.get('content', '')
                display_date = item.get('display_date', '')
                
                # HTMLタグを除去して本文を抽出
                content_text = remove_html_tags(content_html)
                
                file_url = item.get('file_url')
                
                # Slackのmrkdwnを有効にするためにブロックキットを使うか、
                # 単純にテキストを送る場合はmrkdwn=Trueが必要（デフォルトでTrueだが念のため）
                # ただし、upload_file_to_slackのinitial_commentはmrkdwnが効くはず
                
                # IDを埋め込む
                # 縦の長さを節約するため、日付の後ろにIDを表示
                message = f"{display_date} (ID: {item_id})\n📢 *{title}*\n\n{content_text}"
                
                if file_url:
                    # 相対パスの場合は補完
                    if file_url.startswith('/'):
                        # APIエンドポイント(/api/...)も静的ファイル(/codmon/...)も
                        # ps-api.codmon.com ドメインで取得する方が確実
                        full_url = f"https://ps-api.codmon.com{file_url}"
                    else:
                        full_url = file_url
                        # 絶対パスの場合でも parents.codmon.com が含まれていたら ps-api に置換する
                        if "parents.codmon.com" in full_url:
                            full_url = full_url.replace("parents.codmon.com", "ps-api.codmon.com")
                            logger.info(f"parentsドメインをps-apiドメインに置換しました: {full_url}")

                    content = download_content(session, full_url)
                    if content:
                        filename = os.path.basename(file_url)
                        
                        upload_file_to_slack(
                            client,
                            content,
                            filename,
                            title,
                            message
                        )

                        # PDFなら展開して画像もアップロード
                        if filename.lower().endswith('.pdf'):
                            logger.info(f"PDFを展開して画像を抽出中: {filename}")
                            pdf_images = convert_pdf_to_images(content)
                            posted_any_page = False
                            for i, img_data in enumerate(pdf_images):
                                ok = upload_file_to_slack(
                                    client,
                                    img_data,
                                    f"{filename}_page_{i+1}.jpg",
                                    f"{title} (ページ {i+1})",
                                    "."  # Android対策でドットを入れる
                                )
                                posted_any_page = posted_any_page or ok

                            # ページ画像はドット投稿が続くため、最後にマーカー専用投稿を追加する
                            if posted_any_page:
                                client.chat_postMessage(channel=SLACK_CHANNEL_ID, text=f"(ID: {item_id})")
                else:
                    # ファイルがない場合はテキスト通知のみ
                    client.chat_postMessage(channel=SLACK_CHANNEL_ID, text=message)

            # 処理完了したらIDを保存 (Slack投稿自体が保存になるのでファイル書き込みは不要)
            # save_seen_id(item_id)
            
        except Exception as e:
            logger.error(f"アイテム処理エラー {item_id}: {e}")


if __name__ == "__main__":
    # 引数解析
    parser = argparse.ArgumentParser(description='Codmon Timeline Fetcher')
    parser.add_argument('--days', type=int, default=3, help='Number of days to fetch (default: 3)')
    parser.add_argument('--test', action='store_true', help='Test connection settings only (no post)')
    args = parser.parse_args()

    if args.test:
        logger.info("接続テストモード: 設定の確認を行います（投稿は行いません）")
        
        # 1. Slack接続確認 (auth.test)
        if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
            logger.error("❌ Slack設定不足: .envを確認してください")
            exit(1)
            
        try:
            client = WebClient(token=SLACK_BOT_TOKEN)
            auth_res = client.auth_test()
            logger.info(f"✅ Slack接続 OK (Bot User: {auth_res['user']})")
        except SlackApiError as e:
            logger.error(f"❌ Slack接続 NG: {e.response['error']}")
            exit(1)

        # 2. Codmonログイン確認
        session = login_codmon()
        if session:
            logger.info("✅ Codmonログイン OK")
            
            # 3. 施設一覧取得確認
            services_data = get_services(session)
            if services_data and "data" in services_data:
                count = len(services_data["data"]) if isinstance(services_data["data"], dict) else 0
                logger.info(f"✅ 施設一覧取得 OK ({count}件の施設を検出)")
            else:
                logger.warning("⚠️ 施設一覧取得 NG またはデータなし")
        else:
            logger.error("❌ Codmonログイン NG")
            exit(1)
            
        logger.info("🎉 設定確認完了: 正常に接続できています")
        exit(0)

    logger.info(f"処理を開始します (対象期間: {args.days}日間)")
    
    # 1. Slack接続テスト
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        logger.error("Slack設定不足")
        exit(1)
        
    client = WebClient(token=SLACK_BOT_TOKEN)

    # Slack側でエラー状態が続いている場合はCodmonへ接続しない
    if slack_is_in_error_state(client):
        logger.warning("直前のSlack投稿がエラーマーカーだったため、処理を中断します。Slackに任意のメッセージを投稿して解除してください。")
        exit(1)
    
    # 2. Codmonログイン
    session = login_codmon()
    if not session:
        post_slack_error_marker(client, "Codmonへのログインに失敗しました。API仕様変更やメンテナンスの可能性があります。")
        exit(1)
    
    # 3. 施設一覧取得
    services_data = get_services(session)
    if not services_data:
        post_slack_error_marker(client, "施設一覧の取得に失敗しました。Codmon APIの応答を確認してください。")
        exit(1)

    if isinstance(services_data, dict) and "data" in services_data:
        services_dict = services_data["data"]
        
        if isinstance(services_dict, dict):
            for service_id, service in services_dict.items():
                service_name = service.get("name", "不明な施設")
                logger.info(f"施設: {service_name} のタイムラインを確認中...")
                
                # 4. タイムライン取得
                timeline_data = get_timeline(session, service_id, days=args.days)
                if not timeline_data:
                    post_slack_error_marker(client, f"施設『{service_name}』のタイムライン取得に失敗しました。")
                    exit(1)
                
                # 5. タイムライン処理
                process_timeline(session, client, timeline_data)
                
        else:
            logger.warning(f"想定外のデータ構造です: {type(services_dict)}")
    else:
        logger.warning("施設情報が見つかりませんでした")

    logger.info("処理終了")
