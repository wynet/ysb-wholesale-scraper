# 药帮忙价格解密 —— 两层反爬

药帮忙（dian.ysbang.cn）的商品价格有**两层**保护，单纯抓 DOM 文本会得到乱码，
必须用下面方法还原真实单价。

## 第一层：字体反爬（DOM 文本不可信）
- 页面用自定义 Web 字体（`.ysb-din-bold` 之类）把数字渲染成**看似正常、但 Unicode
  码点是乱码 CJK** 的字符。
- 后果：从 `innerText` / `textContent` 拿到的价格是一串乱码汉字，**不要用 DOM 文本
  当价格**。
- 实测：CSS 与 JS 里找不到该字体的 `@font-face` 定义，浏览器回退到系统字体才显示
  出乱码。字体文件无法抓取，也**无需**抓取——因为真实值在数据层。

## 第二层：数据层加密（priceToken）
每条商品（Vuex store 中）带一个 `priceToken` 字段，是 **base64 编码的 protobuf**，
真实单价以明文嵌在 protobuf 里。

### 解码方法
```python
import base64, re

def decode_price(tok):
    if not tok:
        return None
    b = base64.b64decode(tok)
    # protobuf length-delimited 字段：marker 后是 1 字节长度 L，再后是 L 字节字符串
    for marker in (b'\x12', b'\x3a'):      # field 2 / field 7, wire type 2
        i = b.find(marker)
        while i != -1:
            if i + 1 < len(b):
                L = b[i + 1]
                if 1 <= L <= 12:
                    s = b[i + 2: i + 2 + L]
                    if re.match(rb'^\d+\.\d{1,2}$', s) or re.match(rb'^\d+$', s):
                        return s.decode('latin-1')
            i = b.find(marker, i + 1)
    m = re.search(rb'\d+\.\d{2}', b)        # 兜底
    return m.group(0).decode('latin-1') if m else None
```

### 真实样例
`priceToken = "COb30tQEEgUyNi4wMCAIMgX/////BzoFMjYuMDA6"`
解码后字节含 `\x12\x05 26.00` 与 `\x3a\x05 26.00:`，其中 `26.00` 即真实单价。

### 要点
- **登录与否不影响价格**（已验证：登录态页面价格照样加密）。
- 解码是确定的、可离线批量跑，无需网络或字体文件。
- `alreadysales` 字段常为 0，**不是**真实销量；真实销量在卡片的「已拼N」文本里。
