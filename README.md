# LocalDocFormatter

LocalDocFormatter 是一个本地文档批量转换工具。

它在 `127.0.0.1` 上提供 Web UI，文件保留在本机，并使用 LibreOffice
`soffice --headless --convert-to` 作为转换引擎。

## 获取程序

二选一即可：

1. 使用 Release 包：
   从发布页下载压缩包，解压到本地目录（例如 `LocalDocFormatter/`）。
2. 使用源码：

```bash
git clone https://github.com/HaoRangQi/LocalDocFormatter.git
cd <repo-dir>
```

下面所有命令都在项目根目录执行。

## Docker 运行（推荐）

推荐使用 Docker 运行 LocalDocFormatter，因为镜像内已包含 LibreOffice 和常用中文字体。

前置准备：

1. 先安装并启动 Docker Desktop 或 OrbStack。
2. 在终端确认 Docker daemon 可用：`docker version`。
3. 进入项目目录（即包含 `Dockerfile` 与 `docker-compose.yml` 的目录）。
4. 确认本机 `38173` 端口未被占用（若占用请调整 `docker-compose.yml` 端口映射）。

启动步骤：

1. 使用 Compose 构建并启动：

```bash
docker compose up --build
```

2. 打开浏览器访问：

```text
http://127.0.0.1:38173
```

默认 `docker-compose.yml` 挂载：

- `./docker-data` 到 `/data`，用于保存 AI 配置
- `~/Documents` 到 `/workspace/Documents`
- `~/Downloads` 到 `/workspace/Downloads`

在 UI 中可使用以下容器路径：

```text
/workspace/Documents
/workspace/Downloads
```

Docker 模式提供挂载目录浏览器。点击“文件/文件夹”按钮后，只能选择 `/workspace`
下可见路径；未挂载目录对容器不可见。

如需额外挂载目录，可修改 `docker-compose.yml`，示例：

```yaml
volumes:
  - /Users/you/WorkDocs:/workspace/WorkDocs
```

然后在 Web UI 中使用 `/workspace/WorkDocs`。macOS 原生模式支持系统文件选择器；Docker
模式请填写或粘贴容器路径。

也可以直接运行镜像：

```bash
docker build -t localdocformatter:local .
docker run --rm \
  -p 127.0.0.1:38173:38173 \
  -v "$PWD/docker-data:/data" \
  -v "$HOME/Documents:/workspace/Documents" \
  -v "$HOME/Downloads:/workspace/Downloads" \
  localdocformatter:local
```

常用容器环境变量：

```text
DOCFORMAT_PORT=38173
DOCFORMAT_WORKSPACE_ROOTS=/workspace
DOCFORMAT_AI_CONFIG_PATH=/data/ai-config.json
```

如果挂载了多个独立根目录，用 `:` 分隔：

```bash
-e DOCFORMAT_WORKSPACE_ROOTS="/workspace:/archive"
```

## 本机运行

```bash
python3 -m docformat
```

启动后在浏览器中打开终端输出的本地地址。默认会自动尝试打开浏览器。

常用环境变量：

```bash
DOCFORMAT_PORT=38173 python3 -m docformat
DOCFORMAT_HOST=0.0.0.0 DOCFORMAT_PORT=38173 python3 -m docformat
DOCFORMAT_NO_BROWSER=1 python3 -m docformat
```

## 依赖要求

- Python 3.11+
- LibreOffice（实际转换必需）

macOS 安装 LibreOffice：

```bash
brew install --cask libreoffice
```

程序可在未安装 LibreOffice 时启动并提示安装，但执行转换任务需要 `soffice`。

## 支持模式

`modernize`：将旧版或开放格式转换为现代 Office 格式：

- `.doc`、`.dot`、`.rtf`、`.odt`、`.txt`、`.html` 转 `.docx`
- `.xls`、`.xlt`、`.ods`、`.csv` 转 `.xlsx`
- `.ppt`、`.pps`、`.pot`、`.odp` 转 `.pptx`

`pdf`：将 Writer、Calc、Impress 家族文件转为 `.pdf`。

隐藏文件和 Office 锁文件（例如 `~$draft.docx`）会被自动跳过。输出写入所选目录；若未选择输出目录，默认写入源目录下 `converted/`。存在同名文件时不会覆盖，会追加 ` (1)`、` (2)` 等后缀。

每个任务会写出：

- `conversion-report.json`
- `conversion-report.csv`

## AI 文稿修正

LocalDocFormatter 支持通过 OpenAI-compatible API 修正常见语音转写错误。

修正策略为保守模式：

- 修正明显错别字、同音误转、ASR 转译错误、专名误识别和必要标点
- 不做润色、总结、改写、扩写、缩写、重排或语气改动
- 仅返回修正后的全文

支持输入：

- 粘贴文本
- `.txt`、`.md`、`.srt` 文件

文件修正输出为 `name.corrected.ext`，写在源文件旁且不覆盖原文件。`.srt` 会保留字幕序号和时间轴。

### 词表文件格式

