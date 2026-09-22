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
uvicorn app.main:app --host 0.0.0.0 --port 8060 --log-config logging.yml
```

## 控制消息日志

启动时加载 `logging.yml`，为 API、MQTT、服务运行及 HTTP 访问日志添加消息记录时间
（精确到毫秒，采用运行环境的本地时区）。Docker 默认启动命令已加载此配置。

默认 Uvicorn INFO 日志会输出 `/client/control` 通过校验后的原始请求（`API IN`）、
转换后的下发负载（`API OUT`）、返回上游的设备响应或 EMQX 错误（`API RESPONSE`）。
同时输出通过 EMQX 同步接口提交的 MQTT 消息（`MQTT SEND`）、接口返回的原始消息
（`MQTT RECEIVE`）和 Base64 解码后的设备负载（`MQTT RECEIVE DECODED`）。
日志包含设备 ID、生成的 mid、主题及完整负载，便于核对 URL 和追踪收发。
`MQTT SEND` 表示提交发送请求，不代表设备已收到。容器中可通过
`docker logs -f esp32-sound-collector-server` 查看日志。

## 设备上下线记录

服务启动后使用 `sys_recorders` 账号监听 MQTT 系统主题
`$SYS/brokers/+/clients/+/connected` 和
`$SYS/brokers/+/clients/+/disconnected`，仅保存 `username` 为 `Recorders`
的消息（包括尚未加入 clients.yml 的设备）。默认连接 `192.168.4.244:1883`，
可通过 `.env` 中的 `MQTT_HOST`、`MQTT_PORT`、`MQTT_USERNAME`、`MQTT_PASSWORD` 覆盖。
该账号需有这两个系统主题的订阅权限，EMQX 需开启相应系统事件发布。
连接失败会在后台自动重试，重连成功后重新订阅；服务停机或断线期间的历史消息无法补回。

测试EMQX的服务端命令:
```bash
mosquitto_sub -h 192.168.4.244 -p 11883 -t '$SYS/brokers/+/clients/+/connected' -t '$SYS/brokers/+/clients/+/disconnected' -u sys_recorders -P bestlink
```

日志位于 `clients.yml` 同目录下的 `<clientid>.json`，内容为 JSON 数组。
每条保留原消息字段，增加 `event`（`connected` / `disconnected`）和 `topic`。
按原消息 `ts`（Unix 毫秒）倒序保存最近 7 天的数据；写入、查询和每分钟巡检时清理
过期记录，因此空闲文件的物理清理最多延迟一分钟。文件采用原子替换写入。
请以单个服务实例、单个 Uvicorn worker 使用同一数据目录，避免多个进程并发覆盖日志。
设备 ID 作为文件名时仅接受字母、数字、下划线、连字符和点，且不能以点开头或结尾、不能是 Windows 保留名称。

`GET /configure/client?id=2884856cbfa4`（也支持 `name` 查询）在设备对象中增加
`connection_history`，按时间从新到旧返回最多两条记录，无记录时为 `[]`。
设备离线时也返回历史记录；不带查询条件的列表接口仍仅返回配置。

```json
{
  "clients": [{
    "id": "2884856cbfa4",
    "name": "meeting-root-411",
    "online": false,
    "connection_history": [{
      "event": "disconnected",
      "topic": "$SYS/brokers/emqx@172.17.0.5/clients/2884856cbfa4/disconnected",
      "clientid": "2884856cbfa4",
      "username": "Recorders",
      "ts": 1789981555275,
      "reason": "keepalive_timeout"
    }]
  }]
}
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
  -d '{"name":"meeting-root-411","cmd":"start","url":"ws://192.168.41.15:12345/v1/recorder?id=6F9619FF-8B86-D011-B42D-00C04FC964FF&segment=200&samplerate=16&bitrate=16&channel=1"}'
```

结束录音：

```bash
curl -X POST http://localhost:8060/client/control \
  -H "Content-Type: application/json" \
  -d '{"id":"2884856cbfa4","cmd":"stop"}'
```

设置设备参数：

```bash
curl -X POST http://localhost:8060/client/control \
  -H "Content-Type: application/json" \
  -d '{"id":"2884856cbfa4","cmd":"set","parameters":[{"para":"LED_EFFECT","value":1},{"para":"LED_BRIGHTNESS","value":50}]}'
```

`mid` 不属于接口入参，每次请求均由服务自动生成 UUID，并仅用于 MQTT 通信。为兼容旧调用方，请求中即使携带 `mid` 也会被系统生成值覆盖。

`segment` 不再作为顶层接口参数，语音采集要求统一通过 `url` 的查询参数指定：`segment` 为分包时长（默认 `200` ms）、`samplerate` 为采样率（默认 `16` kHz）、`bitrate` 为位深（默认 `16` bit）、`channel` 为通道数（默认 `1`，单声道）。旧调用方携带的顶层 `segment` 会被忽略。除 `id`、`name`、`mid` 和顶层 `segment` 外，控制请求中的扩展字段会原样转发到设备。

`POST /client/control` 会提取 EMQX 同步请求响应中的 Base64 `payload`，解码并解析 JSON，然后直接以该 JSON 作为 API 响应；EMQX 的外层响应字段不会返回给调用方。

设备负载中 `result` 为 `failed` 时返回 HTTP **502**，响应体直接返回移除 `mid` 后的错误内容，例如：

```json
{"result":"failed","message":"ESP_ERR_INVALID_STATE"}
```

HTTP 请求超时、EMQX 返回 HTTP 408/504，或 EMQX 同步请求返回超时错误时，返回 HTTP **504**，响应体为 `{"detail":"超时原因"}`。成功响应保持原有格式。

## 测试

```powershell
pip install -r requirements-dev.txt
pytest
```
