# Matrix Image Bot

**Бот для доступа к изображениям через Matrix API**

Бот предоставляет альтернативный способ просмотра изображений из Matrix, когда стандартная функция "Поделиться" недоступна.

## Установка и запуск

1. Клонируйте репозиторий:
```bash
git clone https://github.com/erkdot/matrix-bot_show-image.git
cd matrix-bot_show-image
```

2. Установите зависимости:
```
pip install -r requirements.txt
```

3. Настройте конфигурацию (config.ini):
```
[LOGIN CREDENTIALS]
homeserver = https://example.domain
user_id = @example:domain_name.domain
password = example

[LOGGING]
filename = logs/matrix_bot.log  # Путь к файлу логов
when = midnight
interval = 1
backupCount = 10
encoding = utf-8

[INVITE]
allow_users = @good_user1:spammers.com @good_user2:matrix.org
allow_domains = *
deny_users = @baduser:matrix.org @baduser2:tt.net
deny_domains = spammers.com spammers2.net
```

4. Запуск
```
python3 main.py
```

5.Отправьте запрос на:
```
http://ваш-сервер/image/!room_id:server.com/$event_id
```

пример:
```
http://localhost:8000/image/!abcdefg:matrix.org/$1234567890
```