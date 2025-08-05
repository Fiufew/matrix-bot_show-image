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


def setup_logging():
    """Настройки логирования."""
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
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
    
    logging.getLogger("nio").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

setup_logging()

logger = logging.getLogger(__name__)

CONFIG_FILE = "config.ini"

client = None

def get_exception_traceback_descr(e):
    """Полное описание исключения"""
    if hasattr(e, '__traceback__'):
        return "".join(traceback.format_exception(type(e), e, e.__traceback__))
    return str(e)

app = FastAPI()

@app.get("/image/{url:path}")
async def get_matrix_image(url):
    """Получение и вывод изображения"""
    try:
        logger.info(f"запрос изображения по url: {url}")
        client = await initialize_client()
        
        mxc_url, filename = await find_mxc_url(client, url)
        logger.debug(f"получена mxc-ссылка: {mxc_url}, имя файла: {filename}")
        
        response = await client.download(mxc_url)
        if not isinstance(response, DownloadResponse):
            logger.error(f"ошибка загрузки изображения. ответ сервера: {response}")
            raise HTTPException(status_code=500, detail="ошибка загрузки изображения")
        
        mime_type, _ = mimetypes.guess_type(filename)
        if mime_type is None:
            mime_type = "image/jpeg"
            logger.warning(f"не удалось определить тип файла для {filename}, используется image/jpeg")
        
        return StreamingResponse(
            BytesIO(response.body),
            media_type=mime_type,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ошибка при обработке запроса {url}: {get_exception_traceback_descr(e)}")
        raise HTTPException(status_code=400, detail="ошибка обработки запроса изображения")

async def create_config():
    """Создание config.ini при первом запуске"""
    logger.info("для использования нужен config.ini с данными")
    homeserver = input("введите ссылку на сервер (пример 'https://matrix.org'): ")
    user_id = input("введите user_id пользователя (пример '@name:matrix.org'): ")
    device_name = input("введите имя устройства (пример 'matrix-nio'): ")
    password = input("введите пароль: ")

    temp_client = AsyncClient(homeserver, user_id, ssl=False)
    resp = await temp_client.login(password, device_name=device_name)

    if isinstance(resp, LoginResponse):
        save_config(resp, homeserver, user_id, password)
        logger.info("вход выполнен, файл config.ini создан")
    else:
        logger.error(f"не удалось войти: {resp}")
        sys.exit(1)
    await temp_client.close()

def save_config(resp, homeserver, user_id, password, filepath="config.ini"):
    """Запись в файл config.ini"""
    config = configparser.ConfigParser()
    config["LOGIN CREDENTIALS"] = {
        'homeserver': homeserver,
        'user_id': user_id,
        'password': password,
        'device_id': resp.device_id,
        'access_token': resp.access_token,
    }
    with open(filepath, 'w') as configfile:
        config.write(configfile)

async def initialize_client() -> AsyncClient:
    """Вход в клиент с помощью config.ini"""
    global client
    if client is None:
        config = await load_config()
        client = AsyncClient(config["homeserver"], ssl=False)
        client.access_token = config["access_token"]
        client.user_id = config["user_id"]
        client.device_id = config["device_id"]
        logger.info("matrix клиент успешно инициализирован")
    return client

async def load_config() -> dict:
    """Загружает конфигурацию из файла"""
    if not os.path.exists(CONFIG_FILE):
        logger.info("конфигурационный файл не найден, начинаем процесс создания")
        await create_config()
    
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    logger.debug("конфигурационный файл успешно загружен")
    return {
        "homeserver": config["LOGIN CREDENTIALS"]["homeserver"],
        "user_id": config["LOGIN CREDENTIALS"]["user_id"],
        "password": config["LOGIN CREDENTIALS"]["password"],
        "device_id": config["LOGIN CREDENTIALS"]["device_id"],
        "access_token": config["LOGIN CREDENTIALS"]["access_token"],
    }

def filename_generator(filename):
    """Генерирует случайное имя файла с сохранением расширения"""
    file_type = os.path.splitext(filename)[1]
    random_name = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    return f"{random_name}{file_type if file_type else '.jpeg'}"

async def find_mxc_url(client, url):
    """Извлечение mxc-ссылки + имя файла"""
    logger.info(f"извлечение mxc-ссылки из {url}")
    try:
        parts = url.split("/")
        if len(parts) < 2:
            raise ValueError("неверный формат url. ожидается !room_id:server.com/$event_id")
        
        room_id = parts[0]
        event_id = parts[1]
        
        response = await client.room_get_event(room_id=room_id, event_id=event_id)
        if not isinstance(response, RoomGetEventResponse):
            logger.error("неверный ответ от сервера matrix")
            raise ValueError("неверный ответ от сервера matrix")
        
        content = response.event.source.get('content', {})
        url_mxc = content.get('url')
        filename = content.get('body', 'image')
        
        if not url_mxc:
            logger.error("не удалось извлечь mxc-ссылку")
            raise ValueError("не удалось извлечь mxc-ссылку")
            
        return url_mxc, filename_generator(filename)
    except Exception as e:
        logger.error(f"ошибка обработки url: {str(e)}")
        raise ValueError(f"ошибка обработки url: {str(e)}")

async def matrix_bot():
    """запуск бота для непрерывной работы"""
    client = await initialize_client()
    logger.info("matrix бот запущен")
    while True:
        try:
            await client.sync(timeout=30000, full_state=True)
        except Exception as e:
            logger.error(f"ошибка синхронизации: {get_exception_traceback_descr(e)}")
            await asyncio.sleep(5)

async def main():
    """Запуск + настройка"""
    await initialize_client()
    logger.info("matrix бот и сервер запущены и работают")
    
    config = Config()
    config.bind = ["0.0.0.0:8000"]
    
    try:
        await asyncio.gather(
            serve(app, config),
            matrix_bot(),
        )
    except asyncio.CancelledError:
        logger.info("приложение остановлено по запросу")
    except Exception as e:
        logger.critical(f"критическая ошибка: {get_exception_traceback_descr(e)}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("приложение остановлено по сигналу keyboardinterrupt")
    except Exception as e:
        logger.critical(f"необработанное исключение: {get_exception_traceback_descr(e)}")