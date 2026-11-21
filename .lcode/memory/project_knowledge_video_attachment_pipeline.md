---
type: project_knowledge
title: lcode 视频附件抽帧链路（方案 B）
created: '2026-11-21T19:33:16.671232+08:00'
updated: '2026-11-21T19:33:16.671232+08:00'
---
lcode 视频附件不再当音频处理，改为「抽帧 → view_image」：
- src/lcode/video.py（新增）：find_ffmpeg / probe_duration / frame_timestamps / extract_frames
- src/lcode/tui/attachments.py：kind=video 走 _run_video，抽帧后按 MAX_IMAGES 分批（6+6+4）调 view_image，拼成带时间范围的文字进提示词
- src/lcode/tool/media.py：ftyp 按 major brand 分流，M4A/M4B→audio/mp4，其余→video/mp4
- docs/media.md 记录方案与决策；测试在 tests/test_video.py 等

ffmpeg 要点：
- ffmpeg 查找顺序 PATH → imageio-ffmpeg 自带二进制，都没有时给 winget 安装提示，不静默失败
- `-ss` 必须放在 `-i` 之前（关键帧定位），放后面会整片解码
- 缩放滤镜里逗号要转义为 `\,`，否则被当成下一个滤镜
- 时长从 `ffmpeg -i` 的 stderr 里抠 Duration，不依赖 ffprobe
- 片尾 seek 落空少几帧不报错，一帧都抽不到才报错；某批视觉请求失败只标记该批
- 帧写 TemporaryDirectory，发完即删

已知边界：固定 16 帧（28 秒与 28 分钟成本相同，长视频中段细节会丢，需改 FRAME_COUNT 或对可疑时段精抽）；不自动转写音轨（要听声需模型自行调 transcribe_audio，块头会注明「只有画面，音轨未转写」）；16 帧 960px ≈ 12k token / 3 次请求。已用真实 vision API 验证通过。
