# 前端

Vue 3、TypeScript、Vite、Vue Router、Element Plus 构成商品影棚工作台。公开快照保留原页面、玻璃组件与业务入口，将权属未核实的视频和海报换成 CSS 冷灰背景；没有第三方视频回退 URL。

完整本地应用建议按[根目录说明](../README.md#本地起步)启动。单独开发前端时，先让隔离后端在本机 `127.0.0.1:8000` 可用，再在 `frontend` 目录执行：

```powershell
npm ci
npm run dev
```

Vite 默认页面为 `http://127.0.0.1:5173`，`/api` 代理到本机后端。Vite 配置监听所有本机网络接口，只应在可信环境使用。`npm run build` 执行 TypeScript 检查和 Vite 构建；它不证明 API、媒体、模型或平台服务可用。Inter 字体及许可见[第三方说明](../THIRD_PARTY_NOTICES.md)。
