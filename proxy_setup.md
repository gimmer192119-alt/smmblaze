# Настройка прокси для обхода блокировок FunPay

Если ваш браузер или провайдер блокирует доступ к FunPay, используйте прокси.

## Способы обхода блокировок

### 1. Бесплатные прокси (для теста)

```json
{
  "http": "http://proxy.server.com:8080",
  "https": "https://proxy.server.com:8080"
}
```

### 2. SOCKS5 прокси (рекомендуется)

```json
{
  "http": "socks5://127.0.0.1:1080",
  "https": "socks5://127.0.0.1:1080"
}
```

### 3. VPN прокси

```json
{
  "http": "http://user:pass@vpn.server.com:8080",
  "https": "https://user:pass@vpn.server.com:8080"
}
```

## Как настроить

1. Откройте файл `config.json`
2. Найдите поле `"funpay_proxy": ""`
3. Вставьте JSON с прокси настройками
4. Перезапустите бота

## Где взять прокси

### Бесплатные:
- https://free-proxy-list.net/
- https://www.proxy-list.download/
- https://github.com/clarketm/proxy-list

### Платные (стабильные):
- Bright Data
- Oxylabs
- Smartproxy

### TOR/SOCKS5:
- Установите TOR Browser
- Используйте локальный SOCKS5 прокси: `socks5://127.0.0.1:9150`

## Пример настройки

```json
{
  "bot_token": "ваш_токен",
  "admin_ids": [123456789],
  "funpay_golden_key": "ваш_golden_key",
  "funpay_proxy": "{\"http\": \"socks5://127.0.0.1:9150\", \"https\": \"socks5://127.0.0.1:9150\"}",
  "twiboost_api_key": "ваш_api_key"
}
```

## Проверка работы

После настройки прокси перезапустите бота. В логах должно появиться:
```
✅ Используется прокси: socks5://127.0.0.1:9150
✅ FunPay: ваш_никнейм (ID: 12345)
```

## Если не работает

1. Проверьте доступность прокси
2. Убедитесь что JSON формат корректный
3. Попробуйте другой прокси
4. Проверьте настройки файрвола
