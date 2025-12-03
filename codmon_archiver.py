import os
import logging
import requests
import datetime
import json
import time
import re
import argparse
from dotenv import load_dotenv
from pathlib import Path

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 環境変数読み込み
load_dotenv()

CODMON_EMAIL = os.getenv("CODMON_EMAIL")
CODMON_PASSWORD = os.getenv("CODMON_PASSWORD")
DATA_DIR = "codomon_data"

def parse_args():
    parser = argparse.ArgumentParser(description='Codmon Archiver')
    parser.add_argument('-fs', '--full-scan', action='store_true', help='既存データがあっても停止せず、全期間をスキャンします。連絡帳も全期間（2019年〜）取得します。指定しない場合、連絡帳は直近2ヶ月のみ取得します。')
    parser.add_argument('-na', '--no-assets', action='store_true', help='添付ファイル（写真・PDF）をダウンロードしません')
    parser.add_argument('-f', '--force', action='store_true', help='既存のファイルを上書きして再取得します')
    parser.add_argument('--since', help='取得対象の最も古い日付 (YYYY-MM-DD)')
    parser.add_argument('--until', help='取得対象の最も新しい日付 (YYYY-MM-DD) = さかのぼり開始日')
    parser.add_argument('--debug', action='store_true', help='詳細なデバッグログを出力します')
    return parser.parse_args()

