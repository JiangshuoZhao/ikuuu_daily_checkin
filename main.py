import asyncio
import json
import os
import threading
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import Protocol, cast

import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from telethon import TelegramClient
from telethon.errors import AuthKeyError, RPCError, UnauthorizedError
from telethon.sessions import StringSession

DEFAULT_DOMAIN = "https://ikuuu.org/"
# iKuuu 固定验证码，由脚本发送给 TELEGRAM_BOT 确认登录。
# 账号或验证码变更时修改这里，或用 TELEGRAM_CODE 环境变量覆盖。
DEFAULT_TELEGRAM_CODE = "527740"
TELEGRAM_BOT = "@iKuuuu_VPN_bot"
REQUEST_TIMEOUT_SECONDS = 20
LOGIN_TIMEOUT_SECONDS = 60
LOGIN_ATTEMPTS = 2
PLAYWRIGHT_DEFAULT_TIMEOUT_MS = 20_000


class HttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class HttpSession(Protocol):
    headers: MutableMapping[str, str]

    def get(self, url: str, **kwargs: object) -> HttpResponse: ...

    def post(self, url: str, **kwargs: object) -> HttpResponse: ...


class AsyncTelegramClient(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def is_user_authorized(self) -> bool: ...

    async def send_message(self, entity: str, message: str) -> object: ...


TelegramClientFactory = object


class LoginClient(Protocol):
    def open_login_page(self) -> None: ...

    def wait_for_login(self) -> None: ...

    def check_in(self) -> str: ...

    def close(self) -> None: ...


class CheckinError(RuntimeError):
    """Expected failure with a message safe for logs and notifications."""


class ConfigurationError(CheckinError):
    pass


class NetworkError(CheckinError):
    pass


class ProtocolError(CheckinError):
    pass


class LoginTimeoutError(CheckinError):
    pass


class TelegramSessionError(CheckinError):
    pass


class TelegramDeliveryError(CheckinError):
    pass


@dataclass(frozen=True)
class TelegramCredentials:
    api_id: int
    api_hash: str
    session: str
    code: str

    @classmethod
    def from_environment(cls) -> "TelegramCredentials | None":
        values = {
            "TELEGRAM_API_ID": os.environ.get("TELEGRAM_API_ID", "").strip(),
            "TELEGRAM_API_HASH": os.environ.get("TELEGRAM_API_HASH", "").strip(),
            "TELEGRAM_SESSION": os.environ.get("TELEGRAM_SESSION", "").strip(),
        }
        configured = [name for name, value in values.items() if value]
        if not configured:
            return None
        if len(configured) != len(values):
            missing = ", ".join(name for name, value in values.items() if not value)
            raise ConfigurationError(f"Telegram 配置不完整，缺少：{missing}")

        try:
            api_id = int(values["TELEGRAM_API_ID"])
        except ValueError as exc:
            raise ConfigurationError("TELEGRAM_API_ID 必须是整数") from exc
        if api_id <= 0:
            raise ConfigurationError("TELEGRAM_API_ID 必须是正整数")

        code = (os.environ.get("TELEGRAM_CODE") or "").strip() or DEFAULT_TELEGRAM_CODE
        return cls(
            api_id=api_id,
            api_hash=values["TELEGRAM_API_HASH"],
            session=values["TELEGRAM_SESSION"],
            code=code,
        )


@dataclass(frozen=True)
class Config:
    domain: str
    telegram: TelegramCredentials | None
    sckey: str | None

    @classmethod
    def from_environment(cls) -> "Config":
        domain = (os.environ.get("DOMAIN_NAME") or DEFAULT_DOMAIN).rstrip("/") + "/"
        return cls(
            domain=domain,
            telegram=TelegramCredentials.from_environment(),
            sckey=(os.environ.get("SCKEY") or "").strip() or None,
        )


class ServerChanNotifier:
    def __init__(
        self,
        sckey: str | None,
        session: HttpSession | None = None,
        request_timeout: int = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.sckey = sckey
        self.session = session or requests.Session()
        self.request_timeout = request_timeout

    def send(self, content: str) -> bool:
        if not self.sckey:
            return False
        try:
            response = self.session.post(
                f"https://sctapi.ftqq.com/{self.sckey}.send",
                data={"title": "iKuuu 自动签到任务提示", "desp": content},
                timeout=self.request_timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"Server酱通知发送失败（{type(exc).__name__}）")
            return False
        print("Server酱通知发送成功")
        return True


class PlaywrightIkuuuClient:
    def __init__(
        self,
        domain: str,
        default_timeout_ms: int = PLAYWRIGHT_DEFAULT_TIMEOUT_MS,
        login_timeout_ms: int = LOGIN_TIMEOUT_SECONDS * 1000,
    ) -> None:
        self.domain = domain.rstrip("/") + "/"
        self.login_url = self.domain + "auth/login"
        self.checkin_url = self.domain + "user/checkin"
        self.default_timeout_ms = default_timeout_ms
        self.login_timeout_ms = login_timeout_ms
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                )
            )
            self._context.set_default_timeout(self.default_timeout_ms)
            self._page = self._context.new_page()
        except PlaywrightError as exc:
            self.close()
            raise NetworkError(f"浏览器启动失败（{type(exc).__name__}）") from exc
        return self._page

    def open_login_page(self) -> None:
        try:
            page = self._ensure_page()
            page.goto(
                self.login_url,
                wait_until="domcontentloaded",
                timeout=self.default_timeout_ms,
            )
        except PlaywrightTimeoutError as exc:
            raise NetworkError("打开 iKuuu 登录页超时") from exc
        except PlaywrightError as exc:
            raise NetworkError(
                f"浏览器访问 iKuuu 失败（{type(exc).__name__}）"
            ) from exc

    def wait_for_login(self) -> None:
        try:
            page = self._ensure_page()
            page.wait_for_url("**/user**", timeout=self.login_timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise LoginTimeoutError("等待登录确认超时") from exc
        except PlaywrightError as exc:
            raise NetworkError(f"浏览器等待登录失败（{type(exc).__name__}）") from exc

    def check_in(self) -> str:
        try:
            page = self._ensure_page()
            result = page.evaluate(
                """
                async (url) => {
                  const response = await fetch(url, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                      'Accept': 'application/json',
                      'X-Requested-With': 'XMLHttpRequest'
                    }
                  });
                  return {
                    status: response.status,
                    contentType: response.headers.get('content-type') || '',
                    text: await response.text()
                  };
                }
                """,
                self.checkin_url,
            )
        except PlaywrightError as exc:
            raise NetworkError(f"浏览器签到请求失败（{type(exc).__name__}）") from exc

        if not isinstance(result, dict):
            raise ProtocolError("签到响应结构不符合预期")
        status = result.get("status")
        content_type = str(result.get("contentType", "")).split(";")[0] or "unknown"
        if not isinstance(status, int) or status >= 400:
            raise NetworkError(f"签到请求失败（HTTP {status}）")
        text = result.get("text")
        if not isinstance(text, str):
            raise ProtocolError("签到响应结构不符合预期")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProtocolError(
                f"iKuuu 返回了非 JSON 响应（HTTP {status}, {content_type}）"
            ) from exc
        if not isinstance(payload, dict):
            raise ProtocolError("iKuuu 返回的 JSON 结构不符合预期")
        message = payload.get("msg")
        if not isinstance(message, str) or not message.strip():
            raise ProtocolError("签到响应缺少有效消息")
        return cast(str, message.strip())

    def close(self) -> None:
        for resource_name in ("_context", "_browser"):
            resource = getattr(self, resource_name)
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
                setattr(self, resource_name, None)
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self._page = None


async def send_telegram_code(
    credentials: TelegramCredentials,
    client_factory: TelegramClientFactory | None = None,
) -> None:
    client: AsyncTelegramClient | None = None
    try:
        factory = client_factory or cast(TelegramClientFactory, TelegramClient)
        client = factory(
            StringSession(credentials.session),
            credentials.api_id,
            credentials.api_hash,
        )
        await client.connect()
        if not await client.is_user_authorized():
            raise TelegramSessionError(
                "Telegram Session 已失效，请在本地重新生成 TELEGRAM_SESSION"
            )
        _ = await client.send_message(TELEGRAM_BOT, credentials.code)
    except TelegramSessionError:
        raise
    except (UnauthorizedError, AuthKeyError, ValueError) as exc:
        raise TelegramSessionError(
            "Telegram Session 已失效，请在本地重新生成 TELEGRAM_SESSION"
        ) from exc
    except (OSError, asyncio.TimeoutError) as exc:
        raise NetworkError(f"Telegram 网络连接失败（{type(exc).__name__}）") from exc
    except RPCError as exc:
        raise TelegramDeliveryError(
            f"Telegram 消息发送失败（{type(exc).__name__}）"
        ) from exc
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass


def send_telegram_code_sync(
    credentials: TelegramCredentials,
    client_factory: TelegramClientFactory | None = None,
) -> None:
    error: list[BaseException] = []

    def run_in_thread() -> None:
        try:
            asyncio.run(send_telegram_code(credentials, client_factory=client_factory))
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]


