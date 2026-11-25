import argparse
from pathlib import Path

from .app import create_app
from .media import MediaService


def main():
    parser = argparse.ArgumentParser(description="Локальная выгрузка видео и обложек YouTube")
    parser.add_argument("--output", type=Path, default=Path("downloads"), help="папка для файлов")
    parser.add_argument("--port", type=int, default=8765, help="локальный порт")
    parser.add_argument("--allow-pages", action="store_true", help="подключить интерфейс GitHub Pages на порту 8765")
    parser.add_argument("--browser", choices=("firefox", "chrome", "edge", "brave", "chromium", "safari",
                                             "opera", "vivaldi", "whale"),
                        help="использовать локальный сеанс YouTube в этом браузере")
    parser.add_argument("--js-runtime", choices=("node", "deno"), default="node", help="среда JavaScript")
    args = parser.parse_args()
    if args.allow_pages and args.port != 8765:
        parser.error("GitHub Pages подключается к порту 8765")
    app = create_app(args.output, MediaService(args.browser, args.js_runtime), args.allow_pages)
    app.run(host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
