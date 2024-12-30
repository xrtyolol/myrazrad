import sys
import logging
from datetime import datetime
from flask_socketio import SocketIO

class WebConsoleHandler(logging.Handler):
    def __init__(self, socketio):
        super().__init__()
        self.socketio = socketio

    def emit(self, record):
        try:
            msg = self.format(record)
            self.socketio.emit('console_log', {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'level': record.levelname,
                'message': msg
            })
        except Exception:
            self.handleError(record)

class ConsoleToWeb:
    def __init__(self, socketio):
        self.socketio = socketio
        self.stdout = sys.stdout
        self.stderr = sys.stderr

    def write(self, message):
        if message.strip():  # Игнорируем пустые строки
            self.stdout.write(message)
            self.socketio.emit('console_log', {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'level': 'INFO',
                'message': message.strip()
            })

    def flush(self):
        self.stdout.flush() 