def login_and_checkin_with_telegram(
    domain: str,
    credentials: TelegramCredentials,
    telegram_sender: object = send_telegram_code_sync,
    browser_factory: type[LoginClient] | object = PlaywrightIkuuuClient,
) -> str:
    last_error: CheckinError | None = None
    for attempt in range(1, LOGIN_ATTEMPTS + 1):
        browser = browser_factory(domain)
        try:
            browser.open_login_page()
            print(f"发送固定验证码 {credentials.code} 到 {TELEGRAM_BOT}")
            telegram_sender(credentials)
            browser.wait_for_login()
            result = browser.check_in()
            print(result)
            return result
        except (NetworkError, LoginTimeoutError) as exc:
            last_error = exc
            if attempt < LOGIN_ATTEMPTS:
                print(f"登录未完成，准备重试（{attempt + 1}/{LOGIN_ATTEMPTS}）")
        finally:
            browser.close()
    if last_error is not None:
        raise last_error
    raise CheckinError("登录失败")


def run_checkin(
    config: Config,
    telegram_sender: object = send_telegram_code_sync,
    browser_factory: type[LoginClient] | object = PlaywrightIkuuuClient,
) -> str:
    if config.telegram is None:
        raise ConfigurationError(
            "请配置 TELEGRAM_API_ID、TELEGRAM_API_HASH、TELEGRAM_SESSION"
        )

    print("使用浏览器和 Telegram 获取新的登录会话...")
    return login_and_checkin_with_telegram(
        config.domain,
        config.telegram,
        telegram_sender=telegram_sender,
        browser_factory=browser_factory,
    )


def main() -> int:
    notifier: ServerChanNotifier | None = None
    try:
        config = Config.from_environment()
        notifier = ServerChanNotifier(config.sckey)
        result = run_checkin(config)
    except CheckinError as exc:
        content = f"签到失败：{exc}"
        print(content)
        if notifier is not None:
            notifier.send(content)
        return 1
    except Exception as exc:
        content = f"签到失败：未知错误（{type(exc).__name__}）"
        print(content)
        if notifier is not None:
            notifier.send(content)
        return 1

    notifier.send(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
