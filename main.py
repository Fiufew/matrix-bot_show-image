import asyncio
import configparser
import logging
import mimetypes
import os
import random
import string
import traceback
from io import BytesIO

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from hypercorn.asyncio import serve
from hypercorn.config import Config
from nio import (
    AsyncClient,
    DownloadResponse,
    RoomGetEventResponse,
)
from logging.handlers import TimedRotatingFileHandler


client = None
log = None
web = None
config = None


def setup_logging(config_file = "config.ini"):
    """Инициализация и настройка системы логирования."""
    global log
    
    parser = configparser.ConfigParser()
    if not parser.read(config_file):
        raise FileNotFoundError(f"Конфигурационный файл {config_file} не найден")

    log_dir = os.path.dirname(parser["LOGGING"]["filename"])
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    handler = TimedRotatingFileHandler(
        filename=parser["LOGGING"]["filename"],
        when=parser["LOGGING"]["when"],
        interval=int(parser["LOGGING"]["interval"]),
        backupCount=int(parser["LOGGING"]["backupCount"]),
        encoding=parser["LOGGING"]["encoding"],
    )

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(filename)s:%(lineno)d - %(funcName)s() %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    logging.getLogger("nio").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    log = logger
    return logger


async def load_config():
    """Загрузка конфигурации из файла."""
    config_file = "config.ini"
    parser = configparser.ConfigParser()
    
    if not parser.read(config_file):
        raise FileNotFoundError(f"Конфигурационный файл {config_file} не найден")
    
    if not parser.has_section("LOGIN CREDENTIALS"):
        raise ValueError("В конфигурационном файле отсутствует секция LOGIN CREDENTIALS")
    
    required_keys = ['homeserver', 'user_id', 'password']
    missing_keys = [key for key in required_keys if key not in parser["LOGIN CREDENTIALS"]]
    if missing_keys:
        raise ValueError(f"Отсутствуют обязательные параметры в конфигурации: {', '.join(missing_keys)}")
    
    return {
        "homeserver": parser["LOGIN CREDENTIALS"]["homeserver"],
        "user_id": parser["LOGIN CREDENTIALS"]["user_id"],
        "password": parser["LOGIN CREDENTIALS"]["password"],
    }


async def initialize_client():
    """Инициализация клиента Matrix с аутентификацией по паролю."""
    global client, config, log
    
    if client is None:
        if config is None:
            config = await load_config()
        
        try:
            client = AsyncClient(
                homeserver=config["homeserver"],
                user=config["user_id"],
                ssl=False
            )

            await client.login(
                password=config["password"],
                device_name="Matrix Image Bot"
            )


            if log:
                log.info("Успешный вход в Matrix по паролю")
                log.debug(f"User ID: {client.user_id}")

        except Exception as e:
            error_msg = f"Ошибка инициализации клиента: {str(e)}"
            if log:
                log.error(error_msg)
            raise ConnectionError(error_msg)
    
    return client


def create_web_app():
    """Создание и настройка FastAPI приложения."""
    app = FastAPI()

    @app.get("/image/{url:path}")
    async def get_matrix_image(url: str):
        """Обработчик запросов изображений."""
        global client, log
        
        try:
            if log:
                log.info(f"Запрос изображения по URL: {url}")
            
            mxc_url, filename = await find_mxc_url(client, url)
            if log:
                log.debug(f"Получена mxc-ссылка: {mxc_url}, имя файла: {filename}")
            
            response = await client.download(mxc_url)
            if not isinstance(response, DownloadResponse):
                error_msg = f"Ошибка загрузки изображения. Ответ сервера: {response}"
                if log:
                    log.error(error_msg)
                raise HTTPException(status_code=500, detail=error_msg)
            
            mime_type, _ = mimetypes.guess_type(filename)
            if mime_type is None:
                mime_type = "image/jpeg"
                if log:
                    log.warning(f"Не удалось определить тип файла для {filename}, используется image/jpeg")
            
            return StreamingResponse(
                BytesIO(response.body),
                media_type=mime_type,
            )
            
        except HTTPException:
            raise
        except Exception as e:
            error_msg = f"Ошибка обработки запроса {url}: {get_exception_traceback_descr(e)}"
            if log:
                log.error(error_msg)
            raise HTTPException(status_code=400, detail=error_msg)

    return app


