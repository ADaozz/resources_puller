# 网页媒体资源解析器

在一个页面里操作真实的 Chromium，并持续捕获这个浏览器发出的图片和视频地址。第一版是单会话。

## 同一会话

画面和监听不是两个浏览器。

1. 容器启动 Xvfb，显示号 `:99`。
2. Playwright 以有头模式把 Chromium 启动在这个显示号上。
3. x11vnc 读取同一块屏幕，websockify / noVNC 把画面和键鼠送进网页里的 iframe。
4. 你在 iframe 里的点击、滚动，进入的就是 Playwright 正在监听的那个 Chromium 进程。
5. `BrowserContext` 在导航之前挂上响应监听。图片、视频和 HLS/DASH 清单会进资源组，`.ts` / `.m4s` 分片只作为清单的片段，不计入视频资源。

页面上显示的进程号，就是这一个 Chromium。

## 运行

```bash
docker compose up --build
```

浏览器打开 <http://127.0.0.1:8080>。

地址栏由容器内的 Chromium 打开。内置测试页请使用 `http://127.0.0.1:8080/demo/`。点「测试页」即可。下方是图片仓和视频仓：没有资源时是空状态，超过 4 个时最多叠 4 张封面并显示真实数量。悬停会展开，点击打开列表。列表按资源组勾选，可以切换分辨率、预览、复制原始链接、单个或批量下载。

验收脚本（在服务起来之后）：

```bash
docker compose exec app python /app/scripts/verify.py
```

脚本会检查：有头浏览器、noVNC 点击命中同一页面、实时捕获、多分辨率合并、不同目录同名分开、重复请求、懒加载、动态 MP4、HLS 分片不计数、堆叠仓和列表、选择在重连后保持、预览、复制、批量下载、HLS/DASH 合并、进度、取消、签名失效、SSRF、会话清理。

单元测试不依赖浏览器：

```bash
python3 backend/tests/test_logic.py
```

## 接口

- `POST /api/sessions`：创建或返回当前唯一会话
- `DELETE /api/sessions/{id}`：关闭页面并删除捕获记录和下载文件
- `POST /api/sessions/{id}/navigate|back|forward|reload`
- `GET /api/sessions/{id}/resources`
- `GET /api/sessions/{id}/variants/{variant_id}/preview`
- `POST /api/sessions/{id}/downloads`，`POST .../downloads/{job_id}/cancel`，`GET .../file`
- `WS /api/sessions/{id}/events`：`resource.created`、`resource.updated`、`variant.added`、`download.updated`

## 资源怎么合并

文件名去掉分辨率后缀（如 `_1280x720`、`-720p`、`_1080`、`@2x`）后，与主机、目录、扩展名一起作为组键。同一目录下的不同清晰度合成一组，每条原始链接都保留。不同目录的同名文件分开。无法判断时保持分开。

HLS 主清单和它的各档播放列表在同一组。DASH 清单上的 Representation 作为清晰度。下载清单时用 FFmpeg 合成一个 MP4，状态从 `PENDING` 到 `PROBING`、`DOWNLOADING` 或 `MERGING`，再到 `COMPLETED`，也可以是 `FAILED` 或 `CANCELLED`。

## 限制

- 只做单会话，VNC 没有密码，只监听容器内的本地端口。不要把 8080 暴露到公网。
- 导航、预览和下载会拒绝私网、链路本地和云元数据地址。本服务自己的 `127.0.0.1:8080` / `:8000` 例外，用来打开内置测试页。检查发生在每次连接和重定向之前，极端的 DNS 重绑定仍应靠网络隔离。
- 第一版不做断点续传，也不处理 DRM。
- 容器里的 Chromium 关闭了沙箱（`--no-sandbox`），这是在容器中跑有头浏览器的常见前提。
