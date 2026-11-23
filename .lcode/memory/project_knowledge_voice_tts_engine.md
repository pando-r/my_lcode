# lcode 语音朗读引擎：edge（默认，像真人）/ sapi（离线）

K 的原话：「音色有点人机，可不可以换一下音色」。原因是 Windows 自带 SAPI 的中文只有
`Microsoft Huihui Desktop` 一档拼接音，调参救不了——只能换引擎。

## 现在的结构（src/lcode/voice/speech.py）

- `tts_engine: edge`（默认）：微软在线神经语音，`edge-tts` 包（纯 Python，无二进制），
  默认音色 `zh-CN-XiaoxiaoNeural`。要联网，**念的文字会发给微软**。
- `tts_engine: sapi`：Windows `System.Speech`，离线、零依赖、音色机械。
- edge 合成失败（断网/没装包）自动回退 sapi，音色变回人机但仍有声音；日志写一行
  「edge TTS 不可用，这次改用 Windows 自带语音」。
- `VoiceAssistant.tts_engine()` 报**实际**引擎（配置 edge 但没装 edge-tts 时返回 sapi），
  启动日志会写「! 配置要的是 edge，但没装 edge-tts，已降级」。别只看配置就以为生效了。

## 播放为什么用 MediaPlayer

edge 给的是 mp3，`winsound`/`SoundPlayer` 只吃 wav。转码要么装 ffmpeg 要么装 pydub，
所以用 WPF 自带的 `System.Windows.Media.MediaPlayer`（经 PowerShell）直接放 mp3，
按 `NaturalDuration` 等真实时长。**「播放进程活着 = 还在念」**这个判据两种引擎一致，
`wake.py:_tts_running()` 不受影响。

## 新踩的坑

1. **greet 不能同步合成**：在线合成光联网就要 1~2 秒，原来在 greet 里同步调
   `speak_async`，等于每喊一次唤醒词主循环卡一下、麦克风缓冲溢出。现在整段
   （含挑音色，sapi 那条第一次要起 PowerShell 问系统音色）丢进后台线程，
   并用 `_tts_pending` 标记「正在准备说话」——合成期间也要丢弃音频块，
   否则准备阶段的环境声会被当成指令。卡死由 `_speak_until`（6 秒）兜底放行。
2. **音色名不能跨引擎混用**：`Microsoft Huihui Desktop` 塞给 edge 会 400（表现只是
   「念不出来」），所以 `_edge_voice()` 把不认识的名字静默换回默认；反过来 edge 的
   名字塞给 SAPI 会让 `SelectVoice` 失败，`try/catch` 兜住退回系统默认。
3. **asyncio 嵌套**：`edge_tts` 是 async 的，语音主循环本身跑在事件循环里，直接
   `asyncio.run` 会炸（和精转写同一个坑）。`speech._run_coroutine()` 检测到已有 loop
   就另起线程跑。

## 试听 / 换音色的入口（别再靠喊唤醒词试）

```bash
lcode --voice-list-voices          # 两套音色全表 + 当前那个（不需要麦克风/模型）
lcode --voice-say "试一下音色"      # 直接用当前音色念一句
lcode --voice-say "试一下" --voice-tts-voice zh-CN-YunxiNeural   # 试别的
```

配置：`.lcode/config.yaml` 的 `voice.tts_engine` / `voice.tts_voice`。
改完要重启常驻语音实例（启动文件夹里的 `Lcode-voice.lnk`）才生效。
