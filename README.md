# Matrix Image Bot

**Бот для доступа к изображениям через Matrix API**

Бот решает проблему доступа к изображениям на новых серверах Matrix, где доступ к медиа без аутентификации запрещён из-за изменений в API

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

[WEB]
default_mime_type = image/jpeg
```

4. Запуск
```
python3 main.py
```

ПРИМЕР РАБОТЫ НА КОНКРЕТНОЙ ССЫЛКЕ:
1. Берем ссылку через "Поделиться"
- нажимаем на изображение 
- параметры (Options)
- поделиться (Share)
- скопировать ссылку (Copy link)
ссылка имеет формат:
```
https://matrix.to/#/!EwKGTvmhz####XXqZ:matrix.org/$8TX3Ou####AOa1qHHdjEjd1lR8zYSEIkLkZXkNyr_9k?via=matrix.org
```
- это пригласительная ссылка, но она дает нам полчить:
room_id - номер комнаты, шаблон (!abc:example.org) - в данном примере - !EwKGTvmhz####XXqZ:matrix.org
event_id - номер события (фотография идет как самостоятельное событие), шаблон ($AbC) - в данном примере - $8TX3Ou####AOa1qHHdjEjd1lR8zYSEIkLkZXkNyr_9k

2. Соответственно при запущенном и полностью рабочем веб-сервере:
- нам интересен ЕДИНСТВЕННЫЙ эндпоинт - https://<адрес сервера>/image/
- далее нам необходимо любым способом (в ручную, с помощью скрипта) преобразовать ранее скопированную ссылку в:
```
!EwKGTvmhz####XXqZ:matrix.org/$8TX3Ou####AOa1qHHdjEjd1lR8zYSEIkLkZXkNyr_9k - то есть в набор необходимых аргументов
```
3. Запускаем или подставляем после https://<адрес сервера>/image/ аргументы и получаем:
```
https://<адрес сервера>/image/!EwKGTvmhz####XXqZ:matrix.org/$8TX3Ou####AOa1qHHdjEjd1lR8zYSEIkLkZXkNyr_9k
```

4. В итоге получаем изображение ВАЖНОЕ условие, бот должен находится в той комнате (room_id) откуда мы берем изображение
