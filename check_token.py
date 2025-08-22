import requests

# вставь свой настоящий токен сюда!
TOKEN = "8405327847:AAHOfjx7LboM6WqKV0PeowL3zVefmQCS7d4"

url = f"https://api.telegram.org/bot{TOKEN}/getMe"
response = requests.get(url)

print("Ответ Telegram API:")
print(response.text)
