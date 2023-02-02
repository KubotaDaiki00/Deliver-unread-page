import datetime
import os
import random
from typing import Union
from zoneinfo import ZoneInfo

from deta import Deta
from linebot import LineBotApi
from linebot.models import FlexSendMessage, TextSendMessage
from notion_client import APIResponseError, Client

deta = Deta()
line_bot_api = LineBotApi(os.environ["LINE_ACCESS_TOKEN"])


def push_message(send_message: Union[str, dict], user_id: str):
    """ユーザのLINEへメッセージを送る

    Parameters
    ----------
    send_message : Union[str, dict]
        送信する内容
    user_id : str
        LINEのユーザID
    """
    if type(send_message) == str:
        messages = TextSendMessage(text=send_message)
    else:
        messages = FlexSendMessage(alt_text="配信時間を設定してください", contents=send_message)
    line_bot_api.push_message(user_id, messages=messages)


def set_time_to_db(delivery_time: str, user_id: str):
    """配信時間をdbに保存する

    Parameters
    ----------
    delivery_time : str
        配信時間
    user_id : str
        LINEのユーザID

    Returns
    -------
    str
        LINEへ送るメッセージ
    """
    user_db = deta.Base("user_db")
    user = user_db.fetch({"line_user_id": user_id}).items[0]
    user_db.update({"time": delivery_time}, user["key"])
    return f"配信時間が{delivery_time}に設定されました"


def clear_time_to_db(user_id: str):
    """配信時間をdbから削除する

    Parameters
    ----------
    user_id : str
        LINEのユーザID

    Returns
    -------
    str
        LINEへ送るメッセージ
    """
    user_db = deta.Base("user_db")
    user = user_db.fetch({"line_user_id": user_id}).items[0]
    user_db.update({"time": user_db.util.trim()}, user["key"])
    return "配信を一時停止しました\n再開したい場合は、改めて配信時間の設定をしてください"


def is_delivery_time(delivery_time: str):
    """現在時刻が配信時間になっているか判定する

    Parameters
    ----------
    delivery_time : str
        配信時間

    Returns
    -------
    bool
        配信時間ならTrue、そうでないときはFalseを返す
    """

    now_time = datetime.datetime.now(ZoneInfo("Asia/Tokyo"))
    set_time = datetime.datetime.fromisoformat(f"2022-01-04T{delivery_time}+09:00")
    delta = now_time - set_time
    return delta.seconds < 60


def get_page_data_from_notion(user: str):
    """ユーザのNotionDBからランダムな未読記事のタイトルとURLを取得する

    Parameters
    ----------
    user : str
        ユーザ情報

    Returns
    -------
    str
        ランダムな未読記事のタイトルとURLを合わせたLINEメッセージ用のテキスト
    """
    notion = Client(auth=user["notion_api_token"])
    my_database = notion.databases.query(
        **{
            "database_id": user["notion_database_id"],
            "filter": {
                "property": "read",
                "select": {"is_empty": True},
            },
        }
    )
    random_page_num = random.randint(0, len(my_database["results"]) - 1)
    selected_page = my_database["results"][random_page_num]
    selected_page_title, selected_page_url = get_title_and_url(selected_page)
    return selected_page_title + "\n\n" + selected_page_url


def get_title_and_url(selected_page: dict) -> tuple[str, str]:
    """ページデータからタイトルとURLを取得する

    Parameters
    ----------
    selected_page : dict
        Notionページデータ（json）

    Returns
    -------
    tuple[str, str]
        ページタイトルとURL
    """
    selected_page_title = selected_page["properties"]["名前"]["title"][0]["text"][
        "content"
    ]
    selected_page_url = selected_page["properties"]["URL"]["url"]
    return selected_page_title, selected_page_url


