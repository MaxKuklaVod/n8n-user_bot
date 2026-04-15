"""
Telegram User Bot API Bridge
------------------------------------------------------
Специализированный микросервис для интеграции n8n с Telegram Client API.
Реализует мультимодальное взаимодействие (текст, голос, фото), 
асинхронную оркестрацию очередей и глубокую имитацию человеческого поведения.
"""

import uvicorn
import httpx
import asyncio
import time
import os
from asyncio import Queue
from typing import Dict
from pydantic import BaseModel
from PIL import Image
from pydub import AudioSegment
from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Form
from telethon import TelegramClient, events
from telethon.tl.functions.contacts import ImportContactsRequest, BlockRequest, UnblockRequest
from telethon.tl.types import InputPhoneContact
from telethon.tl.functions.messages import DeleteHistoryRequest

# ==============================================================================
# 1. КОНФИГУРАЦИЯ И НАСТРОЙКИ
# ==============================================================================
API_ID = 'Ваш ID'
API_HASH = 'Ваш Api Hash'
SESSION_NAME = 'Имя первой сессии'

# Глобальный URL для связи с логикой в n8n
N8N_WEBHOOK_URL = "Ваш n8n URL"

# Пути к файлам атмосферного фона
SAND_FILE = "sand.aiff"
WOOD_FILE = "wood.wav"

# ==============================================================================
# 2. УПРАВЛЕНИЕ СОСТОЯНИЕМ И ОЧЕРЕДЯМИ
# ==============================================================================
user_queues: Dict[int, Queue] = {}
active_workers: set[int] = set()
last_interaction_time: Dict[int, float] = {}

class MessageRequest(BaseModel):
    chat_id: str | int
    text: str

class ImportRequest(BaseModel):
    phone: str
    first_name: str

