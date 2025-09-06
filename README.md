# Matrix Image Bot

**Bot for accessing images via Matrix API**

The bot solves the problem of accessing images on new Matrix servers where media access without authentication is prohibited due to API changes.

## nstallation and Launch

1. Clone the repository:
```bash
git clone https://github.com/erkdot/matrix-bot_show-image.git
cd matrix-bot_show-image
```

2. Install dependencies:
```
pip install -r requirements.txt
```

3. Configure the settings (config.ini):
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

4. Launch
```
python3 main.py
```

WORKING EXAMPLE WITH A SPECIFIC LINK:
1. Get the link via "Share"
- Click on the image
- Options
- Share
- Copy link
- The link has the format:
```
https://matrix.to/#/!EwKGTvmhz####XXqZ:matrix.org/$8TX3Ou####AOa1qHHdjEjd1lR8zYSEIkLkZXkNyr_9k?via=matrix.org
```
- This is an invite link, but it allows us to get:
- room_id - room number, template (!abc:example.org) - in this example - !EwKGTvmhz####XXqZ:matrix.org
- event_id - event number (the photo is treated as a separate event), template ($AbC) - in this example - $8TX3Ou####AOa1qHHdjEjd1lR8zYSEIkLkZXkNyr_9k

2. Accordingly, with a running and fully operational web server:
- We are interested in the SINGLE endpoint - https://<server address>/image/
- Next, we need to somehow (manually or using a script) convert the previously copied link into:
```
!EwKGTvmhz####XXqZ:matrix.org/$8TX3Ou####AOa1qHHdjEjd1lR8zYSEIkLkZXkNyr_9k - то есть в набор необходимых аргументов
```
3. We launch or substitute the arguments after https://<server address>/image/ and get:
```
https://<адрес сервера>/image/!EwKGTvmhz####XXqZ:matrix.org/$8TX3Ou####AOa1qHHdjEjd1lR8zYSEIkLkZXkNyr_9k
```

4. As a result, we get the image. IMPORTANT condition: the bot must be in the same room (room_id) from which we are taking the image.
