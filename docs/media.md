# 看见世界：图片、音频、视频、二进制文件

## 解决的问题

Lcode 的主模型是纯文本的（DeepSeek 这类不接收 `image_url`），而 `read_file`
以前把任何文件都当 UTF-8 硬解 —— 一张截图会变成几万个 `\ufffd` 替换字符，
再被截到 2000 行，既烧 token 又什么都没读到。

现在分三层处理：

| 层 | 文件 | 作用 |
|---|---|---|
| 类型嗅探 | `tool/media.py` | 读文件头判 MIME，不需要任何依赖 |
| 看图 | `tool/view_image.py` + `vision.py` | 副模型看图 → 回文字 |
| 听音 | `tool/transcribe_audio.py` + `audio.py` | 副模型转写 → 回带时间戳的文字 |
| 看视频 | `video.py` + `tui/attachments.py` | 固定抽 16 帧 → 分批交给 `view_image` |

## 核心设计：工具式多模态

图片**不进对话历史**。视觉模型看完，只把答案（文字）交回主模型：

```
用户 → /image 截图.png 这什么报错
         ↓（后台任务）
     vision 模型 → "第 3 行 KeyError: 'name'"
         ↓（作为一条普通 user 消息）
     主模型 → 回答
```

为什么不直接把图片塞进 `Message`：

- `Message.content` 是 `str`，两个 provider 都直传字符串。改类型要横切
  `agent` / `compact` / `memory` / `session` / `subagent` / `team` 六处。
- `ToolResult.content` 也是 `str`，OpenAI 的 tool role 只收文本。
- JSONL 落盘是 `asdict(msg)`，附件一旦是 base64，每轮 fsync 一个几 MB 的字符串。
- `compact/token.py` 按字符数估 token，base64 长度会把上下文瞬间判成"爆了"。

工具式把这些全绕开了：历史里永远只有文本，压缩、恢复、子 Agent 都不用改。

## 配置

`<项目根>/.lcode/config.yaml` 里加两段（模板见仓库里的示例注释）：

```yaml
vision:
  protocol: openai          # 或 anthropic
  api_key: sk-xxx
  model: qwen3-vl-plus      # 填你控制台里真实可见的型号
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
  max_image_side: 1568      # 长边上限，调小更省 token
  detail: high              # low 更省，high 用来读小字

audio:
  backend: dashscope       # 百炼原生 multimodal 接口（推荐，国内最快）
  api_key: "${DASHSCOPE_API_KEY}"
  model: qwen3-asr-flash
  language: zh
  itn: true                # 「八千七百一十四」→ 8714；要保留原话设 false
  # backend: openai        # 走 /audio/transcriptions（OpenAI / Groq 等）
  # backend: local         # faster-whisper，不联网（pip install lcode[media]）
  # local_model: small
```

### 三个后端的真实差异（都是实测出来的）

| | `dashscope` | `openai` | `local` |
|---|---|---|---|
| 端点 | `/api/v1/services/aigc/multimodal-generation/generation` | `/audio/transcriptions` | 本地进程 |
| 单次上限 | **约 9MB**（实测 9.2MB 通过、11.6MB 拒绝） | 约 25MB | 无 |
| 时间戳 | **只有分段级**（每片一个） | 句子级 | 句子级 |
| prompt 提示 | 不支持 | 支持 | 支持 |
| 需要 key | 是 | 是 | 否 |
| 需要 ffmpeg | 非 wav 长音频才需要 | 长音频需要 | 长音频需要 |

**`dashscope` 没有 `/audio/transcriptions` 端点**（实测 404）。它是原生 multimodal 接口，
音频要作为 `data:audio/wav;base64,...` 放进 `input.messages[].content[].audio`，
响应在 `output.choices[].message.content[].text`。这是这次改动里最容易踩的坑。

**上限是按体积不是按时长。** 6 分 20 秒的 22050Hz wav 是 11.6MB，超过 9MB 上限——
光看「5 分钟」这条会漏判。所以切片按**字节预算**算（留 20% 余量），不按秒数算：
采样率可能是 16k / 22.05k / 44.1k，按时长换算必然有偏。

