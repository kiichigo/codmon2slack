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
SEEN_IDS_FILE = "seen_ids.txt"


def load_seen_ids():
    """処理済みのIDリストを読み込む"""
    if os.path.exists(SEEN_IDS_FILE):
        try:
            with open(SEEN_IDS_FILE, "r", encoding="utf-8") as f:
                return set(line.strip() for line in f)
        except Exception as e:
            logger.error(f"IDファイル読み込みエラー: {e}")
            return set()
    return set()


def save_seen_id(item_id):
    """処理済みのIDを保存する"""
    try:
        with open(SEEN_IDS_FILE, "a", encoding="utf-8") as f:
            f.write(f"{item_id}\n")
    except Exception as e:
        logger.error(f"ID保存エラー: {e}")


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
            images.append(pix.tobytes("jpg")) # JPGとして取得
        return images
    except Exception as e:
        logger.error(f"PDF変換エラー: {e}")
        return []


def upload_file_to_slack(client, file_content, filename, title, initial_comment=None):
    """Slackにファイルをアップロードする"""
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
    """タイムラインデータを処理してSlackに投稿する"""
    if not timeline_data or 'data' not in timeline_data:
        return

    seen_ids = load_seen_ids()
    items = timeline_data['data']
    
    # 古い順に処理するために逆順にする
    for item in reversed(items):
        item_id = str(item.get('id'))
        kind = item.get('timeline_kind')
        
        if item_id in seen_ids:
            continue
            
        if kind == 'responses':
            # 欠席連絡などはスキップ
            continue
            
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
                
                # まずタイトルと本文を投稿
                main_message = f"{display_date}\n📸 *{title}*\n{overview}"
                client.chat_postMessage(channel=SLACK_CHANNEL_ID, text=main_message)
                
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
                            # 連続投稿による表示乱れを防ぐために少し待つ
                            time.sleep(1)
            
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
                
                message = f"{display_date}\n📢 *{title}*\n\n{content_text}"
                
                if file_url:
                    # 相対パスの場合は補完
                    if file_url.startswith('/'):
                        # /api/v2/parent/topics/{id}/file のようなAPIエンドポイントの場合がある
                        # この場合、API経由でファイルを取得する必要があるかもしれない
                        # しかし、通常は parents.codmon.com 配下の静的ファイルか、リダイレクトされるURL
                        full_url = f"https://parents.codmon.com{file_url}"
                    else:
                        full_url = file_url
                    
                    # API経由での取得が必要なケース（file_urlがAPIのエンドポイントっぽい場合）
                    # 例: /api/v2/parent/topics/149673803/file
                    if "/api/" in full_url:
                        # APIエンドポイントの場合は、ps-api.codmon.com を使うべきかもしれない
                        # 現在の full_url は https://parents.codmon.com/api/... となっている
                        # これを https://ps-api.codmon.com/api/... に置換してみる
                        full_url = full_url.replace("https://parents.codmon.com/api/", "https://ps-api.codmon.com/api/")
                        logger.info(f"APIエンドポイントを検出。URLを置換しました: {full_url}")
                    
                    # ユーザーから提供された情報に基づく修正:
                    # ブラウザでは https://ps-api.codmon.com/codmon/1183/topics/xxxx.pdf?PHPSESSID=... のようなURLで取得できている
                    # API (/api/v2/parent/topics/{id}/file) を叩くと、上記のような実ファイルURLへのリダイレクト(302)が返ってくる可能性がある
                    # requestsはデフォルトでリダイレクトを追跡するが、Cookie (PHPSESSID) が重要かもしれない
                    
                    # 2025-11-28 追記:
                    # parents.codmon.com ドメインのファイルURLの場合も、ps-api.codmon.com に置換してみる
                    # ログによると https://parents.codmon.com/codmon/... というURLでHTMLが返ってきている
                    if "parents.codmon.com/codmon/" in full_url:
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
                            for i, img_data in enumerate(pdf_images):
                                upload_file_to_slack(
                                    client,
                                    img_data,
                                    f"{filename}_page_{i+1}.jpg",
                                    f"{title} (ページ {i+1})",
                                    ""  # 2枚目以降はコメントなし
                                )
                else:
                    # ファイルがない場合はテキスト通知のみ
                    client.chat_postMessage(channel=SLACK_CHANNEL_ID, text=message)

            # 処理完了したらIDを保存
            save_seen_id(item_id)
            
        except Exception as e:
            logger.error(f"アイテム処理エラー {item_id}: {e}")


if __name__ == "__main__":
    # 引数解析
    parser = argparse.ArgumentParser(description='Codmon Timeline Fetcher')
    parser.add_argument('--days', type=int, default=3, help='Number of days to fetch (default: 3)')
    args = parser.parse_args()

    logger.info(f"処理を開始します (対象期間: {args.days}日間)")
    
    # 1. Slack接続テスト
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        logger.error("Slack設定不足")
        exit(1)
        
    client = WebClient(token=SLACK_BOT_TOKEN)
    
    # 2. Codmonログイン
    session = login_codmon()
    
    if session:
        # 3. 施設一覧取得
        services_data = get_services(session)
        
        if services_data:
            if isinstance(services_data, dict) and "data" in services_data:
                services_dict = services_data["data"]
                
                if isinstance(services_dict, dict):
                    for service_id, service in services_dict.items():
                        service_name = service.get("name", "不明な施設")
                        logger.info(f"施設: {service_name} のタイムラインを確認中...")
                        
                        # 4. タイムライン取得
                        timeline_data = get_timeline(session, service_id, days=args.days)
                        
                        # 5. タイムライン処理
                        process_timeline(session, client, timeline_data)
                        
                else:
                    logger.warning(f"想定外のデータ構造です: {type(services_dict)}")
            else:
                logger.warning("施設情報が見つかりませんでした")

    logger.info("処理終了")
