# 药帮忙 Vuex 数据结构与采集陷阱

## 进入 Vuex store
```js
const app = document.querySelector('#app');
const list = app.__vue__.$store.state.drugList.drugList;   // 当前页商品数组（约 60 条）
```

## 商品条目字段（search 结果页）
| 字段 | 含义 | 备注 |
|---|---|---|
| `drugname` | 商品名 | 如「汤臣倍健 钙DK软胶囊 400g」 |
| `specification` | 规格 | 如「400g(200粒)」 |
| `minamount` | 起订量 | 整数，单位见 `unit` |
| `unit` | 单位 | 盒/瓶/袋/粒… |
| `drugimageurl` | 图片完整 URL | `https://img.ysbang.cn/data/img/...` |
| `brand` | 品牌 | |
| `provider_name` | 供应商/店铺 | |
| `wholesaleAmount` | 批发阶梯起量 | |
| `priceToken` | 价格加密串 | 见 decryption.md，base64 protobuf |
| `alreadysales` | 已销量（常为 0） | **非真实销量**，忽略 |

## 翻页
**必须点「下一页」按钮翻页**（`.pagination-next`），URL 的 `?page=N` 参数无效——
站点是 SPA，直接改 URL 或 `new_tab(url?page=N)` 打开的永远是第 1 页（所有页数据雷同）。
每页 `pagesize=60`，列表 URL 仅用于首次入口：
```
https://dian.ysbang.cn/#/indexContent?page=1&pagesize=60&searchkey=<URL编码关键词>&operationtype=1
```
正确做法：进入第 1 页 → 点「销量」排序 → 循环 `click_next()` 点「下一页」，
每次翻页后等待列表首条变化才算生效。

## 致命陷阱：卡片错位 / 推荐串号
搜索结果 DOM 里 `.all-goods-wrapper` 卡片数量（约 70）**多于** Vuex 列表（60），
因为混入了「猜你喜欢」「为你推荐」等**推荐商品**。

- ❌ 错误做法：按索引 `cards[i] ↔ list[i]` 对齐 → 推荐商品的「已拼」「满减价」「有效期」
  会被错挂到主商品上。后果严重：蛋白粉真实销量仅 2，却被记成 11000（那 11000 实际是
  推荐位里某鱼油的）。
- ✅ 正确做法：**按商品名匹配**——
  ```js
  let block = '';
  for (const w of document.querySelectorAll('.all-goods-wrapper')) {
    if (w.innerText.indexOf(it.drugname) !== -1) { block = w.innerText; break; }
  }
  ```
  再把 `block` 按 `it.drugname` 截取一个窗口（如前后 ±250 字符）再去解析
  「已拼N / 满X享Y元 / 有效期日期」，确保只读主商品自身的文本。

## 登录与会话
- 搜索数据**必须登录**才能返回（未登录页返回 0 条）。
- **自动登录**：若未登录或登录过期，用 `scripts/auto_login.py <手机号> <密码>`
  自动完成登录+网易易盾滑块验证（CDP 图像分析找缺口+人类轨迹模拟拖拽，失败自动重试4次）。
  详见 `references/troubleshooting.md` 的「登录失效」章节。
- 连接已登录的 Chrome 调试会话（端口 9222，登录态在 `--user-data-dir` 持久化）。
  browser-use 守护进程自动连上该会话；若该端口没有可用会话，需先启动带
  `--remote-debugging-port=9222 --remote-allow-origins=*` 的 Chrome 并登录。
- 若要对调试端口用 Python 直连 CDP，Chrome 必须带 `--remote-allow-origins=*`
  （用户自己的真实 Chrome 默认不带，会被 WebSocket 拒绝）。

## 文件系统隔离（browser-use 沙箱）
在 browser-use 沙箱进程里写文件**不会出现在真实工作区**（看似成功实则丢失）。
**采集脚本只把 JSON 打到 stdout**，由普通 `python.exe` 进程读取并写出 xlsx/图片/html。
