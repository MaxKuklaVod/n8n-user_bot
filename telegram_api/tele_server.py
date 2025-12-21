"""
Микросервис для интеграции визуальной платформы n8n с Telegram Client API.
Реализует асинхронную обработку сообщений, очереди для пользователей
и алгоритмы имитации человеческого поведения.
"""

import uvicorn
import httpx
import asyncio
import time
from asyncio import Queue
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from telethon import TelegramClient, events
from typing import Dict

# 1. Настройки подключения
# Данные для аутентификации в Telegram (Client API)
API_ID = 'Ваш ID'
API_HASH = 'Ваш Api Hash'
SESSION_NAME = 'Имя первой сессии'

# URL вебхука n8n (Production), куда будут пересылаться входящие сообщения
N8N_WEBHOOK_URL = "Ваш n8n URL"


# 2. Управление состоянием и очередью
# Очереди сообщений для каждого пользователя.
# Необходимы для предотвращения "состояния гонки", когда короткие ответы ИИ
# отправляются быстрее длинных, нарушая хронологию диалога.
user_queues: Dict[int, Queue] = {}

# Множество активных воркеров. Гарантирует, что для одного чата
# работает только один обработчик в моменте времени.
active_workers: set[int] = set()

# Словарь для хранения времени последнего взаимодействия (timestamp).
# Используется для расчета логики "отложенного прочтения".
last_interaction_time: Dict[int, float] = {}

# Модель данных для валидации входящих HTTP-запросов от n8n
class MessageRequest(BaseModel):
    chat_id: str | int
    text: str

# Инициализация приложений
app = FastAPI()
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# 3. Лоика отправки и имитация человека
async def message_worker(chat_id: int):
    """
    Асинхронный воркер, обрабатывающий очередь исходящих сообщений для конкретного чата.
    Реализует имитацию набора текста.
    """
    queue = user_queues[chat_id]

    while not queue.empty():
        text = await queue.get()
        try:
            # Алгоритм расчиета задержки
            # Средняя скорость печати: ~8 символов в секунду.
            # Ограничиваем задержку рамками: минимум 2 сек, максимум 60 сек.
            chars_per_second = 8
            delay = len(text) / chars_per_second
            min_delay = 2.0
            max_delay = 60.0
            final_delay = min(max_delay, max(min_delay, delay))

            # Устанавливаем статус "Печатает..." на время расчетной задержки
            async with client.action(chat_id, 'typing'):
                await asyncio.sleep(final_delay)
                await client.send_message(chat_id, text)

        except Exception as e:
            print(f"[Ошибка воркера {chat_id}] {e}")
        finally:
            queue.task_done()
    active_workers.remove(chat_id)


# 4. Обработчик входящих сообщений (Telegram -> n8n)
@client.on(events.NewMessage(incoming=True))
async def n8n_trigger_handler(event):
    """
    Перехватывает личные сообщения, реализует логику "умного прочтения"
    и пересылает данные в n8n.
    """
    if event.is_private and not event.message.out:
        sender = await event.get_sender()
        chat_id = sender.id
        current_time = time.time()

        # Логика отложенного прочтения
        # Если пользователь молчал более 2 минут (120 сек), бот не читает сообщение сразу.
        # Имитируется задержка "человека, который не держит телефон в руках".
        if chat_id not in last_interaction_time or (current_time - last_interaction_time[chat_id]) > 120:
            await asyncio.sleep(10)

        # Обновляем timestamp активности
        last_interaction_time[chat_id] = current_time

        # Отправляем подтверждение прочтения (две галочки)
        await client.send_read_acknowledge(event.chat_id, event.message)
        print(f"Сообщение от {sender.first_name} прочитано. Отправляю в n8n...")

        # Формируем payload и отправляем в n8n
        payload = {"chat_id": sender.id, "text": event.message.text}
        try:
            async with httpx.AsyncClient() as client_http:
                await client_http.post(N8N_WEBHOOK_URL, json=payload)
        except Exception as e:
            print(f"[ОШИБКА] Не удалось отправить вебхук в n8n: {e}")


# 5. API шлюз (n8n -> Telegram)
@app.post("/send_message")
async def send_message(request: MessageRequest, background_tasks: BackgroundTasks):
    """
    Эндпоинт, принимающий команды от n8n.
    Не блокирует поток, добавляя задачу отправки в фон.
    """
    chat_id = int(request.chat_id)
    text = request.text

    # Инициализация очереди для пользователя, если её нет
    if chat_id not in user_queues:
        user_queues[chat_id] = Queue()

    # Добавление сообщения в очередь
    await user_queues[chat_id].put(text)

    # Запуск воркера в фоне, если он еще не активен
    if chat_id not in active_workers:
        active_workers.add(chat_id)
        background_tasks.add_task(message_worker, chat_id)

    return {"status": "success", "message": "Message queued for sending"}

# 6. Управление жизненным циклом
@app.on_event("startup")
async def startup_event():
    await client.start()

@app.on_event("shutdown")
async def shutdown_event():
    await client.disconnect()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5000)
