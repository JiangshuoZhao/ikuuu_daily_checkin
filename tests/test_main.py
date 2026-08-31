from collections import deque

import pytest

import main


class FakeResponse:
    def __init__(
        self,
        payload: object = None,
        *,
        status_code: int = 200,
    ) -> None:
        self.payload: object = payload
        self.status_code: int = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")

    def json(self) -> object:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse] | None = None) -> None:
        self.responses = deque(responses or [])
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append(("post", url, kwargs))
        if not self.responses:
            raise AssertionError(f"No fake response left for post {url}")
        return self.responses.popleft()


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


class FakePage:
    def __init__(self, evaluate_result: object) -> None:
        self.evaluate_result = evaluate_result

    def evaluate(self, _script: str, *_args: object) -> object:
        return self.evaluate_result


def telegram_credentials() -> main.TelegramCredentials:
    return main.TelegramCredentials(12345, "api-hash", "string-session")


def test_partial_telegram_config_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    monkeypatch.delenv("TELEGRAM_SESSION", raising=False)

    with pytest.raises(main.ConfigurationError, match="TELEGRAM_SESSION"):
        main.TelegramCredentials.from_environment()


def test_config_reads_domain_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOMAIN_NAME", raising=False)
    monkeypatch.delenv("SCKEY", raising=False)

    assert main.Config.from_environment().domain == main.DEFAULT_DOMAIN


def test_telegram_login_and_checkin_success() -> None:
    browser = FakeBrowser(number="654321")
    sent_codes: list[str] = []
    config = main.Config("https://example.test/", telegram_credentials(), None)

    result = main.run_checkin(
        config,
        telegram_sender=lambda _credentials, code: sent_codes.append(code),
        browser_factory=lambda _domain: browser,
    )

    assert result == "签到成功"
    assert sent_codes == ["654321"]
    assert browser.closed


def test_run_checkin_requires_telegram_config() -> None:
    config = main.Config("https://example.test/", None, None)

    with pytest.raises(main.ConfigurationError, match="TELEGRAM_SESSION"):
        main.run_checkin(config)


def test_already_checked_in_is_a_valid_result() -> None:
    browser = FakeBrowser(result="今天已经签到过了")
    config = main.Config("https://example.test/", telegram_credentials(), None)

    assert (
        main.run_checkin(
            config,
            telegram_sender=lambda _credentials, _code: None,
            browser_factory=lambda _domain: browser,
        )
        == "今天已经签到过了"
    )
    assert browser.closed


def test_timeout_retries_request_a_new_code() -> None:
    browsers = deque(
        [
            FakeBrowser("111111", error=main.LoginTimeoutError("等待超时")),
            FakeBrowser("222222", error=main.LoginTimeoutError("等待超时")),
        ]
    )
    sent_codes: list[str] = []

    with pytest.raises(main.LoginTimeoutError, match="等待超时"):
        main.login_and_checkin_with_telegram(
            "https://example.test/",
            telegram_credentials(),
            telegram_sender=lambda _credentials, code: sent_codes.append(code),
            browser_factory=lambda _domain: browsers.popleft(),
        )

    assert sent_codes == ["111111", "222222"]
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

    # 空字符串可被 StringSession 解析；占位字符串会导致会话对象未创建
    credentials = main.TelegramCredentials(12345, "api-hash", "")

    with pytest.raises(main.TelegramSessionError, match="重新生成"):
        await main.send_telegram_code(
            credentials,
            "123456",
            client_factory=lambda *_args: client,
        )

    assert client.disconnected


@pytest.mark.asyncio
async def test_sync_telegram_sender_works_inside_running_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[str, str]] = []

    async def fake_send(
        credentials: main.TelegramCredentials,
        code: str,
        client_factory: object | None = None,
    ) -> None:
        sent.append((credentials.session, code))

    monkeypatch.setattr(main, "send_telegram_code", fake_send)
    credentials = main.TelegramCredentials(12345, "api-hash", "sess")

    main.send_telegram_code_sync(credentials, "4321")

    assert sent == [("sess", "4321")]


def test_login_page_protocol_change_is_reported() -> None:
    browser = FakeBrowser(
        error=main.ProtocolError("iKuuu 登录页格式已变化，未找到 Telegram 登录入口")
    )

    with pytest.raises(main.ProtocolError, match="登录页格式已变化"):
        main.login_and_checkin_with_telegram(
            "https://example.test/",
            telegram_credentials(),
            telegram_sender=lambda _credentials, _code: None,
            browser_factory=lambda _domain: browser,
        )

    assert browser.closed


def test_checkin_non_json_response_is_reported_without_body() -> None:
    client = main.PlaywrightIkuuuClient("https://example.test/")
    client._page = FakePage(
        {
            "status": 200,
            "contentType": "text/html; charset=utf-8",
            "text": "<html>login page containing secrets</html>",
        }
    )

    with pytest.raises(main.ProtocolError) as error:
        client.check_in()

    assert "非 JSON" in str(error.value)
    assert "containing secrets" not in str(error.value)


def test_checkin_http_error_is_reported() -> None:
    client = main.PlaywrightIkuuuClient("https://example.test/")
    client._page = FakePage(
        {"status": 403, "contentType": "text/html", "text": "<html>denied</html>"}
    )

    with pytest.raises(main.NetworkError, match="HTTP 403"):
        client.check_in()


def test_checkin_missing_message_is_reported() -> None:
    client = main.PlaywrightIkuuuClient("https://example.test/")
    client._page = FakePage(
        {"status": 200, "contentType": "application/json", "text": '{"ret": 0}'}
    )

    with pytest.raises(main.ProtocolError, match="缺少有效消息"):
        client.check_in()


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


def test_serverchan_skipped_without_key() -> None:
    session = FakeSession()
    notifier = main.ServerChanNotifier(None, session=session)

    assert not notifier.send("签到成功")
    assert session.calls == []


def test_main_sends_success_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "api-hash")
    monkeypatch.setenv("TELEGRAM_SESSION", "string-session")
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
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "api-hash")
    monkeypatch.setenv("TELEGRAM_SESSION", "string-session")
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


def test_main_missing_config_fails_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    monkeypatch.delenv("TELEGRAM_SESSION", raising=False)
    monkeypatch.delenv("SCKEY", raising=False)

    # run_checkin 在缺少 Telegram 配置时直接报错，且无 SCKEY 时不发通知
    assert main.main() == 1
    assert sent == []
