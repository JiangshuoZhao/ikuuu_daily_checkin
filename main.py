import requests, json, os

session = requests.session()
# 配置用户名（一般是邮箱）
email = os.environ.get('EMAIL')
# 配置用户名对应的密码 和上面的email对应上
passwd = os.environ.get('PASSWD')
# server酱
SCKEY = os.environ.get('SCKEY')
COOKIE = os.environ.get('IKUUU_COOKIE') or os.environ.get('COOKIE')

## 域名经常出问题
domain_name = os.environ.get('DOMAIN_NAME', 'https://ikuuu.org/').rstrip('/') + '/'
login_url = domain_name + 'auth/login'
check_url = domain_name + 'user/checkin'
info_url = domain_name + 'user/profile'

header = {
        'origin': domain_name.rstrip('/'),
        'referer': login_url,
        'user-agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36'
}
data = {
        'email': email,
        'passwd': passwd
}

def push(content):
    if SCKEY:
        push_url = 'https://sctapi.ftqq.com/{}.send?title=ikuuu自动签到任务提示&desp={}'.format(SCKEY, content)
        requests.post(url=push_url, timeout=20)
        print('推送成功')

def response_json(response):
    response.raise_for_status()
    return response.json()

try:
    if COOKIE:
        print('使用 COOKIE 进行签到...')
        header['cookie'] = COOKIE
    else:
        print('进行登录...')
        response = response_json(session.post(url=login_url,headers=header,data=data,timeout=20))
        print("----域名正常-----")
        print(response['msg'])
        if response.get('ret') != 1:
            raise RuntimeError(response['msg'])
        # 获取账号名称
        session.get(url=info_url,headers=header,timeout=20)
    # 进行签到
    result = response_json(session.post(url=check_url,headers=header,timeout=20))
    print(result['msg'])
    content = result['msg']
    # 进行推送
    push(content)
except Exception as exc:
    content = '签到失败: {}'.format(exc)
    print(content)
    push(content)
    raise SystemExit(1)