**没写某一段，对应工具就不会注册** —— 模型不会看见一个调用必然失败的工具。
`view_image` / `transcribe_audio` 因此也不占提示词。

`vision` / `audio` 刻意是顶层段落，不是 `providers` 里的条目：
`providers` 列表长度决定启动时是否弹选择菜单，多一个纯服务项会平白多一步。

## 两种用法

**命令式**（你知道要问什么）：

```
/image C:\Users\K\shot.png 这个报错是什么原因
/audio C:\Users\K\备忘.m4a 这是关于 lcode 重构的讨论
```

带空格的路径可以直接写，`split_path_and_question` 会从最短前缀开始试到真实存在的文件；
也可以用引号。没给问题时会用默认问题（"图里有什么，文字请逐字给出"）。

**按钮式**（最省事）：输入框右侧的 `📎 附加`。

```
❯ 这个任务跑成功了吗？                        [📎 附加]
📎 shot.png(图)  (820x200 9.9KB)   [Esc 清空]
```

点一下弹出系统文件对话框（可多选、可混选图片、音频和视频），选完在输入框上方列出
待发送队列，然后照常打字回车。发送时按类型自动分流：

| 类型 | 走哪 |
|---|---|
| 图片 | `view_image`（多张一次调用，适合对比） |
| 音频 | `transcribe_audio` |
| 视频 | 抽 16 帧 → `view_image` 分 3 批（6+6+4） |
| 文本 | 直接内联（带 12000 字符截断） |
| 其它 | 列入「未能读取的部分」 |

**只有附件、不写字也能发**（"看看这个"不必写出来）。按下 Esc 清空队列。

**工具式**（模型自己决定）：

模型遇到"读不了图"的 `read_file` 错误提示，或在你提到某张图时，会自己调
`view_image(paths=[...], question=...)`。它还可以用 `crop: [x1,y1,x2,y2]` 放大看细节
（截图里的小字），或者一次传多张做对比。

## 关键决策

**1. `read_file` 先嗅探再解码。**
`probe()` 读前 8192 字节判 MIME，非文本直接返回一段类型说明 + 该怎么办。
这是这次改动里性价比最高的一步：顺手修掉了把 PNG 当文本读的老 bug。

编码回退是 `utf-8-sig → gb18030 → utf-8(replace)`。GBK 文件以前是满屏乱码，
现在能正常读出来。

**2. `question` 是必填参数。**
只做一个"描述这张图"的工具是废设计。让主模型带着问题去问，返回的是信息而不是
一段通用描述；描述是压缩，压缩完主模型没法追问。

**3. `crop` 相当于给主模型一个显微镜。**
整图缩到 1568 长边后，截图里的报错字体基本糊了。`crop` 让它先定位再放大。

**4. 两个工具都绕过路径沙箱，但只认容器格式。**
截图天然在项目外（桌面、图片目录），要求先拷进仓库会让功能几乎不可用。
真正的安全边界是 `SUPPORTED_MIME`：`view_image` 只收 PNG/JPEG/GIF/WebP，
`transcribe_audio` 只收音频容器 —— 读不到 `.env`、读不到私钥。

代价是 `permission/settings.py` 的 `extract_target` 对这两个工具返回
`("", False, False)`，等于跳过沙箱检查。改动时别顺手把它们加进文件工具列表。

**5. 工具自带超时。**
`Registry.execute` 默认 30s，视觉请求和转写远远不够。`Tool` 协议没加字段，
而是用 `getattr(tool, "timeout", None)` 读取：工具声明了就优先用（
`view_image` 180s，`transcribe_audio` 900s），没声明就沿用调用方默认值。

**6. 文件选择器用子进程，不用 tkinter。**
tkinter 在 Textual 的事件循环里会阻塞主线程；扔到线程里又不稳（Tcl 解释器不跨线程）。
所以走 `powershell -EncodedCommand` 调 `System.Windows.Forms.OpenFileDialog`
（macOS 用 `osascript`，Linux 用 `zenity`/`kdialog`），再用 `asyncio.to_thread` 包起来。
`-EncodedCommand` 是 UTF-16LE base64 —— 和 notify.py 里 Toast 的处理同理，
避免中文路径被 ANSI 代码页折损。