def register_user_data(user_id: str, message: str):
    """ユーザ情報を登録する

    Parameters
    ----------
    user_id : str
        LINEのユーザID
    message : str
        ユーザ情報が入ったLINEからのメッセージ

    Returns
    -------
    str
        LINEへ送るメッセージ
    """
    user_db = deta.Base("user_db")
    try:
        _, api_token_text, database_id_text = message.split("\n")
        api_token = api_token_text.split("：")[-1]
        database_id = database_id_text.split("：")[-1]
        notion = Client(auth=api_token)
        notion.databases.query(
            **{
                "database_id": database_id,
                "filter": {
                    "and": [
                        {"property": "名前", "title": {"is_empty": True}},
                        {
                            "property": "URL",
                            "url": {"is_empty": True},
                        },
                        {
                            "property": "read",
                            "select": {"is_empty": True},
                        },
                    ]
                },
            }
        )
    except:
        return "登録が出来ませんでした\n以下が原因の可能性があります\n・トークンかデータベースIDが間違っている\n・データベースのプロパティ名や種類が間違っている\n\n正しい情報で再度登録を行って下さい"
    user_db.put(
        {
            "line_user_id": user_id,
            "notion_api_token": api_token,
            "notion_database_id": database_id,
        },
    )
    return "登録が完了しました\nメニューから配信時間を設定すると配信が開始します"


class NotionOperation:
    def __init__(self, user_id: str, massage: str):
        user_db = deta.Base("user_db")
        user = user_db.fetch({"line_user_id": user_id}).items[0]

        self.user_id = user["line_user_id"]
        self.database_id = user["notion_database_id"]
        self.massage = massage
        self.notion = Client(auth=user["notion_api_token"])

    def put_state_db(self) -> None:
        """deta baseにLINEの現在の入力状態を送る"""
        state_db = deta.Base("state_db")
        state_db.put({"state": self.massage}, self.user_id, expire_in=300)

    def mark_page(self):
        """Notionページに既読をつける。message内のURLと一致するページのみを対象とする。

        Returns
        -------
        str
            LINEへ送るメッセージ
        """
        my_database = self.notion.databases.query(
            **{
                "database_id": self.database_id,
                "filter": {
                    "property": "URL",
                    "url": {"equals": self.massage},
                },
            }
        )
        page_id = my_database["results"][0]["id"]
        self.notion.pages.update(
            **{
                "page_id": page_id,
                "properties": {"read": {"select": {"name": "read"}}},
            }
        )
        return "既読を付けました"

    def delete_page(self):
        """Notionページを削除する。message内のURLと一致するページのみを対象とする。

        Returns
        -------
        str
            LINEへ送るメッセージ
        """
        my_database = self.notion.databases.query(
            **{
                "database_id": self.database_id,
                "filter": {
                    "property": "URL",
                    "url": {"equals": self.massage},
                },
            }
        )
        page_id = my_database["results"][0]["id"]
        self.notion.pages.update(
            **{
                "page_id": page_id,
                "archived": True,
            }
        )
        return "削除しました"

    def pin_delivery_content(self):
        """配信に内容を固定する

        Returns
        -------
        str
            LINEへ送るメッセージ
        """
        my_database = self.notion.databases.query(
            **{
                "database_id": self.database_id,
                "filter": {
                    "property": "URL",
                    "url": {"equals": self.massage},
                },
            }
        )
        selected_page = my_database["results"][0]
        page_title, page_url = get_title_and_url(selected_page)

        pin_page_db = deta.Base("pin_content_db")
        pin_page_db.put(
            {"page_title": page_title, "page_url": page_url},
            self.user_id,
        )
        return "配信内容を固定しました"

    def cancel_pin(self):
        """配信内容の固定を解除する

        Returns
        -------
        str
            LINEへ送るメッセージ
        """
        pin_page_db = deta.Base("pin_content_db")
        pin_page_db.delete(self.user_id)
        return "配信内容の固定を解除しました"

    def get_message_delivery_time(self):
        """配信時間を登録するために必要なメッセージを返す

        Returns
        -------
        dict
            LINEへ送るメッセージ
        """
        flex_message = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "配信時間を設定してください", "align": "center"},
                    {
                        "type": "button",
                        "action": {
                            "type": "datetimepicker",
                            "label": "設定する",
                            "data": "set_time",
                            "mode": "time",
                        },
                    },
                    {"type": "separator", "margin": "xxl"},
                    {
                        "type": "text",
                        "text": "配信を一時停止したい場合は",
                        "align": "center",
                        "margin": "xxl",
                    },
                    {"type": "text", "text": "以下のボタンを押してください", "align": "center"},
                    {
                        "type": "button",
                        "action": {
                            "type": "postback",
                            "label": "配信を一時停止する",
                            "data": "clear_time",
                        },
                    },
                ],
            },
        }
        return flex_message
