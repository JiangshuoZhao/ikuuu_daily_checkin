from collections import deque
from collections.abc import Mapping, MutableMapping

import pytest
import requests

import main


class FakeResponse:
    def __init__(
        self,
        payload: object = None,
        *,
        text: str = "",
        status_code: int = 200,
        content_type: str = "application/json",
        json_error: Exception | None = None,
    ) -> None:
        self.payload: object = payload
        self.text: str = text
        self.status_code: int = status_code
        self.headers: Mapping[str, str] = {"content-type": content_type}
        self.json_error: Exception | None = json_error

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> object:
        if self.json_error:
            raise self.json_error
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse] | None = None) -> None:
        self.responses = deque(responses or [])
        self.headers: MutableMapping[str, str] = {}
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def _respond(
        self, method: str, url: str, kwargs: dict[str, object]
    ) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"No fake response left for {method} {url}")
        return self.responses.popleft()

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        return self._respond("get", url, kwargs)

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        return self._respond("post", url, kwargs)


class FakeBrowser:
    def __init__(
        self,
        number: str = "123456",
        result: str = "签到成功",
        error: main.CheckinError | None = None,
    ) -> None:
        self.number = number
        self.result = result
        self.error = error
        self.closed = False

    def get_telegram_code(self) -> str:
        if isinstance(self.error, main.ProtocolError):
            raise self.error
        return self.number

    def wait_for_login(self) -> None:
        if self.error is not None and not isinstance(self.error, main.ProtocolError):
            raise self.error

    def check_in(self) -> str:
        return self.result

    def close(self) -> None:
        self.closed = True


def telegram_credentials() -> main.TelegramCredentials:
    return main.TelegramCredentials(12345, "api-hash", "string-session")


def test_telegram_login_and_checkin_success() -> None:
    session = FakeSession()
    client = main.IkuuuClient("https://example.test/", session=session)
    browser = FakeBrowser()
    sent_numbers: list[str] = []
    config = main.Config(
        "https://example.test/",
        telegram_credentials(),
        "stale-cookie",
        None,
    )

    result = main.run_checkin(
        config,
        client=client,
        telegram_sender=lambda _credentials, number: sent_numbers.append(number),
        browser_factory=lambda _domain: browser,
    )

    assert result == "签到成功"
    assert sent_numbers == ["123456"]
    assert browser.closed
    assert "cookie" not in session.headers
    assert session.calls == []


def test_already_checked_in_is_a_valid_result() -> None:
    browser = FakeBrowser(result="今天已经签到过了")
    config = main.Config(
        "https://example.test/",
        telegram_credentials(),
        "stale-cookie",
        None,
    )

    assert (
        main.run_checkin(
            config,
            telegram_sender=lambda _credentials, _number: None,
            browser_factory=lambda _domain: browser,
        )
        == "今天已经签到过了"
    )
    assert browser.closed


def test_polling_timeout_retries_complete_login_twice() -> None:
    browsers = deque(
        [
            FakeBrowser("111111", error=main.LoginTimeoutError("等待超时")),
            FakeBrowser("222222", error=main.LoginTimeoutError("等待超时")),
        ]
    )
    sent_numbers: list[str] = []

    with pytest.raises(main.LoginTimeoutError, match="等待超时"):
        main.login_and_checkin_with_telegram(
            "https://example.test/",
            telegram_credentials(),
            telegram_sender=lambda _credentials, number: sent_numbers.append(number),
            browser_factory=lambda _domain: browsers.popleft(),
        )

    assert sent_numbers == ["111111", "222222"]
    assert not browsers


@pytest.mark.asyncio
async def test_revoked_telegram_session_is_reported() -> None:
    class UnauthorizedClient:
        disconnected = False

        async def connect(self) -> None:
            pass

        async def is_user_authorized(self) -> bool:
            return False

        async def send_message(self, entity: str, message: str) -> None:
            raise AssertionError("send_message must not be called")

        async def disconnect(self) -> None:
            self.disconnected = True

    client = UnauthorizedClient()
    credentials = main.TelegramCredentials(12345, "api-hash", "")

    with pytest.raises(main.TelegramSessionError, match="重新生成"):
        await main.send_telegram_code(
            credentials,
            "123456",
            client_factory=lambda *_args: client,
        )

    assert client.disconnected


def test_login_page_protocol_change_is_reported() -> None:
    browser = FakeBrowser(
        error=main.ProtocolError("iKuuu 登录页格式已变化，未找到 Telegram 登录入口")
    )

    with pytest.raises(main.ProtocolError, match="登录页格式已变化"):
        main.login_and_checkin_with_telegram(
            "https://example.test/",
            telegram_credentials(),
            telegram_sender=lambda _credentials, _number: None,
            browser_factory=lambda _domain: browser,
        )

    assert browser.closed


def test_cookie_fallback_remains_supported() -> None:
    session = FakeSession([FakeResponse({"ret": 0, "msg": "Cookie 签到成功"})])
    client = main.IkuuuClient("https://example.test/", session=session)
    config = main.Config("https://example.test/", None, "cookie-value", None)

    assert main.run_checkin(config, client=client) == "Cookie 签到成功"
    assert session.headers["cookie"] == "cookie-value"


def test_non_json_response_is_reported_without_body() -> None:
    session = FakeSession(
        [
            FakeResponse(
                text="<html>login page containing secrets</html>",
                content_type="text/html; charset=utf-8",
                json_error=ValueError("not json"),
            )
        ]
    )
    client = main.IkuuuClient("https://example.test/", session=session)

    with pytest.raises(main.ProtocolError) as error:
        client.check_in()

    assert "非 JSON" in str(error.value)
    assert "containing secrets" not in str(error.value)


def test_serverchan_notification_uses_post_body() -> None:
    session = FakeSession([FakeResponse({"code": 0})])
    notifier = main.ServerChanNotifier("server-key", session=session)

    assert notifier.send("签到成功")
    method, url, kwargs = session.calls[0]
    assert method == "post"
    assert url == "https://sctapi.ftqq.com/server-key.send"
    assert kwargs["data"] == {
        "title": "iKuuu 自动签到任务提示",
        "desp": "签到成功",
    }


def test_main_sends_success_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    monkeypatch.delenv("TELEGRAM_SESSION", raising=False)
    monkeypatch.setenv("IKUUU_COOKIE", "cookie-value")
    monkeypatch.setenv("SCKEY", "server-key")
    monkeypatch.setattr(main, "run_checkin", lambda _config: "签到成功")
    monkeypatch.setattr(
        main.ServerChanNotifier,
        "send",
        lambda _notifier, content: sent.append(content) is None,
    )

    assert main.main() == 0
    assert sent == ["签到成功"]


def test_main_sends_failure_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    monkeypatch.delenv("TELEGRAM_SESSION", raising=False)
    monkeypatch.setenv("IKUUU_COOKIE", "cookie-value")
    monkeypatch.setenv("SCKEY", "server-key")

    def fail_checkin(_config: main.Config) -> str:
        raise main.ProtocolError("登录页格式已变化")

    monkeypatch.setattr(main, "run_checkin", fail_checkin)
    monkeypatch.setattr(
        main.ServerChanNotifier,
        "send",
        lambda _notifier, content: sent.append(content) is None,
    )

    assert main.main() == 1
    assert sent == ["签到失败：登录页格式已变化"]