**7. 附件在发送时一次性转成文本，然后清空队列。**
不在附加的瞬间就调视觉模型：那样用户还得等，而且可能反悔。
也不把转换结果缓存——转写失败后留着同样的文件只会反复失败。

转换失败时**不把空内容发给模型**：全部失败就在日志里报错并中止这一轮；
部分失败则照常发送成功的部分，失败的列在提示词的「未能读取的部分」里。
半成功的结果通常仍然有用，静默丢弃反而更糟。

**8. 视频走抽帧，不默认转写音轨。**
第一版只把视频接到 `transcribe_audio`，结果是游戏录像、无解说录屏回一句
「没有识别出任何语音」——画面信息全丢，用户看到的是「你解析不了视频」。
补上固定 16 帧抽帧后，「这视频讲了什么」才终于有答案。
不顺手把音轨也一起转写：那是另一笔（长视频还可能是几十笔）请求，
需要听声音时让模型单独调 `transcribe_audio`，不该每次附视频都付两份钱。

## 大文件怎么处理

- **图片**：长边 > `max_image_side` 或 > 5MB → Pillow 缩放/裁剪，
  有透明通道存 PNG（否则半透明会压成黑块），否则 JPEG q85。
  没装 Pillow 时小图仍能直传，只是不能裁/缩（返回明确的安装提示）。
- **多图总大小 10MB**：DashScope 域名的请求体上限是 16 MiB，而 base64 膨胀 4/3。
  6 张 5MB 的图会变成 40MB，比单图超限更容易踩到，所以在发请求前就拦住，
  并明确提示「减张数 / 裁剪 / 调小 max_side」。
- **音频**：超过单次上限（dashscope 约 9MB / openai 约 25MB）或视频文件才切段。
  优先 ffmpeg（任意格式 + 能抽音轨）；**没装 ffmpeg 时退回纯 Python 的 WAV 切片**——
  录音和录屏最常见的产物就是 wav，不该因为没装 ffmpeg 就用不了长音频。
  切片按字节预算算，段间偏移量累加进时间戳。
- **视频**：附件链路抽 16 帧（`video.py`），每帧缩到 960 宽再送视觉模型。
  不能只转写音轨：录屏和游戏录像大多没人说话，那样用户只会得到
  「没有识别出任何语音」，画面信息全丢。
  抽帧用「每帧一次 `-ss` seek」而不是整片解码一遍 —— 216MB 的 60fps 录像
  抽 16 帧约 5 秒，产出 0.7MB。`-ss` 必须放在 `-i` 之前（关键帧定位），
  缩放表达式里的逗号必须转义成 `\,`，否则会被当成下一个滤镜。
  固定帧数而不是固定间隔：10 秒的 clip 和 10 分钟的录像成本一致。
  单批送 6 张（`view_image` 的上限），每批的问题里带时间范围，主模型才知道
  「哪一段发生了什么」；某一批失败只标在那一批上，不吞掉其它批。
  临时帧发完即删，不在用户磁盘留垃圾。
- **音轨**：`transcribe_audio` 也能直接接 `.mp4/.mov/.mkv/...`，靠 ffmpeg 抽音轨
  （没有 ffmpeg 时给出 `winget install Gyan.FFmpeg`，不静默失败）。
  视频附件本身不自动转写音轨——那是另一笔请求，需要时让模型单独调。
  另外 `.m4a/.m4b` 是纯音频容器（手机录音常见），`sniff_mime` 按 ftyp brand
  判成 `audio/mp4`；判成 video 会让它去抽帧，必然失败。

## 成本

结果尾部会附元信息，方便判断什么时候该 `/compact`：

```
---
[shot.png 1024x683 image/jpeg 11.6KB ≈932 tok]
[vision: qwen3-vl-plus]
```

一张 1568px 的图约 1.1k token，比同等信息量的文字贵 5–10 倍。
所以绝不在 `read_file` 里自动调 vision —— 必须由模型按需调用。

## 已知限制