def get_exception_traceback_descr(e):
    """Форматирование описания исключения."""
    if hasattr(e, '__traceback__'):
        return "".join(traceback.format_exception(type(e), e, e.__traceback__))
    return str(e)


def generate_filename(original_name):
    """Генерация случайного имени файла с сохранением расширения."""
    file_ext = os.path.splitext(original_name)[1]
    random_name = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    return f"{random_name}{file_ext if file_ext else '.jpeg'}"


async def find_mxc_url(client, url):
    """Извлечение mxc-ссылки и имени файла из URL."""
    global log
    
    try:
        if log:
            log.info(f"Извлечение mxc-ссылки из {url}")
        
        parts = url.split("/")
        if len(parts) < 2:
            raise ValueError("Неверный формат URL. Ожидается !room_id:server.com/$event_id")
        
        room_id, event_id = parts[0], parts[1]
        
        response = await client.room_get_event(room_id=room_id, event_id=event_id)
        if not isinstance(response, RoomGetEventResponse):
            error_msg = "Неверный ответ от сервера Matrix"
            if log:
                log.error(error_msg)
            raise ValueError(error_msg)
        
        content = response.event.source.get('content', {})
        url_mxc = content.get('url')
        filename = content.get('body', 'image')
        
        if not url_mxc:
            error_msg = "Не удалось извлечь mxc-ссылку"
            if log:
                log.error(error_msg)
            raise ValueError(error_msg)
            
        return url_mxc, generate_filename(filename)
        
    except Exception as e:
        error_msg = f"Ошибка обработки URL: {str(e)}"
        if log:
            log.error(error_msg)
        raise ValueError(error_msg)

async def check_connection(client):
    try:
        await client.sync(timeout=5000)
        return True
    except Exception as e:
        if "M_UNKNOWN_TOKEN" in str(e):
            if log:
                log.warning("Обнаружен невалидный токен, требуется переаутентификация")
            return False
        if log:
            log.warning(f"Ошибка проверки соединения: {str(e)}")
        return False


async def run_matrix_bot():
    global client
    
    if log:
        log.info("Matrix бот запущен")
    
    while True:
        try:
            if not await check_connection(client):
                if log:
                    log.warning("Проблема с соединением или аутентификацией, переподключаемся...")
                await client.close()
                client = await initialize_client()
                continue
                
            sync_response = await client.sync(timeout=30000, full_state=True)
            
            if hasattr(sync_response, 'next_batch'):
                if log:
                    log.debug(f"Успешная синхронизация, next_batch: {sync_response.next_batch}")
            else:
                if log:
                    log.warning(f"Проблема с синхронизацией: {sync_response}")

        except asyncio.CancelledError:
            if log:
                log.info("Синхронизация остановлена по запросу")
            break
        except Exception as e:
            if log:
                log.error(f"Ошибка синхронизации: {str(e)}")
            await asyncio.sleep(5)


async def run_web_server():
    """Запуск веб-сервера."""
    global web, log
    
    if log:
        log.info("Запуск веб-сервера")
    
    server_config = Config()
    server_config.bind = ["0.0.0.0:8000"]
    
    try:
        await serve(web, server_config)
    except Exception as e:
        if log:
            log.critical(f"Ошибка веб-сервера: {get_exception_traceback_descr(e)}")
        raise


async def main():
    """Основная функция приложения."""
    global log, config, client, web
    
    try:
        log = setup_logging()
        config = await load_config()
        client = await initialize_client()
        web = create_web_app()
        
        if log:
            log.info("Инициализация завершена успешно")

        await asyncio.gather(
            run_web_server(),
            run_matrix_bot(),
        )
        
    except asyncio.CancelledError:
        if log:
            log.info("Приложение остановлено по запросу")
    except Exception as e:
        if log:
            log.critical(f"Критическая ошибка: {get_exception_traceback_descr(e)}")
        raise
    finally:
        if client:
            await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Приложение остановлено по сигналу KeyboardInterrupt")
    except Exception as e:
        log.critical(f"Необработанное исключение: {get_exception_traceback_descr(e)}")
