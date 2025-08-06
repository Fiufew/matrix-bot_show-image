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
from typing import Optional

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

# Глобальные переменные
client: Optional[AsyncClient] = None
web: Optional[FastAPI] = None
log: Optional[logging.Logger] = None
config: Optional[configparser.ConfigParser] = None

# ========== Функции инициализации ==========

def init_logging() -> logging.Logger:
    """Инициализация системы логирования"""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Удаляем все существующие обработчики
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Создаем директорию для логов если ее нет
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    
    # Настраиваем ротирующий обработчик
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
    
    # Уменьшаем уровень логирования для некоторых библиотек
    logging.getLogger("nio").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    
    return logger

def load_config() -> configparser.ConfigParser:
    """Загрузка конфигурации из файла"""
    config_file = "config.ini"
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Конфигурационный файл {config_file} не найден")
    
    cfg = configparser.ConfigParser()
    cfg.read(config_file)
    return cfg

async def create_config_interactive() -> None:
    """Интерактивное создание конфигурационного файла"""
    print("Для использования нужен config.ini с данными")
    homeserver = input("Введите ссылку на сервер (пример 'https://matrix.org'): ")
    user_id = input("Введите user_id пользователя (пример '@name:matrix.org'): ")
    device_name = input("Введите имя устройства (пример 'matrix-nio'): ")
    password = input("Введите пароль: ")

    temp_client = AsyncClient(homeserver, user_id, ssl=False)
    resp = await temp_client.login(password, device_name=device_name)

    if isinstance(resp, LoginResponse):
        save_config(resp, homeserver, user_id, password)
        print("Вход выполнен, файл config.ini создан")
    else:
        print(f"Не удалось войти: {resp}")
        sys.exit(1)
    await temp_client.close()

def save_config(resp: LoginResponse, homeserver: str, user_id: str, password: str) -> None:
    """Сохранение конфигурации в файл"""
    cfg = configparser.ConfigParser()
    cfg["LOGIN CREDENTIALS"] = {
        'homeserver': homeserver,
        'user_id': user_id,
        'password': password,
        'device_id': resp.device_id,
        'access_token': resp.access_token,
    }
    with open("config.ini", 'w') as configfile:
        cfg.write(configfile)

async def init_matrix_client() -> AsyncClient:
    """Инициализация Matrix клиента"""
    global config
    if not config:
        raise RuntimeError("Конфигурация не загружена")
    
    credentials = config["LOGIN CREDENTIALS"]
    client = AsyncClient(credentials["homeserver"], ssl=False)
    client.access_token = credentials["access_token"]
    client.user_id = credentials["user_id"]
    client.device_id = credentials["device_id"]
    
    return client

def init_web_app() -> FastAPI:
    """Инициализация веб-приложения"""
    app = FastAPI()
    
    @app.get("/image/{url:path}")
    async def get_matrix_image(url: str):
        """Получение и вывод изображения"""
        try:
            log.info(f"Запрос изображения по url: {url}")
            
            mxc_url, filename = await find_mxc_url(client, url)
            log.debug(f"Получена mxc-ссылка: {mxc_url}, имя файла: {filename}")
            
            response = await client.download(mxc_url)
            if not isinstance(response, DownloadResponse):
                log.error(f"Ошибка загрузки изображения. Ответ сервера: {response}")
                raise HTTPException(status_code=500, detail="Ошибка загрузки изображения")
            
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
            log.error(f"Ошибка при обработке запроса {url}: {get_exception_traceback_descr(e)}")
            raise HTTPException(status_code=400, detail="Ошибка обработки запроса изображения")
    
    return app

# ========== Вспомогательные функции ==========

def get_exception_traceback_descr(e: Exception) -> str:
    """Полное описание исключения"""
    if hasattr(e, '__traceback__'):
        return "".join(traceback.format_exception(type(e), e, e.__traceback__))
    return str(e)

def filename_generator(filename: str) -> str:
    """Генерирует случайное имя файла с сохранением расширения"""
    file_type = os.path.splitext(filename)[1]
    random_name = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    return f"{random_name}{file_type if file_type else '.jpeg'}"

async def find_mxc_url(client: AsyncClient, url: str) -> tuple[str, str]:
    """Извлечение mxc-ссылки + имя файла"""
    log.info(f"Извлечение mxc-ссылки из {url}")
    try:
        parts = url.split("/")
        if len(parts) < 2:
            raise ValueError("Неверный формат url. Ожидается !room_id:server.com/$event_id")
        
        room_id = parts[0]
        event_id = parts[1]
        
        response = await client.room_get_event(room_id=room_id, event_id=event_id)
        if not isinstance(response, RoomGetEventResponse):
            log.error("Неверный ответ от сервера Matrix")
            raise ValueError("Неверный ответ от сервера Matrix")
        
        content = response.event.source.get('content', {})
        url_mxc = content.get('url')
        filename = content.get('body', 'image')
        
        if not url_mxc:
            log.error("Не удалось извлечь mxc-ссылку")
            raise ValueError("Не удалось извлечь mxc-ссылку")
            
        return url_mxc, filename_generator(filename)
    except Exception as e:
        log.error(f"Ошибка обработки url: {str(e)}")
        raise ValueError(f"Ошибка обработки url: {str(e)}")

# ========== Основные циклы ==========

async def matrix_loop() -> None:
    """Основной цикл работы с Matrix"""
    global client, log
    log.info("Matrix бот запущен")
    while True:
        try:
            await client.sync(timeout=30000, full_state=True)
        except Exception as e:
            log.error(f"Ошибка синхронизации: {get_exception_traceback_descr(e)}")
            await asyncio.sleep(5)

async def web_server_loop() -> None:
    """Запуск веб-сервера"""
    global web, log, config
    log.info("Веб-сервер запускается")
    
    web_config = Config()
    web_config.bind = ["0.0.0.0:8000"]
    
    try:
        await serve(web, web_config)
    except Exception as e:
        log.critical(f"Ошибка веб-сервера: {get_exception_traceback_descr(e)}")
        raise

# ========== Главная функция ==========

async def main() -> None:
    """Главная функция приложения"""
    global client, web, log, config
    
    try:
        # Инициализация конфигурации и логирования
        log = init_logging()
        try:
            config = load_config()
        except FileNotFoundError:
            log.info("Конфигурационный файл не найден, запуск интерактивного создания")
            await create_config_interactive()
            config = load_config()
        
        # Инициализация компонентов
        client = await init_matrix_client()
        web = init_web_app()
        
        log.info("Приложение инициализировано, запуск основных циклов")
        
        # Запуск основных циклов
        await asyncio.gather(
            matrix_loop(),
            web_server_loop(),
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