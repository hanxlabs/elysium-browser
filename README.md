# Elysium Browser Gateway

Elysium 的受限内部浏览器页面抓取服务。它使用固定版本的 CloakBrowser Python wrapper 启动 Chromium，但不暴露 CDP，也不提供登录、验证码处理、任意 JavaScript 执行或通用 HTTP 代理功能。

## 范围

- `POST /internal/v1/pages/fetch`：抓取任意公网 HTTP(S) 站点的已渲染 HTML；可携带现有 Cookie，并回传同站 Cookie。
- 每个请求使用独立浏览器上下文；本版本不保存账号 Profile，也不实现登录或签到。
- 只支持页面导航 `GET`，不支持表单提交、文件下载和任意请求转发。
- URL、页面所有子资源及重定向均受公网地址校验限制；本机、私网和保留地址会被拒绝。

## 部署

1. 开发环境创建 Docker 内部网络（只需一次）：

   ```bash
   docker network create elysium-internal
   ```

2. 准备配置：

   ```bash
   cp .env.example .env
   ```

   至少设置：

   - `BROWSER_GATEWAY_TOKEN`：Elysium 调用时使用的内部鉴权令牌。

3. 构建并启动：

   ```bash
   docker compose up -d --build
   ```

容器不会映射宿主机端口。把 Elysium 服务也加入 `elysium-internal` 网络后，使用 `http://elysium-browser-gateway:8090` 调用。

飞牛生产环境使用私有 Jenkins 仓库的 `elysium-browser-prod/` 流水线部署；该部署会加入现有的 `elysium-server_elysium_ipv6_net` 网络，无需创建 `elysium-internal`。

## Elysium 调用约定

请求头：

```text
X-Elysium-Gateway-Token: <BROWSER_GATEWAY_TOKEN>
Content-Type: application/json
```

请求示例：

```json
{
  "requestId": "a58e2e3b-07a4-4f62-b9da-7a43180ee533",
  "siteKey": "piggo",
  "accountId": 42,
  "url": "https://pt.example.org/index.php",
  "cookie": "session=existing-value",
  "headers": {
    "Referer": "https://pt.example.org/"
  },
  "timeoutSeconds": 45,
  "waitUntil": "domcontentloaded"
}
```

响应包含渲染 HTML、最终 URL、导航状态、同站 Cookie 列表和 `challengeDetected` 标识。此标识只用于 Elysium 的失败分类；网关不会尝试处理验证页面。

## 更新策略

- 网关镜像使用 `elysium-browser-gateway:<版本>` 发布；不要在生产使用未验证的 `latest`。
- CloakBrowser wrapper 在 `requirements.txt` 中精确锁定。升级依赖后先测试，再构建新网关镜像。
- Dockerfile 在构建时执行 `python -m cloakbrowser install`，将浏览器内核随镜像发布，避免首次业务请求下载。
- `browser-cache` volume 保存运行时缓存；它不包含本版本尚未实现的账号 Profile。

## 本地测试

使用 Python 3.12 虚拟环境安装开发依赖后执行：

```bash
pip install -r requirements-dev.txt
pytest -q
```

测试不会启动浏览器或访问外部站点。
