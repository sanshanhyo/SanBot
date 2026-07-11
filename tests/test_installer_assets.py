import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_installer_configures_every_feature_switch_and_group_scope() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    keys = re.findall(r"^([A-Z][A-Z0-9_]*)=", env_example, flags=re.MULTILINE)
    guided_keys = [
        key
        for key in keys
        if key.startswith("ENABLE_")
        or key.endswith("ALLOWED_GROUP_IDS")
        or key == "TG_AUTO_FETCH_GROUP_IDS"
    ]

    missing = [
        key
        for key in guided_keys
        if f'set_env_value "$env_file" {key} ' not in installer
    ]

    assert missing == []
    assert "请输入 true 或 false" in installer


def test_installer_generates_private_onebot_servers() -> None:
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert '"name": "SanBot HTTP"' in installer
    assert '"name": "SanBot WebSocket"' in installer
    assert '"enable": true' in installer
    assert '"127.0.0.1:3000:3000"' not in installer
    assert '"127.0.0.1:3001:3001"' not in installer


def test_generated_compose_template_is_valid_yaml() -> None:
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    compose_function = installer[
        installer.index("write_compose_file()") : installer.index("write_manager_command()")
    ]
    template = re.search(r"<<'EOF'\n(.*?)\nEOF", compose_function, flags=re.DOTALL)

    assert template is not None
    compose = yaml.safe_load(template.group(1))
    assert set(compose["services"]) == {"backend", "bot", "napcat"}
    assert compose["services"]["napcat"]["expose"] == ["3000", "3001"]


def test_container_contains_jav_crawler_and_ffmpeg() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ffmpeg" in dockerfile
    assert "COPY javlibrary_crawler ./javlibrary_crawler" in dockerfile
    assert "COPY assets ./assets" in dockerfile


def test_readme_documents_one_line_install() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "curl -fsSL" in readme
    assert "scripts/install.sh | sudo bash" in readme
    assert "sanbot doctor" in readme