转换时勾选“转换时先做 AI 错别字修正”后，可以选择词表文件，也可以在页面表格里手动添加词条。词表用于提示 AI 将常见误识别内容按指定方向修正。

支持以下文件格式，文件编码请使用 UTF-8：

CSV：

```csv
错误词,正确词
在见,再见
open ai,OpenAI
```

TSV：

```text
错误词	正确词
阿里妈妈	阿里巴巴
```

JSON 对象：

```json
{
  "在见": "再见",
  "open ai": "OpenAI"
}
```

JSON 数组：

```json
[
  {"wrong": "在见", "correct": "再见"},
  {"key": "open ai", "value": "OpenAI"}
]
```

JSONL：

```jsonl
{"wrong": "在见", "correct": "再见"}
{"key": "open ai", "value": "OpenAI"}
```

TXT/MD：

```text
在见 => 再见
open ai => OpenAI
```

页面里的“检查词表”会读取词表文件并显示每个文件读取到的条数、前 5 条样例和失败原因。任务启动时如果词表文件不存在、编码错误、JSON/CSV 解析失败或没有有效词条，任务会失败并把原因写入页面和 `conversion-report.json`。

AI 配置默认保存在：

```text
~/Library/Application Support/DocFormat/ai-config.json
```

配置文件权限为 `0600`。前端仅接收脱敏 key 与 `hasApiKey` 状态；任务报告不会保存源文或 API key。

`base_url` 会按 OpenAI-compatible 的 `/v1` 根地址保存。可以填写包含 `/v1` 的完整地址，也可以填写服务根地址，程序会自动补齐 `/v1`，例如：

```text
https://api.openai.com/v1
https://your-relay.example.com/v1
```

模型发现按 OpenAI Models API 调用 `GET {base_url}/models`。文稿修正按 OpenAI Chat Completions API 调用 `POST {base_url}/chat/completions`，请求体包含 `messages`、`model`、`temperature: 0`、`stream: true`，并从 SSE 的 `choices[].delta.content` 增量拼回全文。

## 本地 API

服务默认绑定 `127.0.0.1`。所有变更类接口都需要 `X-DocFormat-Token`。浏览器从本地服务获取该 token。

- `GET /api/health`
- `POST /api/jobs`
- `GET /api/jobs/{id}`
- `POST /api/jobs/{id}/cancel`
- `GET /api/pick?kind=files`
- `GET /api/pick?kind=directory`
- `GET /api/browse?path=...`
- `GET /api/ai/config`
- `POST /api/ai/config`
- `POST /v1/models`：本地模型列表代理，请求体带 `baseUrl`、`apiKey`、`selectedModel`，服务端再按 OpenAI Models API 转发到 `{base_url}/models`
- `POST /api/ai/correction-jobs`
- `GET /api/ai/correction-jobs/{id}`
- `POST /api/ai/correction-jobs/{id}/cancel`

## 测试

```bash
python3 -m unittest discover -s tests
```

执行完整 QA 套件：

```bash
./scripts/run_qa_suite.sh
```

在 QA 套件中包含 Docker 镜像构建：

```bash
./scripts/run_qa_suite.sh --with-docker-build
```

## QA 报告

详细 QA 报告见：

```text
docs/QA_REPORT_2026-05-30.md
```

## 开发环境启动

本节用于本地开发和调试，按步骤执行即可。

### 1. 前置检查

1. 确认在项目根目录（包含 `docformat/`、`README.md`、`docker-compose.yml`）。
2. 本机开发模式：确认 Python 可用。

```bash
python3 --version
```

3. Docker 开发模式：确认 Docker daemon 可用。

```bash
docker version
```

4. 确认端口可用（默认 `38173`）。

```bash
lsof -i :38173
```

如果有占用，请先停止占用进程或改用其他端口。

### 2. 启动方式 A（本机开发）

```bash
DOCFORMAT_NO_BROWSER=1 DOCFORMAT_PORT=38173 python3 -m docformat
```

默认固定使用 `38173`，避免开发和使用时地址来回变化。只有确认端口冲突时才临时改端口，例如：

```bash
DOCFORMAT_NO_BROWSER=1 DOCFORMAT_PORT=38174 python3 -m docformat
```

### 3. 启动方式 B（Docker 开发）

```bash
docker compose up --build
```

如需后台运行：

```bash
docker compose up --build -d
```

### 4. 启动成功验证

浏览器访问：

```text
http://127.0.0.1:38173
```

或用健康检查接口：

```bash
curl http://127.0.0.1:38173/api/health
```

### 5. 停止与清理

本机开发模式：在前台终端按 `Ctrl + C`。

Docker 前台模式：

```bash
docker compose down
```

Docker 后台模式：

```bash
docker compose down
```

### 6. 常见问题

1. 端口占用：改 `DOCFORMAT_PORT` 或调整 `docker-compose.yml` 端口映射。
2. Docker 无法连接 daemon：先启动 Docker Desktop 或 OrbStack。
3. 无法转换：检查 LibreOffice 是否就绪，或在 Docker 模式使用内置 LibreOffice。

## 致谢

vibe by codex，让我们谢谢 codex。
