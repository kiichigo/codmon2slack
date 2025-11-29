import os
import time
import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

# ログ設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 環境変数読み込み
load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID")

def clean_channel_history():
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        logger.error("環境変数が設定されていません。")
        return

    client = WebClient(token=SLACK_BOT_TOKEN)
    
    try:
        # 履歴を取得 (最新1000件まで)
        logger.info("メッセージ履歴を取得中...")
        response = client.conversations_history(channel=SLACK_CHANNEL_ID, limit=1000)
        messages = response.get("messages", [])
        
        if not messages:
            logger.info("削除対象のメッセージはありません。")
            return

        logger.info(f"{len(messages)} 件のメッセージが見つかりました。削除を開始します...")

        count = 0
        for msg in messages:
            ts = msg.get("ts")
            
            try:
                # メッセージを削除
                client.chat_delete(channel=SLACK_CHANNEL_ID, ts=ts)
                logger.info(f"削除成功: {ts}")
                count += 1
                
                # APIレートリミット回避のために少し待機
                time.sleep(1.2) 
                
            except SlackApiError as e:
                if e.response['error'] == 'cant_delete_message':
                    logger.warning(f"削除不可（他人の投稿など）: {ts}")
                else:
                    logger.error(f"削除エラー: {e.response['error']}")
        
        logger.info(f"完了: {count} 件のメッセージを削除しました。")

    except SlackApiError as e:
        if e.response['error'] == 'missing_scope':
            logger.error("権限不足です。SlackアプリのScopesに 'channels:history' (または groups:history) を追加してください。")
        else:
            logger.error(f"履歴取得エラー: {e.response['error']}")

if __name__ == "__main__":
    print("⚠️  警告: このスクリプトは設定されたチャンネルのメッセージを削除します。")
    confirm = input("実行しますか？ (y/n): ")
    if confirm.lower() == 'y':
        clean_channel_history()
    else:
        print("キャンセルしました。")
