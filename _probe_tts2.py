import sherpa_onnx

print("version:", getattr(sherpa_onnx, "__version__", "?"))
print("OfflineTts:", hasattr(sherpa_onnx, "OfflineTts"))
cfg = sherpa_onnx.OfflineTtsConfig()
print("config fields:", [f for f in dir(cfg) if not f.startswith("_")])
print("model fields:", [f for f in dir(cfg.model) if not f.startswith("_")])
print("vits fields:", [f for f in dir(cfg.model.vits) if not f.startswith("_")])
print("matcha fields:", [f for f in dir(cfg.model.matcha) if not f.startswith("_")])
print("kokoro fields:", [f for f in dir(cfg.model.kokoro) if not f.startswith("_")])
