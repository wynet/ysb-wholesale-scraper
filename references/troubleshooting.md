# 常见问题与排查

## 登录失效 / 会话过期
**症状**：列表页返回 0 条，或页面跳转到登录页。
**原因**：Chrome 9222 会话的登录态过期（`--user-data-dir` 中的 cookie 失效）。
**解决**：
1. 用 `scripts/auto_login.py <手机号> <密码>` 自动登录+滑块验证。
2. 若自动登录失败（滑块 4 次未通过），在 9222 Chrome 窗口手动完成滑块验证后重跑。

## 翻页无效 / 所有页数据相同
**症状**：点击翻页后数据不变，或 URL 改了但内容还是第 1 页。
**原因**：站点是 SPA，URL 的 `?page=N` 参数无效。
**解决**：必须用 `click_next()` 点击「下一页」按钮（`.pagination-next`），每次翻页后等待列表首条变化才算生效。

## 采集途中滑块验证
**症状**：列表页翻页或详情页采集过程中弹出网易易盾滑块验证。
**原因**：请求频率触发了风控策略。
**解决**（已内置自动处理）：
- `extract.py` 和 `fetch_detail.py` 均已内置滑块检测：检测到易盾滑块后**先自动解决**——用 JS canvas 下载背景图、分析每列像素梯度找缺口边缘、计算拖拽距离，再通过 `setTimeout` 调度 `MouseEvent` 模拟人类拖拽（ease-out 轨迹 + y 轴抖动 + 终点回弹），刷新重试最多 3 次。
- 自动解决失败时转为**等待人工完成**（每 2 秒检测弹窗是否消失，超时 60 秒）。
- `fetch_detail.py` 检测点共 3 处：SPA 入口页、每次路由导航后、new_tab 兜底后。
- `fetch_detail.py` 已内置 2 秒间隔防验证。
- **注意**：JS 模拟拖拽的通过率取决于站点风控策略（比 CDP 直连的 `Input.dispatchMouseEvent` 稍弱）。若 JS 方式反复失败，可改用 `auto_login.py` 的 CDP 方式（需在 browser-use 沙箱外运行），或在 9222 Chrome 窗口手动完成。

## 详情页数据全相同（SPA 缓存）
**症状**：所有商品的详情页数据都是第一条的值。
**原因**：Vue Router SPA 缓存了详情页组件，未卸载就重新挂载。
**解决**：`fetch_detail.py` 已修复——每次导航前先 `$router.push('/home')` 回首页，强制 Vue 卸载组件再重新挂载。

## 商品详情链接打不开 / 打开错误页面
**症状**：点击 Excel/HTML 中的商品链接，打开的页面内容不对或报错。
**原因**：拼团商品和普通商品的路由参数不同，用错了参数会打开错误页面。
**解决**：`process.py` 已修复——根据商品名自动选择路由参数：
- 拼团商品（商品名含「包邮」）：`isAssemble=true&scene=0`
- 普通商品（商品名含「起购」）：`isAssemble=false&scene=1`

## 价格显示为乱码
**症状**：DOM 文本中的价格是一串乱码汉字。
**原因**：站点使用字体反爬，DOM 文本不可信。
**解决**：这是正常现象。真实价格在 Vuex 的 `priceToken` 字段中（base64 编码的 protobuf），用 `decode_price()` 解码即可，详见 `references/decryption.md`。

## browser-use 沙箱写文件丢失
**症状**：在 browser-use 脚本中 `open().write()` 写出的文件在真实工作区找不到。
**原因**：browser-use 沙箱进程的文件系统与真实工作区隔离。
**解决**：采集脚本只把 JSON 打到 stdout，由普通 python 进程负责写 xlsx/图片/html。切勿在 extract.py 内直接写产物文件。

## 图片压缩触发批量删除拦截
**症状**：压缩图片时进程被杀，报安全策略拦截。
**原因**：本环境有「批量删除安全策略」，单轮约 50 次文件删除即触发拦截。
**解决**：用 `PIL.Image.open(io.BytesIO(data))` 内存内压缩，不要落临时 `.orig` 文件再 `os.remove()`。

## CDP 消息 ID 冲突
**症状**：CDP 通信返回错误的响应，或 WebSocket 报消息 ID 重复。
**原因**：`wait_and_read` 等待函数与主循环使用了相同的消息 ID 范围。
**解决**：`fetch_detail.py` 已修复——使用全局自增计数器分配消息 ID，消除冲突。

## 详情页部分字段为空
**症状**：部分商品的 `paid_units`/`stores_joined` 等字段为 None。
**原因**：异形页面格式或页面加载不完全。
**解决**：
- `fetch_detail.py` 重跑时会自动检测关键字段全为 None 的记录并重新抓取。
- `process.py` 对缺失数据做诚实降级：HTML 中标「待补采」并置灰，不伪造数据。
