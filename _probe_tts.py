import importlib
import subprocess


def ps(script):
    p = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return ((p.stdout or "") + (p.stderr or "")).strip()


print("=== System.Speech (Desktop) ===")
print(
    ps(
        "Add-Type -AssemblyName System.Speech;"
        "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
        ".GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"
    )
)

print("=== OneCore tokens ===")
print(
    ps(
        r'Get-ChildItem "HKLM:\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens" -ErrorAction SilentlyContinue'
        r" | ForEach-Object { $_.PSChildName }"
    )
)

print("=== modules ===")
for m in ("edge_tts", "pyttsx3", "sounddevice", "soundfile", "numpy", "pygame", "pydub", "miniaudio"):
    try:
        importlib.import_module(m)
        print(m, "OK")
    except Exception:
        print(m, "NO")

print("=== ffmpeg ===")
print(ps("(Get-Command ffmpeg -ErrorAction SilentlyContinue).Source"))
