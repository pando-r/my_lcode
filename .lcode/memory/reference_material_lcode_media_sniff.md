---
type: reference_material
title: lcode 媒体类型魔数识别（tool/media.py）
created: '2026-11-22T19:12:48.612913+08:00'
updated: '2026-11-22T19:12:48.612913+08:00'
---
lcode 的 tool/media.py 用文件头「魔数」判断媒体类型，不依赖扩展名：PNG 89 50 4E 47 0D 0A 1A 0A、JPEG FF D8 FF、GIF GIF87a/GIF89a、WebP RIFF....WEBP（真类型在第 8-12 字节，走 _riff_mime）、PDF %PDF、ZIP/docx PK\x03\x04、MP3 ID3（无 ID3 时帧同步 FF Ex）、7z 37 7A BC AF 27 1C，代码在 media.py:78-113。image_size 同样直接解析字节（PNG 宽高在 16-24 字节，struct.unpack(">II", head[16:24])；JPEG 需扫 SOF 段），因此判断图片尺寸不需要 Pillow，只有缩放才 import PIL。read_file 靠 sniff_mime 避免把二进制当 UTF-8 解码成乱码。
