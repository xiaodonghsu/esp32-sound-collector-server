# ESP32 Sound Collector Server

基于 FastAPI 的 ESP32 录音设备管理服务。服务以 `clients.yml` 保存设备配置，通过 EMQX Enterprise REST API 查询连接状态，并使用 `emqx_sync_request` 插件同步发送 MQTT 控制消息。

## 启动

需要 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env，填写 EMQX_API_KEY 和 EMQX_SECRET_KEY
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Docker 运行

构建镜像：

```bash
docker build -t esp32-sound-collector-server .
```

启动容器。EMQX 凭据通过环境变量传入，不会打包进镜像；命名卷会
持久化 `clients.yml` 中的设备配置：

```bash
docker run --rm -p 8000:8000 \
  --env-file .env \
  -v esp32-clients:/app/data \
  esp32-sound-collector-server
```

Windows PowerShell 使用：

```powershell
docker run --rm -p 8000:8000 `
  --env-file .env `
  --mount "type=volume,source=esp32-clients,target=/app/data" `
  esp32-sound-collector-server
```

如果需要映射宿主机目录，应挂载包含 `clients.yml` 的整个目录，而不要只
挂载单个文件，以便应用能够原子更新配置。

健康检查地址为 `http://localhost:8000/health`。部署平台也可以通过 `PORT`
环境变量覆盖监听端口。

应用启动时会自动读取当前工作目录中的 `.env`。已存在的系统环境变量优先于 `.env`；默认 EMQX 地址为 `http://192.168.4.244:18083/api/v5`，全部变量见 `.env.example`。出于安全考虑，仓库不保存真实 API 凭据。

启动后可访问：

- OpenAPI 文档：`http://localhost:8000/docs`
- 健康检查：`GET http://localhost:8000/health`

## API 示例

添加设备（相同 `id` 表示更新；不同设备不能使用相同 `name`）：

```bash
curl -X POST http://localhost:8000/configure/client \
  -H "Content-Type: application/json" \
  -d '{"id":"2884856cbfa4","name":"meeting-root-411","location":"会议室 411","type":"XVF3800"}'
```

查询设备配置与 EMQX 在线状态：

```bash
curl "http://localhost:8000/configure/client?name=meeting-root-411"
```

查询结果统一使用 `clients` 数组；指定设备时，EMQX 实时信息位于该设备的 `status` 字段：

```json
{
  "clients": [
    {
      "id": "2884856cbfa4",
      "name": "meeting-root-411",
      "status": {
        "connected": true
      }
    }
  ]
}
```

不提供 `id` 或 `name` 时返回 `clients.yml` 中的全部设备配置，并且不会查询 EMQX：

```bash
curl "http://localhost:8000/configure/client"
```

删除设备（请求体只能提供 `id` 或 `name` 中的一个）：

```bash
curl -X DELETE http://localhost:8000/configure/client \
  -H "Content-Type: application/json" \
  -d '{"id":"2884856cbfa4"}'
```

开始录音：

```bash
curl -X POST http://localhost:8000/client/control \
  -H "Content-Type: application/json" \
  -d '{"name":"meeting-root-411","cmd":"start","url":"ws://192.168.4.250:10345/v1/recorder?id=abc","segment":200}'
```

设置设备参数：

```bash
curl -X POST http://localhost:8000/client/control \
  -H "Content-Type: application/json" \
  -d '{"id":"2884856cbfa4","cmd":"set","parameters":[{"para":"LED_EFFECT","value":1},{"para":"LED_BRIGHTNESS","value":50}]}'
```

`mid` 可由调用方提供；省略时服务会自动生成 UUID。除 `id` 和 `name` 外，控制请求中的扩展字段会原样转发到设备。`start` 未给出 `segment` 时默认为 `200` ms。

`POST /client/control` 会提取 EMQX 同步请求响应中的 Base64 `payload`，解码并解析 JSON，然后直接以该 JSON 作为 API 响应；EMQX 的外层响应字段不会返回给调用方。

## 测试

```powershell
pip install -r requirements-dev.txt
pytest
```