def login_codmon():
    """Codmonへのログイン試行"""
    if not CODMON_EMAIL or not CODMON_PASSWORD:
        logger.error("Codmonのログイン情報が設定されていません。.envを確認してください。")
        return None, None

    session = requests.Session()
    
    # 共通ヘッダーをセッションに設定
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
        response = session.post(login_url, json=payload, params=params, headers=login_headers)
        
        if response.status_code == 200:
            logger.info("Codmonログイン成功！")
            return session, response.json()
        else:
            logger.error(f"Codmonログイン失敗: Status Code {response.status_code}")
            logger.error(f"Response: {response.text[:200]}")
            return None, None

    except Exception as e:
        logger.error(f"ログインエラー: {e}")
        return None, None

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
            return data
        else:
            logger.error(f"施設一覧取得失敗: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"施設一覧取得エラー: {e}")
        return None

def get_member_ids_by_service(login_data):
    """ログインデータから施設ごとの子供ID (member_id) を抽出"""
    mapping = {} # { service_id: [ {member_id, child_name}, ... ] }
    
    try:
        if not login_data or 'data' not in login_data:
            return mapping
            
        families = login_data['data'].get('families', {})
        for family_id, family_data in families.items():
            children = family_data.get('children', [])
            for child in children:
                child_name = child.get('name', 'Unknown')
                services = child.get('services', [])
                for service in services:
                    service_id = service.get('service_id')
                    member_id = service.get('member_id')
                    
                    if service_id and member_id:
                        if service_id not in mapping:
                            mapping[service_id] = []
                        
                        mapping[service_id].append({
                            'member_id': member_id,
                            'child_name': child_name
                        })
    except Exception as e:
        logger.error(f"子供情報抽出エラー: {e}")
        
    return mapping

def get_comments(session, member_id, start_date, end_date):
    """連絡帳データを取得"""
    url = "https://ps-api.codmon.com/api/v2/parent/comments/"
    
    params = {
        "search_kind": "2",
        "relation_id": member_id,
        "relation_kind": "2",
        "search_start_display_date": start_date.strftime("%Y-%m-%d"),
        "search_end_display_date": end_date.strftime("%Y-%m-%d"),
        "__env__": "myapp"
    }
    
    try:
        response = session.get(url, params=params)
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"連絡帳取得失敗 ({start_date} - {end_date}): {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"連絡帳取得エラー: {e}")
        return None

def get_contact_responses(session, member_id, start_date, end_date):
    """保護者からの連絡を取得"""
    url = "https://ps-api.codmon.com/api/v2/parent/contact_responses/"
    
    params = {
        "member_id": member_id,
        "search_start_display_date": start_date.strftime("%Y-%m-%d"),
        "search_end_display_date": end_date.strftime("%Y-%m-%d"),
        "search_status_id[]": [1, 2, 3],
        "perpage": 1000,
        "use_db_replica": 1,
        "__env__": "myapp"
    }
    
    try:
        response = session.get(url, params=params)
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"保護者連絡取得失敗 ({start_date} - {end_date}): {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"保護者連絡取得エラー: {e}")
        return None

def get_timeline_page(session, service_id, page=1, start_date=None, end_date=None):
    """タイムラインの特定ページを取得"""
    url = "https://ps-api.codmon.com/api/v2/parent/timeline/"
    
    # デフォルト設定
    if not end_date:
        end_date = datetime.date.today()
    
    if not start_date:
        # 指定がない場合は10年前から
        start_date = end_date - datetime.timedelta(days=365*10)
    
    params = {
        "listpage": page,
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
            return response.json()
        else:
            logger.error(f"タイムライン取得失敗 (Page {page}): {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"タイムライン取得エラー (Page {page}): {e}")
        return None

def download_file(session, url, save_path, force=False):
    """ファイルをダウンロードして保存"""
    try:
        if not force and os.path.exists(save_path):
            # ファイルサイズが0より大きいか確認
            if os.path.getsize(save_path) > 0:
                logger.debug(f"ファイルは既に存在します（スキップ）: {save_path}")
                return True
        
        # URLが相対パスの場合は補完
        if not url.startswith("http"):
            full_url = f"https://parents.codmon.com{url}"
        else:
            full_url = url

        response = session.get(full_url, stream=True)
        if response.status_code == 200:
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(f"ダウンロード完了: {save_path}")
            return True
        else:
            logger.error(f"ダウンロード失敗: {response.status_code} {full_url}")
            return False
    except Exception as e:
        logger.error(f"ダウンロードエラー: {e} {url}")
        return False

def sanitize_filename(name):
    """ファイル名に使えない文字を置換"""
    return re.sub(r'[\\/*?:"<>|]', "", name)

def process_contact_book(session, member_id, child_name, service_name, args):
    """連絡帳の取得処理 (園から & 保護者から)"""
    logger.info(f"連絡帳処理開始: {child_name} ({service_name})")
    
    # 日付範囲の設定
    end_date = datetime.date.today()
    
    # デフォルトの開始日決定ロジック
    if args.full_scan:
        # フルスキャンの場合は2019年から
        start_date = datetime.date(2019, 1, 1)
    else:
        # 通常は直近2ヶ月（60日）のみチェック
        start_date = end_date - datetime.timedelta(days=60)
    
    if args.since:
        try:
            start_date = datetime.datetime.strptime(args.since, '%Y-%m-%d').date()
        except ValueError:
            pass # 既にチェック済みのはず

    if args.until:
        try:
            end_date = datetime.datetime.strptime(args.until, '%Y-%m-%d').date()
        except ValueError:
            pass

    logger.info(f"連絡帳取得対象期間: {start_date} ～ {end_date}")

    # 月単位でループ
    current_start = start_date.replace(day=1)
    
    while current_start <= end_date:
        # 月末を計算
        next_month = current_start.replace(day=28) + datetime.timedelta(days=4)
        current_end = next_month - datetime.timedelta(days=next_month.day)
        
        # 指定終了日を超えないように調整
        if current_end > end_date:
            current_end = end_date
            
        logger.info(f"連絡帳取得中: {current_start} - {current_end}")
        
        # 1. 園からの連絡 (comments)
        data_comments = get_comments(session, member_id, current_start, current_end)
        
        if data_comments and 'data' in data_comments:
            items = data_comments['data']
            if items:
                logger.info(f"  園からの連絡: {len(items)}件")
                for item in items:
                    # 日付取得
                    display_date = item.get('display_date')
                    if not display_date:
                        continue
                        
                    try:
                        dt = datetime.datetime.strptime(display_date, '%Y-%m-%d')
                        year = dt.strftime('%Y')
                        month = dt.strftime('%m')
                    except ValueError:
                        year = "unknown"
                        month = "unknown"
                    
                    # 保存先: data/施設名/年/月/contact_ID/
                    item_id = item.get('id')
                    dir_name = f"contact_{item_id}"
                    item_dir = Path(DATA_DIR) / service_name / year / month / dir_name
                    
                    # 既に存在するかチェック
                    if item_dir.exists() and (item_dir / "done").exists():
                        if not args.full_scan and not args.force:
                            continue
                    
                    item_dir.mkdir(parents=True, exist_ok=True)
                    
                    # contentのJSONパース
                    if 'content' in item and isinstance(item['content'], str):
                        try:
                            item['content_parsed'] = json.loads(item['content'])
                        except json.JSONDecodeError:
                            logger.warning(f"Content JSON decode error: {item_id}")
                    
                    # JSON保存
                    with open(item_dir / "info.json", "w", encoding="utf-8") as f:
                        json.dump(item, f, ensure_ascii=False, indent=4)
                        
                    # 完了マーカー
                    (item_dir / "done").touch()
            else:
                logger.debug("  園からの連絡なし")

        # 2. 保護者からの連絡 (contact_responses)
        data_responses = get_contact_responses(session, member_id, current_start, current_end)
        
        if data_responses and 'data' in data_responses:
            items = data_responses['data']
            if items:
                logger.info(f"  保護者からの連絡: {len(items)}件")
                for item in items:
                    # 日付取得
                    display_date = item.get('display_date')
                    if not display_date:
                        continue
                        
                    try:
                        dt = datetime.datetime.strptime(display_date, '%Y-%m-%d')
                        year = dt.strftime('%Y')
                        month = dt.strftime('%m')
                    except ValueError:
                        year = "unknown"
                        month = "unknown"
                    
                    # 保存先: data/施設名/年/月/contact_response_ID/
                    item_id = item.get('id')
                    dir_name = f"contact_response_{item_id}"
                    item_dir = Path(DATA_DIR) / service_name / year / month / dir_name
                    
                    # 既に存在するかチェック
                    if item_dir.exists() and (item_dir / "done").exists():
                        if not args.full_scan and not args.force:
                            continue
                    
                    item_dir.mkdir(parents=True, exist_ok=True)
                    
                    # JSON保存
                    with open(item_dir / "info.json", "w", encoding="utf-8") as f:
                        json.dump(item, f, ensure_ascii=False, indent=4)
                        
                    # 完了マーカー
                    (item_dir / "done").touch()
            else:
                logger.debug("  保護者からの連絡なし")
        
        # 次の月へ
        current_start = (current_start.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        time.sleep(1) # API負荷軽減

def process_service(session, service, args, member_mapping=None):
    service_id = service['id']
    service_name = sanitize_filename(service['name'])
    logger.info(f"施設処理開始: {service_name} (ID: {service_id})")
    
    # 連絡帳の取得 (member_mappingがある場合)
    if member_mapping and service_id in member_mapping:
        members = member_mapping[service_id]
        for member in members:
            process_contact_book(session, member['member_id'], member['child_name'], service_name, args)
    
    # 日付指定のパース
    since_date = None
    until_date = None
    
    if args.since:
        try:
            since_date = datetime.datetime.strptime(args.since, '%Y-%m-%d').date()
            logger.info(f"取得開始日(最古): {since_date}")
        except ValueError:
            logger.error("日付形式が正しくありません (--since)。YYYY-MM-DD で指定してください。")
            return

    if args.until:
        try:
            until_date = datetime.datetime.strptime(args.until, '%Y-%m-%d').date()
            logger.info(f"取得終了日(最新): {until_date}")
        except ValueError:
            logger.error("日付形式が正しくありません (--until)。YYYY-MM-DD で指定してください。")
            return

    page = 1
    has_next = True
    MAX_PAGES = 3000 # 安全装置：最大ページ数
    seen_ids_in_session = set() # 安全装置：今回のセッションで見たID
    
    while has_next:
        if page > MAX_PAGES:
            logger.warning(f"最大ページ数({MAX_PAGES})に到達しました。無限ループ防止のため強制終了します。")
            break

        logger.info(f"ページ取得中: {page}")
        data = get_timeline_page(session, service_id, page, start_date=since_date, end_date=until_date)
        
        if not data or 'data' not in data or not data['data']:
            logger.info("データがありません。終了します。")
            break
            
        items = data['data']
        
        if items:
            current_date = items[0].get('display_date', 'Unknown')
            logger.info(f"取得アイテム数: {len(items)} (現在処理中の日付: {current_date} 付近)")
        else:
            logger.info(f"取得アイテム数: {len(items)}")
        
        # 安全装置：このページに含まれる新規ID（今回の実行で初めて見るID）の数
        new_ids_count = 0

        for item in items:
            item_id = str(item['id'])
            
            # ループ検知
            if item_id not in seen_ids_in_session:
                seen_ids_in_session.add(item_id)
                new_ids_count += 1
            
            # 日付取得ロジック（display_dateがない場合のフォールバックを追加）
            display_date = item.get('display_date')
            if not display_date:
                if item.get('start_date'):
                    display_date = item['start_date'] # billsなどはこれ
                elif item.get('delivery_start_datetime'):
                    display_date = item['delivery_start_datetime'].split(' ')[0]
                elif item.get('update_datetime'):
                    display_date = item['update_datetime'].split(' ')[0]
                elif item.get('confirm_datetime'):
                    display_date = item['confirm_datetime'].split(' ')[0]
                else:
                    display_date = 'unknown_date'
            
            # 日付パース (YYYY-MM-DD または YYYY年MM月DD日)
            try:
                if '年' in display_date:
                    dt = datetime.datetime.strptime(display_date, '%Y年%m月%d日')
                else:
                    # 時間が含まれている場合も考慮して日付部分のみ抽出
                    clean_date = display_date.split(' ')[0]
                    dt = datetime.datetime.strptime(clean_date, '%Y-%m-%d')
                year = dt.strftime('%Y')
                month = dt.strftime('%m')
            except ValueError:
                year = "unknown"
                month = "unknown"
            
            # 保存先ディレクトリ: data/施設名/年/月/kind_ID/
            timeline_kind = sanitize_filename(item.get('timeline_kind', 'etc'))
            dir_name = f"{timeline_kind}_{item_id}"
            item_dir = Path(DATA_DIR) / service_name / year / month / dir_name
            
            # 既に存在するかチェック
            is_existing = item_dir.exists() and (item_dir / "done").exists()

            if is_existing:
                if not args.full_scan and not args.force:
                    logger.info(f"取得済みデータに到達しました: {item_id} ({display_date})")
                    logger.info("通常モードのため、この施設の処理を終了します。")
                    return 
                else:
                    # フルスキャンまたは強制モードの場合はログを出して続行
                    logger.debug(f"既存データを確認（続行）: {item_id}")
            
            # ディレクトリ作成
            item_dir.mkdir(parents=True, exist_ok=True)
            
            # JSON保存 (強制または存在しない場合)
            if args.force or not (item_dir / "info.json").exists():
                with open(item_dir / "info.json", "w", encoding="utf-8") as f:
                    json.dump(item, f, ensure_ascii=False, indent=4)
            
            # 添付ファイル処理 (no_assetsでない場合)
            if not args.no_assets:
                # 添付ファイル (PDFなど)
                if item.get('file_url'):
                    file_url = item['file_url']
                    ext = os.path.splitext(file_url)[1]
                    if not ext:
                        ext = ".pdf" # デフォルト
                    filename = f"attachment{ext}"
                    download_file(session, file_url, item_dir / filename, force=args.force)
                
                # 写真
                if item.get('photos'):
                    for photo in item['photos']:
                        photo_url = photo['url']
                        photo_id = photo['id']
                        # URLから拡張子を取得、なければjpg
                        if '.png' in photo_url:
                            ext = '.png'
                        else:
                            ext = '.jpg'
                        
                        filename = f"photo_{photo_id}{ext}"
                        download_file(session, photo_url, item_dir / filename, force=args.force)
            
            # 完了マーカー作成
            (item_dir / "done").touch()
            
            if not is_existing:
                logger.info(f"保存完了: {item_id} - {item.get('title', 'No Title')}")
        
        # 安全装置：もし取得したアイテムが全て「今回のセッションですでに見たID」なら、
        # ページが進んでいない（同じデータを取得し続けている）とみなして終了
        if new_ids_count == 0 and len(items) > 0:
            logger.warning("取得したデータが全て重複しています（APIループの可能性があります）。強制終了します。")
            break

        page += 1
        time.sleep(1) # API負荷軽減

def main():
    args = parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("デバッグモードで開始します")

    session, login_data = login_codmon()
    if not session:
        return

    # 子供情報の抽出
    member_mapping = get_member_ids_by_service(login_data)
    if member_mapping:
        logger.info(f"子供情報を抽出しました: {len(member_mapping)} 施設分")
    else:
        logger.warning("子供情報の抽出に失敗しました（連絡帳は取得されません）")

    services_data = get_services(session)
    if not services_data:
        return
    
    logger.info(f"Services Data Type: {type(services_data)}")
    # logger.info(f"Services Data Content: {services_data}")

    services = services_data.get('data', [])
    
    # services が辞書の場合 (IDをキーにした辞書になっている)
    if isinstance(services, dict):
        service_list = []
        for svc_id, svc_data in services.items():
            if isinstance(svc_data, dict):
                svc_data['id'] = svc_id
                service_list.append(svc_data)
        services = service_list
    elif not services:
        # もしかしたら data そのものがリストかも？
        if isinstance(services_data, list):
            services = services_data
        else:
            logger.error(f"施設データが見つかりません: {services_data.keys()}")
            return

    for service in services:
        process_service(session, service, args, member_mapping)

if __name__ == "__main__":
    main()
