import asyncio
import configparser
import logging
import mimetypes
import os
import random
import string
import sys
import traceback
from io import BytesIO
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from hypercorn.asyncio import serve
from hypercorn.config import Config
from nio import (
    AsyncClient,
    DownloadResponse,
    LoginResponse,
    RoomGetEventResponse,
)
from logging.handlers import TimedRotatingFileHandler

# Глобальные переменные с аннотациями типов
client: Optional[AsyncClient] = None
log: Optional[logging.Logger] = None
web: Optional[FastAPI] = None
config: Optional[Dict[str, Any]] = None


def setup_logging() -> logging.Logger:
    """Инициализация и настройка логирования."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Очистка существующих обработчиков
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Создание директории для логов
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    
    # Настройка ротирующего обработчика
    handler = TimedRotatingFileHandler(
        filename=os.path.join(log_dir, 'matrix_bot.log'),
        when='midnight',
        interval=1,
        backupCount=30,
        encoding='utf-8',
        utc=True
    )
    
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)-8s] %(name)-20s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    # Уменьшение уровня логирования для внешних библиотек
    logging.getLogger("nio").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    return logger


async def load_config() -> Dict[str, Any]:
    """Загрузка конфигурации из файла."""
    config_file = "config.ini"
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Конфигурационный файл {config_file} не найден")
    
    parser = configparser.ConfigParser()
    parser.read(config_file)
    
    if not parser.has_section("LOGIN CREDENTIALS"):
        raise ValueError("В конфигурационном файле отсутствует секция LOGIN CREDENTIALS")
    
    required_keys = ['homeserver', 'user_id', 'password', 'device_id', 'access_token']
    if not all(key in parser["LOGIN CREDENTIALS"] for key in required_keys):
        raise ValueError("В конфигурационном файле отсутствуют необходимые ключи")
    
    return {
        "homeserver": parser["LOGIN CREDENTIALS"]["homeserver"],
        "user_id": parser["LOGIN CREDENTIALS"]["user_id"],
        "password": parser["LOGIN CREDENTIALS"]["password"],
        "device_id": parser["LOGIN CREDENTIALS"]["device_id"],
        "access_token": parser["LOGIN CREDENTIALS"]["access_token"],
    }


async def initialize_client() -> AsyncClient:
    """Инициализация Matrix клиента."""
    global client, config
    
    if client is None:
        if config is None:
            config = await load_config()
        
        client = AsyncClient(
            homeserver=config["homeserver"],
            user=config["user_id"],
            ssl=False
        )
        client.access_token = config["access_token"]
        client.user_id = config["user_id"]
        client.device_id = config["device_id"]
        
        log.info("Matrix клиент успешно инициализирован")
    
    return client


def create_web_app() -> FastAPI:
    """Создание и настройка FastAPI приложения."""
    app = FastAPI()

    @app.get("/image/{url:path}")
    async def get_matrix_image(url: str):
        """Обработчик запросов изображений."""
        global client
        
        try:
            log.info(f"Запрос изображения по URL: {url}")
            
            mxc_url, filename = await find_mxc_url(client, url)
            log.debug(f"Получена mxc-ссылка: {mxc_url}, имя файла: {filename}")
            
            response = await client.download(mxc_url)
            if not isinstance(response, DownloadResponse):
                error_msg = f"Ошибка загрузки изображения. Ответ сервера: {response}"
                log.error(error_msg)
                raise HTTPException(status_code=500, detail=error_msg)
            
            mime_type, _ = mimetypes.guess_type(filename)
            if mime_type is None:
                mime_type = "image/jpeg"
                log.warning(f"Не удалось определить тип файла для {filename}, используется image/jpeg")
            
            return StreamingResponse(
                BytesIO(response.body),
                media_type=mime_type,
            )
            
        except HTTPException:
            raise
        except Exception as e:
            error_msg = f"Ошибка обработки запроса {url}: {get_exception_traceback_descr(e)}"
            log.error(error_msg)
            raise HTTPException(status_code=400, detail=error_msg)

    return app


def get_exception_traceback_descr(e: Exception) -> str:
    """Форматирование описания исключения."""
    if hasattr(e, '__traceback__'):
        return "".join(traceback.format_exception(type(e), e, e.__traceback__))
    return str(e)


def generate_filename(original_name: str) -> str:
    """Генерация случайного имени файла с сохранением расширения."""
    file_ext = os.path.splitext(original_name)[1]
    random_name = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    return f"{random_name}{file_ext if file_ext else '.jpeg'}"


async def find_mxc_url(client: AsyncClient, url: str) -> tuple[str, str]:
    """Извлечение mxc-ссылки и имени файла из URL."""
    log.info(f"Извлечение mxc-ссылки из {url}")
    
    try:
        parts = url.split("/")
        if len(parts) < 2:
            raise ValueError("Неверный формат URL. Ожидается !room_id:server.com/$event_id")
        
        room_id, event_id = parts[0], parts[1]
        
        response = await client.room_get_event(room_id=room_id, event_id=event_id)
        if not isinstance(response, RoomGetEventResponse):
            error_msg = "Неверный ответ от сервера Matrix"
            log.error(error_msg)
            raise ValueError(error_msg)
        
        content = response.event.source.get('content', {})
        url_mxc = content.get('url')
        filename = content.get('body', 'image')
        
        if not url_mxc:
            error_msg = "Не удалось извлечь mxc-ссылку"
            log.error(error_msg)
            raise ValueError(error_msg)
            
        return url_mxc, generate_filename(filename)
        
    except Exception as e:
        error_msg = f"Ошибка обработки URL: {str(e)}"
        log.error(error_msg)
        raise ValueError(error_msg)


async def run_matrix_bot() -> None:
    """Основной цикл работы Matrix бота."""
    global client
    
    log.info("Matrix бот запущен")
    while True:
        try:
            await client.sync(timeout=30000, full_state=True)
        except Exception as e:
            log.error(f"Ошибка синхронизации: {get_exception_traceback_descr(e)}")
            await asyncio.sleep(5)


async def run_web_server() -> None:
    """Запуск веб-сервера."""
    global web
    
    log.info("Веб-сервер запускается")
    
    server_config = Config()
    server_config.bind = ["0.0.0.0:8000"]
    
    try:
        await serve(web, server_config)
    except Exception as e:
        log.critical(f"Ошибка веб-сервера: {get_exception_traceback_descr(e)}")
        raise


async def main() -> None:
    """Основная функция приложения."""
    global log, config, client, web
    
    try:
        # Инициализация компонентов
        log = setup_logging()
        config = await load_config()
        client = await initialize_client()
        web = create_web_app()
        
        log.info("Инициализация прошла успешно")
        
        # Запуск основных компонентов
        await asyncio.gather(
            run_web_server(),
            run_matrix_bot(),
        )
        
    except asyncio.CancelledError:
        log.info("Приложение остановлено по запросу")
    except Exception as e:
        log.critical(f"Критическая ошибка: {get_exception_traceback_descr(e)}")
        raise
    finally:
        if client:
            await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        if log:
            log.info("Приложение остановлено по сигналу KeyboardInterrupt")
        else:
            print("Приложение остановлено по сигналу KeyboardInterrupt")
    except Exception as e:
        if log:
            log.critical(f"Необработанное исключение: {get_exception_traceback_descr(e)}")
        else:
            print(f"Необработанное исключение: {str(e)}")