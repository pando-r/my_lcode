"""首次运行引导：生成用户级配置，让 `安装完直接敲 mewcode` 成为可能。"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from mewcode import config as config_mod

TEMPLATE_PACKAGE = "mewcode.templates"
TEMPLATE_NAME = "config.yaml.example"

# 首次运行时打印的提示。此处不涉及密钥，可以放心输出到 stderr。
BANNER = """\
mewcode 首次运行：已生成配置文件
  {path}

下一步：用编辑器打开它，把 providers[0].api_key 换成真实密钥，然后重新运行 `mewcode`。
  · Anthropic 密钥：https://console.anthropic.com/settings/keys
  · OpenAI 或兼容端点：把 base_url 指向你的服务地址即可

想用不同模型？可以在项目里另放一份 .mewcode/config.yaml，项目级优先。
"""


def template_text() -> str:
    # 从 wheel 内的模板读取，源码目录与已安装包两条路径都成立。
    return resources.files(TEMPLATE_PACKAGE).joinpath(TEMPLATE_NAME).read_text(encoding="utf-8")


def ensure_user_config(overwrite: bool = False) -> tuple[Path, bool]:
    # 返回 (配置文件路径, 本次是否新生成)。已存在时默认不覆盖。
    path = config_mod.user_config_path()
    if path.is_file() and not overwrite:
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template_text(), encoding="utf-8")
    return path, True


def banner(path: Path) -> str:
    return BANNER.format(path=path)
