# ikuuu每日签到

这个脚本只适合这一个机场😥😥😥😥，当时只是自己拿来用，就写个这一个机场的。我又写了一个通用的（机场只要Powered by SSPANEL就可以，），请移步<a href = 'https://github.com/bighammer-link/jichang_checkin'>此处</a>
## 推送
  该脚本采用的是server酱的推送方式

# 部署过程
 
1. 右上角Fork此仓库
2. 然后到`Settings`→`Secrets and variables`→`Actions` 新建以下参数：

| 参数   | 是否必须  | 内容  | 
| ------------ | ------------ | ------------ |
| EMAIL  | 是  | 账号邮箱  |
| PASSWD | 是  | 账号密码  |
| IKUUU_COOKIE | 是 | 登录后的完整 Cookie，用于绕过登录验证码直接签到 |
| SCKEY  | 否  | Sever酱秘钥  |

### IKUUU_COOKIE 获取方式

由于 iKuuu 登录页现在有验证码，GitHub Actions 中推荐使用 `IKUUU_COOKIE` 直接签到。

1. 在浏览器打开 `https://ikuuu.org/user` 并完成登录
2. 按 `F12` 打开开发者工具，进入 `Console`
3. 执行以下命令复制 Cookie：

```js
copy(document.cookie)
```

4. 在 GitHub 仓库中进入 `Settings` → `Secrets and variables` → `Actions` → `New repository secret`
5. `Name` 填写 `IKUUU_COOKIE`
6. `Secret` 粘贴刚复制的完整内容，不要加引号，不要换行

示例格式如下，实际值以你浏览器复制到的内容为准：

```text
PHPSESSID=...; uid=...; email=...; key=...; ip=...; expire_in=...
```

`IKUUU_COOKIE` 等同于登录凭证，请不要公开。如果 Action 提示未登录或签到失败，重新登录网站并更新该 Secret。

3. 到`Actions`中创建一个workflow，运行一次，以后每天项目都会自动运行。
4. 最后，可以到Run sign查看签到情况，同时也会也会将签到详情推送到Sever酱。
