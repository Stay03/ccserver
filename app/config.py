import platform
import shutil

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8082
    claude_binary: str = ""
    default_model: str = "sonnet"
    max_budget_usd: float | None = None
    request_timeout: int = 300
    api_key: str = ""
    db_path: str = "claude_proxy.db"
    benchmark_max_concurrency: int = 10
    benchmark_max_requests: int = 50

    model_config = {"env_file": ".env", "env_prefix": "CLAUDE_PROXY_"}

    def get_claude_path(self) -> str:
        if self.claude_binary:
            return self.claude_binary
        if platform.system() == "Windows":
            found = shutil.which("claude.cmd") or shutil.which("claude")
            return found or "claude.cmd"
        return shutil.which("claude") or "claude"


settings = Settings()
