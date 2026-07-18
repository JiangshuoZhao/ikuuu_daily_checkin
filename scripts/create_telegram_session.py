#!/usr/bin/env python3
import getpass
import os

from telethon.sync import TelegramClient
from telethon.sessions import StringSession


def read_api_id() -> int:
    raw_value = os.environ.get("TELEGRAM_API_ID") or input("Telegram API ID: ").strip()
    try:
        api_id = int(raw_value)
    except ValueError as exc:
        raise SystemExit("Telegram API ID 必须是整数") from exc
    if api_id <= 0:
        raise SystemExit("Telegram API ID 必须是正整数")
    return api_id


def main() -> int:
    api_id = read_api_id()
    api_hash = (
        os.environ.get("TELEGRAM_API_HASH")
        or getpass.getpass("Telegram API Hash: ").strip()
    )
    if not api_hash:
        raise SystemExit("Telegram API Hash 不能为空")

    client = TelegramClient(StringSession(), api_id, api_hash)
    try:
        _ = client.start(
            phone=lambda: input(
                "Telegram 手机号（含国家区号，例如 +86138...）: "
            ).strip(),
            code_callback=lambda: input("Telegram 验证码: ").strip(),
            password=lambda: getpass.getpass("Telegram 两步验证密码: "),
        )
        if client.session is None:
            raise SystemExit("无法读取 Telegram Session")
        session = client.session.save()
        print("\n将下面这一整行保存为 GitHub Secret TELEGRAM_SESSION：")
        print(session)
        print("\n请勿提交、分享或记录到仓库文件中。")
    finally:
        _ = client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
