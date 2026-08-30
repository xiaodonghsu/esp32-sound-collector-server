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
uvicorn app.main:app --host 0.0.0.0 --port 8060
```

## Docker 运行

构建镜像并推送到私有仓库：

```bash
docker build -t nuc10.i.uassist.cn:5000/esp32-sound-collector-server:latest .
docker push nuc10.i.uassist.cn:5000/esp32-sound-collector-server:latest
```

### Linux 部署

该私有仓库使用 HTTP。先将以下配置合并到 Linux 主机的
`/etc/docker/daemon.json`，然后重启 Docker：

```json
{
  "insecure-registries": ["nuc10.i.uassist.cn:5000"]
}
```

```bash
sudo systemctl restart docker
```

应用使用 UID/GID `10001:10001` 运行。先在 Linux 宿主机准备配置目录，
并赋予容器用户写权限：

```bash
sudo install -d -o 10001 -g 10001 /home/jh/esp32-sound-collector-server/data
sudo install -o 10001 -g 10001 -m 0644 clients.yml \
  /home/jh/esp32-sound-collector-server/data/clients.yml
sudo install -m 0600 .env /home/jh/esp32-sound-collector-server/data/.env
```

拉取并启动容器：

```bash
docker stop esp32-sound-collector-server
docker rm esp32-sound-collector-server

docker pull nuc10.i.uassist.cn:5000/esp32-sound-collector-server:latest
docker run -d \
  --name esp32-sound-collector-server \
  --restart unless-stopped \
  -p 8060:8060 \
  -v /home/jh/esp32-sound-collector-server/data:/app/data \
  nuc10.i.uassist.cn:5000/esp32-sound-collector-server:latest

```

必须挂载包含 `clients.yml` 的整个目录，而不能只挂载单个文件；应用通过
临时文件和原子替换更新配置，需要对目录拥有写权限。

健康检查地址为 `http://localhost:8060/health`。部署平台也可以通过 `PORT`
环境变量覆盖监听端口。

应用启动时会自动读取当前工作目录中的 `.env`。已存在的系统环境变量优先于 `.env`；默认 EMQX 地址为 `http://192.168.4.244:18083/api/v5`，全部变量见 `.env.example`。出于安全考虑，仓库不保存真实 API 凭据。

启动后可访问：

- OpenAPI 文档：`http://localhost:8060/docs`
- 健康检查：`GET http://localhost:8060/health`

## API 示例

添加设备（相同 `id` 表示更新；不同设备不能使用相同 `name`）：

```bash
curl -X POST http://localhost:8060/configure/client \
  -H "Content-Type: application/json" \
  -d '{"id":"2884856cbfa4","name":"meeting-root-411","location":"会议室 411","type":"XVF3800"}'
```

查询设备配置与 EMQX 在线状态：

```bash
curl "http://localhost:8060/configure/client?name=meeting-root-411"
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
curl "http://localhost:8060/configure/client"
```

删除设备（请求体只能提供 `id` 或 `name` 中的一个）：

```bash
curl -X DELETE http://localhost:8060/configure/client \
  -H "Content-Type: application/json" \
  -d '{"id":"2884856cbfa4"}'
```

开始录音：

```bash
curl -X POST http://localhost:8060/client/control \
  -H "Content-Type: application/json" \
  -d '{"name":"meeting-root-411","cmd":"start","url":"ws://192.168.4.250:10345/v1/recorder?id=abc","segment":200}'
```

设置设备参数：

```bash
curl -X POST http://localhost:8060/client/control \
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
