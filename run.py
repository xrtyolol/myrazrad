import threading
import logging
import sys
import os
from web.app import app, socketio, bot, db, User
import config
from datetime import datetime

# Создаем свой handler для перехвата логов
class SocketIOHandler(logging.Handler):
    def emit(self, record):
        # Игнорируем все системные сообщения
        if any(name in record.name for name in ['werkzeug', 'socketio', 'engineio']):
            return
        if record.levelno < logging.INFO:
            return
            
        try:
            msg = self.format(record)
            
            # Определяем тип лога и форматируем сообщение
            if 'discord' in record.name:
                log_type = 'BOT'
                action = record.levelname
            elif record.levelno >= logging.ERROR:
                log_type = 'ERROR'
                action = record.levelname
            else:
                log_type = 'PANEL'
                action = record.levelname

            socketio.emit('new_log', {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'type': log_type,
                'action': action,
                'details': msg
            }, namespace='/')
            # Выводим в консоль через print
            print(msg)
        except Exception:
            pass

# Настраиваем логирование
class CustomFormatter(logging.Formatter):
    """Кастомный форматтер для логов"""
    
    # Цвета для консоли
    grey = "\x1b[38;21m"
    blue = "\x1b[38;5;39m"
    yellow = "\x1b[38;5;226m"
    red = "\x1b[38;5;196m"
    green = "\x1b[38;5;40m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"

    # Форматы для разных уровней
    FORMATS = {
        logging.DEBUG: grey + "[DEBUG] %(message)s" + reset,
        logging.INFO: blue + "[INFO] %(message)s" + reset,
        logging.WARNING: yellow + "[WARN] %(message)s" + reset,
        logging.ERROR: red + "[ERROR] %(message)s" + reset,
        logging.CRITICAL: bold_red + "[CRIT] %(message)s" + reset
    }

    def format(self, record):
        # Игнорируем системные сообщения
        if any(name in record.name for name in ['werkzeug', 'socketio', 'engineio', 'discord.client']):
            return ""
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

# Настраиваем логирование
logging.basicConfig(level=logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setFormatter(CustomFormatter())

# Получаем корневой логгер и настраиваем его
root_logger = logging.getLogger()
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
root_logger.addHandler(console_handler)

# Отключаем все системные логи
logging.getLogger('werkzeug').disabled = True
logging.getLogger('discord').setLevel(logging.ERROR)
logging.getLogger('discord.http').disabled = True
logging.getLogger('discord.client').disabled = True
logging.getLogger('discord.gateway').disabled = True

# Настройка логгера
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Создаем таблицы при запуске
with app.app_context():
    db.create_all()
    logger.info("База данных инициализирована")

# Настраиваем логгеры для различных компонентов
logger = logging.getLogger(__name__)

# Настраиваем логгер Discord
discord_logger = logging.getLogger('discord')
discord_logger.setLevel(logging.INFO)

def run_flask():
    """Запуск Flask сервера"""
    try:
        logger.info("Запуск веб-сервера на http://localhost:5000")
        socketio.run(app, host='0.0.0.0', port=5000, debug=True, use_reloader=False, log_output=True)
    except Exception as e:
        logger.error(f"Ошибка запуска веб-сервера: {e}")
        raise

def run_bot():
    """Запуск Discord бота"""
    try:
        logger.info("Запуск Discord бота")
        
        @bot.event
        async def on_ready():
            logger.info(f"Бот {bot.user} готов к работе!")
            try:
                synced = await bot.tree.sync()
                logger.info(f"Синхронизировано {len(synced)} команд!")
            except Exception as e:
                logger.error(f"Ошибка синхронизации команд: {e}")
        
        bot.run(config.TOKEN, log_handler=None)
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
        raise

if __name__ == '__main__':
    try:
        # Создаем таблицы при запуске
        with app.app_context():
            db.create_all()
            logger.info("База данных инициализирована")
        
        logger.info("Инициализация приложения...")
        
        # Запускаем бота в отдельном потоке
        bot_thread = threading.Thread(target=run_bot)
        bot_thread.start()
        
        # Запускаем веб-сервер
        run_flask()
        
    except KeyboardInterrupt:
        logger.info("Приложение остановлено пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        raise 