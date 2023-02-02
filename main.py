import os

from deta import App
from fastapi import FastAPI, Header, Request
from linebot import WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, PostbackEvent, UnfollowEvent
from starlette.exceptions import HTTPException

from func import *

app = App(FastAPI())
deta = Deta()

handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])


@app.lib.cron()
def deliver_unread_page(event):
    """dbに保存されているユーザーごとに未読記事の配信をする"""
    user_db = deta.Base("user_db")
    pin_page_db = deta.Base("pin_content_db")
    users = user_db.fetch().items
    for user in users:
        if "time" not in user:
            continue

        user_id = user["line_user_id"]
        delivery_time = user["time"]

        if not is_delivery_time(delivery_time):
            continue

        pin_page = pin_page_db.get(user_id)
        if pin_page is None:
            send_message = get_page_data_from_notion(user)
        else:
            send_message = pin_page["page_title"] + "\n\n" + pin_page["page_url"]
        push_message(send_message, user_id)


@app.post("/callback")
async def callback(request: Request, x_line_signature=Header(None)):
    """LINEからのリクエストの署名を検証し、問題なければhandleに定義されている関数を呼び出す"""
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="InvalidSignatureError")
    return "OK"


@handler.add(MessageEvent)
def handle(event):
    user_id = event.source.user_id
    message = event.message.text
    send_message = execute_notion_operation(user_id, message)
    push_message(send_message, user_id)


def execute_notion_operation(user_id, message):
    """LINEから送られてきたメッセージをもとにNotionへの操作を実行する"""
    state_db = deta.Base("state_db")
    item = state_db.get(user_id)

    if "登録情報" in message:
        return register_user_data(user_id, message)

    notion_operation = NotionOperation(user_id, message)
    if message in ["既読", "削除", "配信内容固定"]:
        notion_operation.put_state_db()
        return "URLを入力してください"
    if message == "固定解除":
        return notion_operation.cancel_pin()
    if message == "配信時間設定":
        return notion_operation.get_message_delivery_time()
    if (item is None) or ("http" not in message):
        return "無効な入力内容です"

    state = item["state"]
    if state == "既読":
        return notion_operation.mark_page()
    if state == "削除":
        return notion_operation.delete_page()
    if state == "配信内容固定":
        return notion_operation.pin_delivery_content()
    return "無効な入力内容です"


@handler.add(PostbackEvent)
def postback(event):
    operation_name = event.postback.data
    user_id = event.source.user_id

    if operation_name == "set_time":
        delivery_time = event.postback.params["time"]
        send_message = set_time_to_db(delivery_time, user_id)
    else:
        send_message = clear_time_to_db(user_id)
    push_message(send_message, user_id)


@handler.add(UnfollowEvent)
def unfollow(event):
    user_id = event.source.user_id
    user_db = deta.Base("user_db")
    user = user_db.fetch({"line_user_id": user_id}).items[0]
    user_db.delete(user["key"])