- **终端里 Ctrl+V 拿不到图片。** Textual 的粘贴只给文本，Windows Terminal
  的图片粘贴根本不会进应用。用 `/image <路径>` 最可靠；真想读剪贴板可以走
  PowerShell `Get-Clipboard -Format Image` 存临时 PNG 再喂进去。
- **PDF / docx / xlsx 还没接。** `read_file` 会明确说明该用什么抽文本，
  但抽取工具本身没做（计划里是 `[project.optional-dependencies]` 那一档）。
- **子 Agent 共用同一份 vision 配置。** 它们和主模型用同一个"眼睛"。
  好在返回值是纯文本，转发给任何模型都不会 400。
- **模型型号会过时，而且比想象中快。** 我曾在文档里写 `qwen3-vl-flash`，
  但它在千问平台的「部分主线模型下线通知」名单里 —— 控制台根本看不到。
  教训：**以控制台实际可见的型号为准**，不要照抄搜索结果里的表格。
  代码层面已把型号配置化（`vision.model` 一行），换型号不用改代码。

  一个正在发生的趋势：视觉能力正在并入主线模型。Qwen3-VL 是当年独立分支，
  而 Qwen3.8 系列本身原生收 Image/Text/Video。所以不必死找带 "VL" 的型号，
  只要控制台里那个模型的「输入模态」里有 Image 就行。

## 搜索通道

`/image` 这类需要"查最新信息"的场景依赖 `search_web`，它的引擎链是：

1. **Tavily**（环境变量 `TAVILY_API_KEY`）——API 型，质量最好，不受页面改版影响；
2. **Bing HTML**——DuckDuckGo 被 DNS 污染时（TCP 直接超时）靠它兜住；
3. **DuckDuckGo html / lite**——Bing 不通的网络环境。

三个引擎全挂时会明确报「所有引擎都不可用」并列出各自原因，
而不是伪装成「没有找到结果」——这两种情况排查方向完全不同。

## 测试

- `tests/test_media.py`（57 个）：工具与协议层。重点覆盖约束而不是"图真的看懂了"：
  - PNG/WAV 用硬编码字节手搓，不依赖 Pillow 造测试数据
  - `read_file` 拒绝二进制时**断言没有 `\ufffd`**（这就是老 bug 的回归点）
  - provider 请求体用假 client 抓下来，断言 `image_url` / `image` block 的结构
  - **断言 dashscope 走的是 `multimodal-generation` 而不是 `/audio/transcriptions`**
    （后者实测 404，写错了只会在真机上报错）
  - 切片用 22050Hz 的 wav 验证「按字节切」，并断言每片都不超上限
  - 转写兼容实现不支持 `verbose_json` 时，退化成整体文本也要能用
  - 工具未配置时返回安装指引，而不是抛异常
  - ftyp brand 分流：`M4A` 判音频、`mp42/qt` 判视频（判反了附件链路会走错分支）
- `tests/test_video.py`（17 个）：抽帧层。`subprocess.run` 换成假 ffmpeg
  （按 `-ss` 的值写文件），于是不需要真视频也能测：
  - 帧数固定 16、时间点均匀且取段中点
  - `-ss` 在 `-i` 之前、缩放表达式逗号已转义
  - 片尾 seek 落空时少几帧不报错；一帧都抽不到才报错
  - 时长解析同时覆盖「ffmpeg 返回 1 但 stderr 有 Duration」和 `N/A` 两种
  - ffmpeg 定位：PATH → imageio-ffmpeg → 都没有时给安装指引
- `tests/test_tui_attach.py`（25 个）：附件 → 提示词是纯函数，重点测这部分。
  视频那段断言三批是 6+6+4、每批问题带时间范围、临时帧用完即删、
  一批视觉请求失败时其它批仍进提示词。
- `tests/test_tui.py` 里的附件段：点按钮、Esc 清空、去重、上限、
  只发附件、斜杠命令不吃附件、没配 vision 时不发空内容。

**别在测试里真的弹文件对话框。** `app.py` 用 `attach_mod.pick_files()`
（模块属性调用）而不是 `from ... import pick_files`，就是为了让 monkeypatch 能生效。
踩过这个坑：早先写成导入名字，测试改不到，真的弹出了 PowerShell 对话框，
单个测试跑了 300 秒才超时。
