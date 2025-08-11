import argparse
import asyncio
import configparser
import logging
import mimetypes
import os
import re
import traceback
from io import BytesIO
from logging.handlers import TimedRotatingFileHandler

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from hypercorn.asyncio import serve
from hypercorn.config import Config
from nio import (
    AsyncClient,
    DownloadResponse,
    RoomGetEventResponse,
    JoinError,
    InviteEvent
)

client = None
log = None
web = None
config = None

def setup_logging():
    """Настройка системы логирования на основе конфигурации."""
    global log, config
    
    log_dir = os.path.dirname(config["LOGGING"]["filename"])
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    handler = TimedRotatingFileHandler(
        filename=config["LOGGING"]["filename"],
        when=config["LOGGING"]["when"],
        interval=int(config["LOGGING"]["interval"]),
        backupCount=int(config["LOGGING"]["backupcount"]),
        encoding=config["LOGGING"]["encoding"],
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

async def load_config(config_path):
    """
    Загрузка конфигурации из файла.
    Если файл отсутствует, создает его из шаблона и просит перезапустить приложение.
    """
    global config
    
    parser = configparser.ConfigParser()
    template_path = os.path.join(os.path.dirname(__file__), "config.ini.example")

    if not parser.read(config_path):
        try:
            if not os.path.exists(template_path):
                raise FileNotFoundError(f"Шаблон конфигурации не найден: {template_path}")
            
            with open(template_path, 'r') as template_file:
                template_content = template_file.read()
            
            with open(config_path, "w") as f:
                f.write(template_content)
            
            print(f"Создан новый конфигурационный файл {config_path} из шаблона.")
            print("Пожалуйста, настройте его и перезапустите приложение.")
            exit(0)
            
        except Exception as e:
            raise FileNotFoundError(f"Не удалось создать конфиг {config_path}: {str(e)}")

    required_sections = {
        "LOGIN CREDENTIALS": ["homeserver", "user_id", "password"],
        "LOGGING": ["filename", "when", "interval", "backupCount", "encoding"],
        "INVITE": ["allow_users", "allow_domains", "deny_users", "deny_domains"],
        "WEB": ["default_mime_type"]
    }

    for section, keys in required_sections.items():
        if not parser.has_section(section):
            raise ValueError(f"Отсутствует обязательная секция {section}")
        
        missing_keys = [key for key in keys if key not in parser[section]]
        if missing_keys:
            raise ValueError(f"Отсутствуют обязательные параметры в секции {section}: {', '.join(missing_keys)}")

    config = {}
    for section in parser.sections():
        config[section] = dict(parser[section])
    
    return config

async def initialize_client():
    """Инициализация клиента Matrix с аутентификацией."""
    global client, config, log
    
    try:
        client = AsyncClient(
            homeserver=config["LOGIN CREDENTIALS"]["homeserver"],
            user=config["LOGIN CREDENTIALS"]["user_id"],
            ssl=False
        )

        await client.login(
            password=config["LOGIN CREDENTIALS"]["password"],
            device_name="Matrix Image Bot"
        )

        client.add_event_callback(invite_cb, InviteEvent)

        log.info("Успешный вход в Matrix")
        log.debug(f"User ID: {client.user_id}")

    except Exception as e:
        log.error(f"Ошибка инициализации клиента: {str(e)}")
        raise ConnectionError(f"Ошибка инициализации клиента: {str(e)}")
    
    return client

def create_web_app():
    """Создание FastAPI приложения для обработки запросов изображений."""
    app = FastAPI()

    @app.get("/image/{url:path}")
    async def get_matrix_image(url: str):
        """Получение изображения из Matrix по URL."""
        global client, log
        
        try:
            log.info(f"Запрос изображения по URL: {url}")
            
            mxc_url, filename = await find_mxc_url(client, url)
            log.debug(f"Получена mxc-ссылка: {mxc_url}, имя файла: {filename}. Успешно")
            
            response = await client.download(mxc_url)
            if not isinstance(response, DownloadResponse):
                error_msg = f"Ошибка загрузки изображения. Ответ сервера: {response}"
                log.error(error_msg)
                raise HTTPException(status_code=500, detail=error_msg)
            
            mime_type = get_mime_type(filename)
            if mime_type == "application/octet-stream":
                log.warning(f"Не удалось определить тип файла для {filename}, используется application/octet-stream")
            
            return StreamingResponse(
                BytesIO(response.body),
                media_type=mime_type,
            )
        except HTTPException:
            raise
        except ValueError as e:
            error_msg = f"Некорректный запрос: {str(e)}"
            log.warning(error_msg)
            raise HTTPException(status_code=400, detail=error_msg)
        except Exception as e:
            error_msg = f"Внутренняя ошибка сервера: {get_exception_traceback_descr(e)}"
            log.error(error_msg)
            raise HTTPException(status_code=500, detail="Internal Server Error")

    return app

def check_allow_invite(user):
    """Проверка разрешений для приглашения пользователя в комнату."""
    global config, log
    
    try:
        allow = False

        log.info(f"Проверка разрешений для пользователя: {user}")

        allow_users = [u.strip() for u in config["INVITE"].get("allow_users", "").split() if u.strip()]
        allow_domains = [u.strip() for u in config["INVITE"].get("allow_domains", "").split() if u.strip()]
        deny_users = [u.strip() for u in config["INVITE"].get("deny_users", "").split() if u.strip()]
        deny_domains = [u.strip() for u in config["INVITE"].get("deny_domains", "").split() if u.strip()]

        log.debug(
            f"Параметры проверки: allow_users={allow_users}, "
            f"allow_domains={allow_domains}, deny_users={deny_users}, "
            f"deny_domains={deny_domains}"
        )
        
        if allow_domains:
            for domain in allow_domains:
                if re.search(f'.*:{domain.lower()}$', user.lower()) is not None:
                    allow = True
                    log.info(f"Пользователь {user} из разрешенного домена {domain} - доступ разрешен")
                    break
                if allow_domains == '*':
                    allow = True
                    allow_mask = True
                    log.info("Обнаружен wildcard домен '*' - доступ разрешен по умолчанию")
                    break

        if allow_mask and deny_domains:
            for domain in deny_domains:
                if re.search(f'.*:{domain.lower()}$', user.lower()) is not None:
                    allow = False
                    log.info(f"Пользователь {user} из запрещенного домена {domain} - доступ запрещен")
                    break

        if deny_users:
            for deny_user in deny_users:
                if deny_user.lower() == user.lower():
                    allow = False
                    log.info(f"Пользователь {user} в списке запрещенных - доступ запрещен")
                    break

        if allow_users:
            for allow_user in allow_users:
                if allow_user.lower() == user.lower():
                    allow = True
                    log.info(f"Пользователь {user} в списке разрешенных - доступ разрешен")
                    break

        log.info(f"Результат проверки для {user}: {'разрешен' if allow else 'запрещен'}")
        
        return allow
        
    except Exception as e:
        error_msg = f"Ошибка проверки разрешений: {get_exception_traceback_descr(e)}"
        log.error(error_msg)
        return False

async def invite_cb(room, event):
    """Callback для обработки приглашений в комнаты Matrix."""
    global client, log
    try:
        log.info(f"Получено приглашение от {event.sender} в комнату {room.room_id}")
        log.debug(f"Детали события: {vars(event)}")
        
        if not check_allow_invite(event.sender):
            log.warning(f"Доступ запрещён для {event.sender}")
            return False
        
        log.info(f"Принимаем приглашение от {event.sender}")
        resp = await client.join(room.room_id)
        
        if isinstance(resp, JoinError):
            log.error(f"Ошибка входа: {resp.message}")
            return False
        
        log.info(f"Успешно присоединились к комнате {room.room_id}")
        return True
        
    except Exception as e:
        log.error(f"Ошибка обработки приглашения: {get_exception_traceback_descr(e)}")
        return False

def get_exception_traceback_descr(e):
    """Получение полного описания исключения с трейсбэком."""
    if hasattr(e, '__traceback__'):
        return "".join(traceback.format_exception(type(e), e, e.__traceback__))
    return str(e)

def get_mime_type(filename):
    """Определение MIME-типа файла по его имени."""
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "application/octet-stream"

async def find_mxc_url(client, url):
    """Извлечение mxc-ссылки и имени файла из URL Matrix события."""
    global log
    
    try:
        log.info(f"Извлечение mxc-ссылки из {url}")
        
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
            
        return url_mxc, filename
        
    except Exception as e:
        error_msg = f"Ошибка обработки URL: {str(e)}"
        log.error(error_msg)
        raise ValueError(error_msg)

async def check_connection(client):
    """Проверка соединения с сервером Matrix."""
    try:
        await client.sync(timeout=5000)
        return True
    except Exception as e:
        if "M_UNKNOWN_TOKEN" in str(e):
            log.warning("Обнаружен невалидный токен, требуется переаутентификация")
            return False
        log.warning(f"Ошибка проверки соединения: {str(e)}")
        return False

async def run_matrix_bot():
    """Основной цикл работы Matrix бота."""
    global client
    log.info("Matrix бот запущен")
    
    while True:
        try:
            if not await check_connection(client):
                log.warning("Проблема с соединением или аутентификацией, переподключение")
                await client.close()
                client = await initialize_client()
                continue
            
            sync_response = await client.sync(timeout=30000, full_state=True)
            if hasattr(sync_response, 'next_batch'):
                log.debug(f"Успешная синхронизация, next_batch: {sync_response.next_batch}")
            else:
                log.warning(f"Проблема с синхронизацией: {sync_response}")
        except asyncio.CancelledError:
            log.info("Синхронизация остановлена по запросу")
            break
        except Exception as e:
            log.error(f"Ошибка синхронизации: {str(e)}")
            await asyncio.sleep(5)

async def run_web_server():
    """Запуск веб-сервера FastAPI."""
    global web, log
    
    log.info("Запуск веб-сервера")
    
    server_config = Config()
    server_config.bind = ["0.0.0.0:8000"]
    
    try:
        await serve(web, server_config)
    except Exception as e:
        log.critical(f"Ошибка веб-сервера: {get_exception_traceback_descr(e)}")
        raise

async def main():
    """Основная функция инициализации и запуска приложения."""
    global log, config, client, web

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    temp_log = logging.getLogger("init")
    
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--config", default="config.ini", help="Path to config file")
        args = parser.parse_args()
        
        temp_log.info(f"Загрузка конфигурации из {args.config}")

        await load_config(args.config)
        
        setup_logging()
        log = logging.getLogger()
        
        log.info("Конфигурация загружена, инициализация компонентов...")
        
        client = await initialize_client()
        web = create_web_app()
        
        log.info("Инициализация завершена успешно, запуск сервисов")

        await asyncio.gather(
            run_web_server(),
            run_matrix_bot(),
        )
        
    except asyncio.CancelledError:
        log.info("Приложение остановлено по запросу")
    except Exception as e:
        error_msg = f"Критическая ошибка: {get_exception_traceback_descr(e)}"
        log.critical(error_msg)
        raise
    finally:
        if client:
            await client.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger().info("Приложение остановлено по сигналу KeyboardInterrupt")
    except Exception as e:
        logging.getLogger().critical(f"Фатальная ошибка: {str(e)}")