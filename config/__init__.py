"""
配置加载器 — 读取 config.yaml 并提供全局访问
"""
import os
import yaml
from pathlib import Path


def load_config(config_path: str = None) -> dict:
    """加载配置文件"""
    if config_path is None:
        # 按优先级查找配置文件
        candidates = [
            Path("config/config.yaml"),
            Path("config/config.example.yaml"),
            Path(__file__).parent.parent / "config" / "config.yaml",
            Path(__file__).parent.parent / "config" / "config.example.yaml",
        ]
        for p in candidates:
            if p.exists():
                config_path = str(p)
                break
        else:
            raise FileNotFoundError("找不到配置文件，请复制 config.example.yaml 为 config.yaml")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return config


def load_dotenv(root: str | Path | None = None) -> None:
    """手动读取项目根目录的 .env（项目未装 python-dotenv），把键值注入 os.environ。

    已存在的环境变量不覆盖（os.environ.setdefault）。多个 demo 脚本共用此实现。
    """
    if root is None:
        root = Path(__file__).resolve().parent.parent
    env = Path(root) / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get_api_key(env_name: str) -> str:
    """从环境变量获取 API Key"""
    key = os.getenv(env_name)
    if not key:
        raise ValueError(f"环境变量 {env_name} 未设置，请在 .env 文件或系统环境中配置")
    return key


# 全局配置单例
_config = None


def get_config() -> dict:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def get_proxy_url() -> str | None:
    """Return proxy URL if enabled in config or env, else None."""
    # Env vars take priority
    from_env = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or os.getenv("https_proxy") or os.getenv("http_proxy")
    if from_env:
        return from_env
    # Fall back to config.yaml
    try:
        cfg = get_config()
        proxy_cfg = cfg.get("proxy", {})
        if proxy_cfg.get("enabled"):
            return proxy_cfg.get("https") or proxy_cfg.get("http")
    except FileNotFoundError:
        pass
    return None
