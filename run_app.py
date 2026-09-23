"""Запуск локального мастера импорта. Публичная сеть не поддерживается."""
from __future__ import annotations

import argparse
from pathlib import Path

from workbench.server import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальное приложение «Потоки»: импорт и анализ таблиц")
    parser.add_argument("--port", type=int, default=8765, help="локальный порт (по умолчанию 8765)")
    parser.add_argument("--data-dir", type=Path, default=None, help="отдельный каталог загрузок и результатов")
    args = parser.parse_args()
    try:
        server = create_server(port=args.port, storage=args.data_dir)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Не удалось запустить приложение: {exc}\n")
    port = server.server_address[1]
    print(f"Потоки — локальный мастер импорта: http://127.0.0.1:{port}", flush=True)
    print("Откройте адрес в браузере. Данные остаются на этом компьютере. Для остановки: Ctrl+C.", flush=True)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        print("\nПриложение остановлено.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
