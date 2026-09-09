from .config import Config
from .web import create_app


def main() -> None:
    import uvicorn

    config = Config.from_env()
    uvicorn.run(create_app(config), host=config.host, port=config.port)


__all__ = ["create_app", "main"]
