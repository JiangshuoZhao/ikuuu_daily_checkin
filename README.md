# iKuuu 每日签到

通过 GitHub Actions 每天自动登录 iKuuu 并签到，全程无需人工操作。

## 工作方式

脚本在无人值守的无头 Chromium 里完成全部动作：

1. 打开 iKuuu 登录页，页面自身会每 2.5 秒轮询一次登录状态；
2. 脚本用 `TELEGRAM_SESSION` 直接把固定验证码发送给 `@iKuuuu_VPN_bot`；
3. iKuuu 检测到已绑定的 Telegram 账号发送了验证码，自动建立登录会话；
4. 脚本在同一浏览器会话中执行签到。

- 固定验证码写在代码里（`DEFAULT_TELEGRAM_CODE = "527740"`），
  也可用 `TELEGRAM_CODE` 环境变量覆盖。
- 登录失败时最多重试 2 次；每次最多等待 60 秒确认登录。
- `SCKEY` 可选；配置后签到成功或失败都会发送 Server酱通知。
- 脚本不会点击页面上的 Telegram 登录按钮，也不会读取页面上的登录码。

## 获取 Telegram API 凭据

1. 使用已绑定 iKuuu 的 Telegram 账号登录
   [my.telegram.org](https://my.telegram.org/)。
2. 打开 `API development tools`。
3. 创建一个应用。`App title` 和 `Short name` 可自行填写。
4. 保存页面显示的 `api_id` 和 `api_hash`。

`api_hash` 是敏感凭据，不要提交到仓库、日志或 Issue。

## 生成 TELEGRAM_SESSION

在本地仓库运行：

```bash
uv run --with 'Telethon>=1.44,<2' scripts/create_telegram_session.py
```

脚本会依次要求输入：

- Telegram API ID 和 API Hash；
- 手机号，需包含国家区号；
- Telegram 收到的验证码；
- 两步验证密码，仅在账号启用两步验证时需要。

完成后会输出一行 `StringSession`。这行内容就是
`TELEGRAM_SESSION`，它可直接访问你的 Telegram 账号，敏感程度与登录凭据相同。
不要在 GitHub Actions 中运行初始化脚本，也不要把输出写入仓库文件。

也可以先通过环境变量提供 API 凭据，减少重复输入：

```bash
TELEGRAM_API_ID=123456 TELEGRAM_API_HASH='your-api-hash' \
  uv run --with 'Telethon>=1.44,<2' scripts/create_telegram_session.py
```

## 配置 GitHub Actions

进入：

`Settings` → `Secrets and variables` → `Actions` →
`New repository secret`

添加以下 Repository Secrets：

| Secret | 必需 | 说明 |
| --- | --- | --- |
| `TELEGRAM_API_ID` | 是 | `my.telegram.org/apps` 显示的数字 API ID |
| `TELEGRAM_API_HASH` | 是 | `my.telegram.org/apps` 显示的 API Hash |
| `TELEGRAM_SESSION` | 是 | 本地初始化脚本生成的完整 StringSession |
| `TELEGRAM_CODE` | 否 | 覆盖代码中的固定验证码，默认 `527740` |
| `SCKEY` | 否 | Server酱 SendKey，用于成功和失败通知 |
| `DOMAIN_NAME` | 否 | iKuuu 站点地址，默认 `https://ikuuu.org/` |

三个 Telegram Secret 必须同时配置，只配置其中一部分时任务会明确报出缺失的
Secret。未配置 Telegram 时任务会直接失败，不再提供其他登录方式。

配置完成后，进入仓库的 `Actions` 页面，选择 `Airport Checkin`，点击
`Run workflow` 手动验证。之后工作流会按计划每天执行。

## Telegram Session 失效

工作流会自动安装 Playwright Chromium，无需额外配置浏览器。

`StringSession` 通常可以长期复用，但以下情况可能使其失效：

- 在 Telegram 官方客户端中终止了对应的活动会话；
- 修改安全设置后 Telegram 撤销了登录授权；
- Telegram 判定会话存在安全风险；
- Session 泄露后主动终止了所有会话。

当日志提示 `Telegram Session 已失效` 时，在本地重新运行初始化脚本，并用新值
覆盖 GitHub 中的 `TELEGRAM_SESSION`。

如果怀疑 Session 泄露，应先在 Telegram 的
`Settings` → `Devices` 中终止相关会话，再重新生成。不要将 Session、API Hash
或 Server酱 SendKey 发到公开位置。

## 本地运行

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

TELEGRAM_API_ID=123456 TELEGRAM_API_HASH='your-api-hash' \
TELEGRAM_SESSION='your-string-session' python main.py
```