app = FastAPI()
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ==============================================================================
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ОБРАБОТКИ
# ==============================================================================
def loop_to_match(sound, target_length):
    """Зацикливает аудио под нужную длительность."""
    if len(sound) < target_length:
        loops = (target_length // len(sound)) + 1
        return (sound * loops)[:target_length]
    return sound[:target_length]

async def message_worker(chat_id: int):
    """Обрабатывает очередь исходящих сообщений с имитацией печати."""
    print(f"--- [ВОРКЕР {chat_id}] Активирован", flush=True)
    queue = user_queues[chat_id]
    while not queue.empty():
        text = await queue.get()
        try:
            # Расчет задержки (человеческая скорость)
            chars_per_second = 16
            delay = len(text) / chars_per_second
            min_delay, max_delay = 2.0, 60.0
            final_delay = min(max_delay, max(min_delay, delay))
            
            print(f"--- [ВОРКЕР {chat_id}] Имитация печати: {final_delay:.2f} сек", flush=True)
            async with client.action(chat_id, 'typing'):
                await asyncio.sleep(final_delay)
                await client.send_message(chat_id, text)
            print(f"--- [ВОРКЕР {chat_id}] Текст успешно отправлен", flush=True)
        except Exception as e:
            print(f"--- [ОШИБКА ВОРКЕРА] {e}", flush=True)
        finally:
            queue.task_done()
    active_workers.remove(chat_id)
    print(f"--- [ВОРКЕР {chat_id}] Деактивирован", flush=True)

# ==============================================================================
# 4. API ЭНДПОИНТЫ (n8n -> Telegram)
# ==============================================================================

@app.post("/import_contact")
async def import_contact(request: ImportRequest):
    """Авторизует пользователя и снимает блокировку для начала игры."""
    try:
        contact = InputPhoneContact(client_id=0, phone=request.phone, first_name=request.first_name, last_name="")
        await client(ImportContactsRequest([contact]))
        try:
            await client(UnblockRequest(request.phone))
            print(f"--- [API] Контакт {request.phone} импортирован и разблокирован", flush=True)
        except: pass
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/send_message")
async def send_message(request: MessageRequest, background_tasks: BackgroundTasks):
    """Стандартный эндпоинт для отправки текста через очередь."""
    chat_id = int(request.chat_id)
    if chat_id not in user_queues: user_queues[chat_id] = Queue()
    await user_queues[chat_id].put(request.text)
    if chat_id not in active_workers:
        active_workers.add(chat_id)
        background_tasks.add_task(message_worker, chat_id)
    return {"status": "success"}

@app.post("/send_voice")
async def send_voice(chat_id: int = Form(...), file: UploadFile = File(...), panic: int = Form(3)):
    """Синтезирует финальное аудио с наложением атмосферных шумов и фильтров."""
    try:
        raw_path = f"raw_{chat_id}.ogg"
        processed_path = f"final_{chat_id}.opus"
        with open(raw_path, "wb") as buffer:
            buffer.write(await file.read())

        voice = AudioSegment.from_file(raw_path)
        
        # Эффект "Глухого пространства" (Микро-эхо 30мс)
        echo = voice - 15
        voice = voice.overlay(echo, position=30)
        
        # Эффект "За стеной" (Фильтрация частот)
        voice = (voice - 10).low_pass_filter(2200).high_pass_filter(250)

        if os.path.exists(SAND_FILE) and os.path.exists(WOOD_FILE):
            sand = AudioSegment.from_file(SAND_FILE) - 20
            wood = AudioSegment.from_file(WOOD_FILE) - 15
            bg_final = loop_to_match(sand.overlay(wood), len(voice))
            final_audio = bg_final.overlay(voice)
        else:
            final_audio = voice

        final_audio.export(processed_path, format="opus", codec="libopus", bitrate="16k")
        await client.send_file(chat_id, processed_path, voice=True)
        
        os.remove(raw_path)
        os.remove(processed_path)
        print(f"--- [УСПЕХ] Голосовое отправлено в {chat_id}", flush=True)
        return {"status": "success"}
    except Exception as e:
        print(f"--- [ОШИБКА АУДИО] {e}", flush=True)
        return {"status": "error", "message": str(e)}

@app.post("/send_photo")
async def send_photo(chat_id: int = Form(...), file: UploadFile = File(...), caption: str = Form("")):
    """Принудительно ухудшает качество фото для имитации камеры 2006 года."""
    try:
        temp_path = f"raw_p_{chat_id}.jpg"
        final_path = f"shakal_{chat_id}.jpg"
        with open(temp_path, "wb") as f: f.write(await file.read())

        with Image.open(temp_path) as img:
            img = img.convert("RGB")
            # Downscaling: 480p с жестким пиксельным ресайзом
            img = img.resize((640, 480), resample=Image.NEAREST)
            # Сжатие: экстремально низкое качество для артефактов
            img.save(final_path, "JPEG", quality=15)

        await client.send_file(chat_id, final_path, caption=caption)
        os.remove(temp_path)
        os.remove(final_path)
        print(f"--- [УСПЕХ] Фото отправлено в {chat_id}", flush=True)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/delete_session")
async def delete_session(request: MessageRequest):
    """Реализует протокол полной зачистки и временной блокировки."""
    try:
        chat_id = int(request.chat_id)
        await client(BlockRequest(chat_id))
        await client(DeleteHistoryRequest(peer=chat_id, max_id=0, just_clear=False, revoke=True))
        await client.delete_dialog(chat_id)
        
        async def delayed_unblock(cid):
            await asyncio.sleep(5)
            try: await client(UnblockRequest(cid))
            except: pass
        asyncio.create_task(delayed_unblock(chat_id))
        print(f"--- [API] Сессия {chat_id} уничтожена", flush=True)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==============================================================================
# 5. ОБРАБОТЧИК СОБЫТИЙ ТЕЛЕГРАМ (Telegram -> n8n)
# ==============================================================================
@client.on(events.NewMessage(incoming=True))
async def n8n_trigger_handler(event):
    if event.is_private and not event.message.out:
        sender = await event.get_sender()
        chat_id = sender.id
        current_time = time.time()

        if chat_id not in last_interaction_time or (current_time - last_interaction_time[chat_id]) > 120:
            print(f"--- [ВХОД] Пауза 10с (неактивность {sender.first_name})", flush=True)
            await asyncio.sleep(10)
         
        last_interaction_time[chat_id] = current_time
        await client.send_read_acknowledge(event.chat_id, event.message)

        text_content = event.message.text or ""
        files = None
        if event.message.voice:
            voice_data = await event.message.download_media(file=bytes)
            files = {'file': ('voice.ogg', voice_data, 'audio/ogg')}

        try: 
            async with httpx.AsyncClient() as client_http:
                if files:
                    await client_http.post(N8N_WEBHOOK_URL, data={"chat_id": chat_id, "text": text_content, "is_voice": "true"}, files=files, timeout=30.0)
                else:
                    await client_http.post(N8N_WEBHOOK_URL, json={"chat_id": chat_id, "text": text_content, "is_voice": "false"})
        except Exception as e:
            print(f"--- [ОШИБКА N8N] {e}", flush=True)

# ==============================================================================
# 6. ЖИЗНЕННЫЙ ЦИКЛ ПРИЛОЖЕНИЯ
# ==============================================================================
@app.on_event("startup")
async def startup_event():
    await client.start()
    print("--- [SYSTEM] API МОСТ ЗАПУЩЕН И СЛУШАЕТ СОБЫТИЯ ---", flush=True)

@app.on_event("shutdown")
async def shutdown_event():
    await client.disconnect()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5000)
