from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, 
    login_required, 
    current_user, 
    UserMixin, 
    login_user, 
    logout_user
)
import discord
from discord.ext import commands
from discord import app_commands
import os
import requests
from datetime import datetime, timedelta, timezone
import json
import logging  # Добавляем импорт logging
from urllib.parse import quote
from dotenv import load_dotenv
from web.console_handler import WebConsoleHandler, ConsoleToWeb
import sys
from flask_cors import CORS

# Загружаем переменные окружения из .env файла
load_dotenv()

# Настройка логгера
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация Discord OAuth2
DISCORD_CLIENT_ID = os.getenv('DISCORD_CLIENT_ID')
DISCORD_CLIENT_SECRET = os.getenv('DISCORD_CLIENT_SECRET')
DISCORD_REDIRECT_URI = os.getenv('DISCORD_REDIRECT_URI', 'http://localhost:5000/callback')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'generate-strong-secret-key')
CORS(app)

# Правильный путь к базе данных
db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, 
                   cors_allowed_origins="*", 
                   logger=False,  # Отключаем логгер Socket.IO
                   engineio_logger=False)  # Отключаем логгер Engine.IO
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Установка московского времени
MSK = timezone(timedelta(hours=3))

def get_msk_time():
    return datetime.now(MSK)

# Модели
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    discord_id = db.Column(db.String(20), unique=True)
    username = db.Column(db.String(100))
    avatar = db.Column(db.String(200))
    is_admin = db.Column(db.Boolean, default=False)
    access_token = db.Column(db.String(100))
    refresh_token = db.Column(db.String(100))

    def get_id(self):
        return str(self.id)

class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    guild_id = db.Column(db.String(20))
    channel_id = db.Column(db.String(20))
    creator_id = db.Column(db.String(20))
    subject = db.Column(db.String(100))
    status = db.Column(db.String(20), default='open')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    closed_at = db.Column(db.DateTime, nullable=True)

class Log(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    type = db.Column(db.String(20))  # BOT, PANEL, ERROR, TICKET
    action = db.Column(db.String(100))
    details = db.Column(db.Text)
    user_id = db.Column(db.String(20), nullable=True)
    guild_id = db.Column(db.String(20), nullable=True)

def add_log(type, action, details=None, user_id=None, guild_id=None):
    with app.app_context():
        log = Log(
            timestamp=get_msk_time(),  # Используем МСК
            type=type,
            action=action,
            details=details,
            user_id=user_id,
            guild_id=guild_id
        )
        db.session.add(log)
        db.session.commit()
        socketio.emit('new_log', {
            'timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'type': log.type,
            'action': log.action,
            'details': log.details,
            'icon': get_log_icon(type)  # Добавляем иконки для логов
        })

# Функция для получения иконок логов
def get_log_icon(type):
    icons = {
        'BOT': 'fa-robot',
        'PANEL': 'fa-desktop',
        'ERROR': 'fa-exclamation-triangle',
        'TICKET': 'fa-ticket-alt',
        'INFO': 'fa-info-circle'
    }
    return icons.get(type, 'fa-circle')

def get_log_color(type):
    colors = {
        'BOT': 'primary',
        'PANEL': 'success',
        'ERROR': 'danger',
        'TICKET': 'warning',
        'INFO': 'info'
    }
    return colors.get(type, 'secondary')

# Инициализация Discord бота
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Команды бота
@bot.tree.command(name="ticket", description="Создать тикет")
@app_commands.describe(subject="Тема тикета")
async def ticket(interaction: discord.Interaction, subject: str):
    await interaction.response.send_message(f"Создание тикета: {subject}")

@bot.tree.command(name="stats", description="Показать статистику")
async def stats(interaction: discord.Interaction):
    await interaction.response.send_message("Статистика бота")

@bot.event
async def on_ready():
    try:
        print(f'Бот {bot.user} готов к работе!')
        add_log('BOT', 'Startup', f'Bot {bot.user} is ready')
        await bot.tree.sync()
        print("Команды синхронизированы!")
    except Exception as e:
        print(f"Ошибка в on_ready: {e}")
        logger.error(f"Ошибка в on_ready: {e}", exc_info=True)

@bot.event
async def on_guild_join(guild):
    add_log('BOT', 'Guild Join', f'Joined {guild.name}', guild_id=str(guild.id))

@bot.event
async def on_guild_remove(guild):
    add_log('BOT', 'Guild Leave', f'Left {guild.name}', guild_id=str(guild.id))

# Flask routes
@app.route('/login')
def login():
    DISCORD_OAUTH_URL = f'https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&redirect_uri={quote(DISCORD_REDIRECT_URI)}&response_type=code&scope=identify%20guilds'
    
    logger.debug(f"Redirecting to Discord OAuth URL: {DISCORD_OAUTH_URL}")
    return redirect(DISCORD_OAUTH_URL)

@app.route('/callback')
def callback():
    if 'error' in request.args:
        logger.error(f"Discord OAuth error: {request.args.get('error')}")
        flash('Ошибка авторизации: ' + request.args.get('error'))
        return redirect(url_for('login'))
        
    code = request.args.get('code')
    if not code:
        logger.error("No code provided in callback")
        flash('Код авторизации не получен')
        return redirect(url_for('login'))
    
    try:
        data = {
            'client_id': DISCORD_CLIENT_ID,
            'client_secret': DISCORD_CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': DISCORD_REDIRECT_URI
        }
        
        # Используем urlencode для правильного кодирования данных
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        # Логируем данные запроса (без client_secret)
        debug_data = data.copy()
        debug_data['client_secret'] = '***'
        logger.debug(f"OAuth request data: {debug_data}")
        
        response = requests.post(
            'https://discord.com/api/oauth2/token',
            data=data,
            headers=headers
        )
        
        if response.status_code != 200:
            error_data = response.json() if response.text else "No response data"
            logger.error(f"Discord OAuth error - Status: {response.status_code}, Response: {error_data}")
            logger.error(f"Request URL: {response.request.url}")
            logger.error(f"Request headers: {response.request.headers}")
            flash('Ошибка авторизации Discord')
            return redirect(url_for('login'))
            
        tokens = response.json()
        access_token = tokens.get('access_token')
        
        headers = {'Authorization': f'Bearer {access_token}'}
        user_response = requests.get('https://discord.com/api/users/@me', headers=headers)
        
        if user_response.status_code != 200:
            logger.error(f"User info error: {user_response.text}")
            flash('Ошибка при получении информации пользователя')
            return redirect(url_for('login'))
            
        user_data = user_response.json()
        
        user = User.query.filter_by(discord_id=user_data['id']).first()
        if not user:
            user = User(
                discord_id=user_data['id'],
                username=user_data['username'],
                avatar=f"https://cdn.discordapp.com/avatars/{user_data['id']}/{user_data['avatar']}.png" if user_data.get('avatar') else None,
                access_token=access_token,
                refresh_token=tokens.get('refresh_token')
            )
            db.session.add(user)
        else:
            user.username = user_data['username']
            user.avatar = f"https://cdn.discordapp.com/avatars/{user_data['id']}/{user_data['avatar']}.png" if user_data.get('avatar') else None
            user.access_token = access_token
            user.refresh_token = tokens.get('refresh_token')
        
        db.session.commit()
        login_user(user)
        
        logger.info(f"User {user.username} successfully logged in")
        return redirect(url_for('index'))
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error during OAuth: {str(e)}")
        flash('Ошибка сети при авторизации')
        return redirect(url_for('login'))
    except Exception as e:
        logger.error(f"Unexpected error during OAuth: {str(e)}", exc_info=True)
        flash('Неожиданная ошибка при авторизации')
        return redirect(url_for('login'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    # Получаем список серверов пользователя через Discord API
    headers = {
        'Authorization': f'Bearer {current_user.access_token}'
    }
    response = requests.get('https://discord.com/api/v10/users/@me/guilds', headers=headers)
    
    if response.status_code == 200:
        current_user.guilds = response.json()
    else:
        current_user.guilds = []
        flash('Не удалось получить список серверов', 'error')
    
    return render_template('index.html')

@app.route('/stats')
@login_required
def stats():
    # Получаем номер страницы из параметров запроса
    page = request.args.get('page', 1, type=int)
    
    # Получаем даты за последние 7 дней
    dates = [(datetime.now() - timedelta(days=i)).strftime('%d.%m') for i in range(6, -1, -1)]
    
    # Получаем логи по дням
    activity_counts = [
        Log.query.filter(
            Log.timestamp >= datetime.now() - timedelta(days=i+1),
            Log.timestamp < datetime.now() - timedelta(days=i)
        ).count()
        for i in range(6, -1, -1)
    ]

    # Получаем логи с пагинацией
    logs_pagination = Log.query.order_by(Log.timestamp.desc()).paginate(
        page=page, per_page=10, error_out=False
    )

    stats_data = {
        'total_users': sum(g.member_count for g in bot.guilds),
        'total_servers': len(bot.guilds),
        'total_tickets': Ticket.query.count(),
        'open_tickets': Ticket.query.filter_by(status='open').count()
    }

    return render_template('stats.html', 
                         stats=stats_data,
                         activity_dates=dates,
                         activity_counts=activity_counts,
                         logs=logs_pagination.items,
                         pagination=logs_pagination)

@app.route('/tickets')
@login_required
def tickets():
    all_tickets = Ticket.query.order_by(Ticket.created_at.desc()).all()
    return render_template('tickets.html', tickets=all_tickets)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Создание базы данных
with app.app_context():
    db.create_all()
    print("База данных создана")
    # Создаем админа если его нет
    admin_discord_id = os.getenv('ADMIN_DISCORD_ID')
    if admin_discord_id:
        admin = User.query.filter_by(discord_id=admin_discord_id).first()
        if not admin:
            admin = User(
                discord_id=admin_discord_id,
                username='Admin',
                is_admin=True
            )
            db.session.add(admin)
            db.session.commit()
            print("Администратор создан")

def run_bot():
    bot.run(DISCORD_BOT_TOKEN)

@app.route('/logs')
@login_required
def logs():
    page = request.args.get('page', 1, type=int)
    log_type = request.args.get('type')
    
    query = Log.query
    if log_type:
        query = query.filter_by(type=log_type)
    
    logs_pagination = query.order_by(Log.timestamp.desc()).paginate(
        page=page, per_page=50
    )
    
    return render_template('logs.html', 
                         logs=logs_pagination.items, 
                         pagination=logs_pagination,
                         get_log_icon=get_log_icon,  # Передаем функцию в шаблон
                         get_log_color=get_log_color  # Передаем функцию в шаблон
                         )

@app.route('/api/logs')
@login_required
def api_logs():
    days = request.args.get('days', 7, type=int)
    start_date = datetime.utcnow() - timedelta(days=days)
    
    logs = Log.query.filter(Log.timestamp >= start_date).all()
    data = {
        'labels': [],
        'bot': [],
        'panel': [],
        'ticket': [],
        'error': []
    }
    
    for i in range(days):
        date = (start_date + timedelta(days=i)).strftime('%Y-%m-%d')
        data['labels'].append(date)
        data['bot'].append(0)
        data['panel'].append(0)
        data['ticket'].append(0)
        data['error'].append(0)
    
    for log in logs:
        day_index = (log.timestamp - start_date).days
        if 0 <= day_index < days:
            data[log.type.lower()][day_index] += 1
    
    return jsonify(data)

def check_env_variables():
    required_vars = [
        'DISCORD_CLIENT_ID',
        'DISCORD_CLIENT_SECRET',
        'DISCORD_BOT_TOKEN'
    ]
    
    missing = []
    for var in required_vars:
        if not os.getenv(var):
            missing.append(var)
    
    if missing:
        raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")

# Проверяем переменные при запуске
check_env_variables()

# После создания socketio
console_handler = WebConsoleHandler(socketio)
logging.getLogger().addHandler(console_handler)
sys.stdout = ConsoleToWeb(socketio)
sys.stderr = ConsoleToWeb(socketio)

@socketio.on('connect')
def handle_connect():
    print("Client connected to WebSocket")

@socketio.on('disconnect')
def handle_disconnect():
    print("Client disconnected from WebSocket")

@app.route('/antinuke')
@login_required
def antinuke():
    # Получаем настройки для текущего сервера
    guild_id = request.args.get('guild_id')
    if not guild_id:
        flash('Выберите сервер', 'error')
        return redirect(url_for('index'))
        
    settings = AntinukeSettings.query.filter_by(guild_id=guild_id).first()
    if not settings:
        settings = AntinukeSettings(guild_id=guild_id)
        db.session.add(settings)
        db.session.commit()
    
    # Получаем белый список
    whitelist = WhitelistedUser.query.filter_by(guild_id=guild_id).all()
    
    # Получаем последние логи
    logs = AntinukeLog.query.filter_by(guild_id=guild_id)\
        .order_by(AntinukeLog.timestamp.desc())\
        .limit(50)\
        .all()
    
    return render_template('antinuke.html', 
                         settings=settings,
                         whitelist=whitelist,
                         logs=logs)

# API endpoints для управления настройками
@app.route('/api/antinuke/settings', methods=['POST'])
@login_required
def update_antinuke_settings():
    data = request.json
    guild_id = data.get('guild_id')
    
    settings = AntinukeSettings.query.filter_by(guild_id=guild_id).first()
    if not settings:
        settings = AntinukeSettings(guild_id=guild_id)
        db.session.add(settings)
    
    # Обновляем настройки
    settings.enabled = data.get('enabled', settings.enabled)
    settings.protection_level = data.get('protection_level', settings.protection_level)
    settings.autoban = data.get('autoban', settings.autoban)
    settings.protect_channels = data.get('protect_channels', settings.protect_channels)
    settings.protect_roles = data.get('protect_roles', settings.protect_roles)
    settings.protect_mass_ban = data.get('protect_mass_ban', settings.protect_mass_ban)
    settings.protect_mass_kick = data.get('protect_mass_kick', settings.protect_mass_kick)
    settings.ban_limit = data.get('ban_limit', settings.ban_limit)
    settings.kick_limit = data.get('kick_limit', settings.kick_limit)
    settings.channel_limit = data.get('channel_limit', settings.channel_limit)
    settings.role_limit = data.get('role_limit', settings.role_limit)
    
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/api/antinuke/whitelist', methods=['POST'])
@login_required
def add_to_whitelist():
    data = request.json
    guild_id = data.get('guild_id')
    user_id = data.get('user_id')
    
    if not guild_id or not user_id:
        return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400
        
    try:
        whitelist = WhitelistedUser(
            guild_id=guild_id,
            user_id=user_id,
            added_by=current_user.discord_id
        )
        db.session.add(whitelist)
        db.session.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 400

@app.route('/api/antinuke/whitelist/<user_id>', methods=['DELETE'])
@login_required
def remove_from_whitelist(user_id):
    guild_id = request.args.get('guild_id')
    if not guild_id:
        return jsonify({'status': 'error', 'message': 'Missing guild_id'}), 400
        
    WhitelistedUser.query.filter_by(guild_id=guild_id, user_id=user_id).delete()
    db.session.commit()
    return jsonify({'status': 'success'})

# Модель для настроек анти-слив системы
class AntinukeSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    guild_id = db.Column(db.String(20), unique=True)
    enabled = db.Column(db.Boolean, default=False)
    protection_level = db.Column(db.String(20), default='medium')
    autoban = db.Column(db.Boolean, default=False)
    protect_channels = db.Column(db.Boolean, default=True)
    protect_roles = db.Column(db.Boolean, default=True)
    protect_mass_ban = db.Column(db.Boolean, default=True)
    protect_mass_kick = db.Column(db.Boolean, default=True)
    ban_limit = db.Column(db.Integer, default=3)
    kick_limit = db.Column(db.Integer, default=5)
    channel_limit = db.Column(db.Integer, default=2)
    role_limit = db.Column(db.Integer, default=2)

# Модель для белого списка
class WhitelistedUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    guild_id = db.Column(db.String(20))
    user_id = db.Column(db.String(20))
    added_by = db.Column(db.String(20))
    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (db.UniqueConstraint('guild_id', 'user_id'),)

# Модель для логов анти-слив системы
class AntinukeLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    guild_id = db.Column(db.String(20))
    action = db.Column(db.String(100))
    target_id = db.Column(db.String(20))
    target_type = db.Column(db.String(20))
    blocked = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class TicketMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'))
    author_id = db.Column(db.String(20))
    content = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

@app.route('/api/tickets/<int:ticket_id>/messages')
@login_required
def get_ticket_messages(ticket_id):
    messages = TicketMessage.query.filter_by(ticket_id=ticket_id).order_by(TicketMessage.timestamp).all()
    return jsonify([{
        'author': message.author_id,
        'content': message.content,
        'timestamp': message.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
        'is_bot': str(message.author_id) == str(bot.user.id)
    } for message in messages])

@app.route('/api/tickets/<int:ticket_id>/messages', methods=['POST'])
@login_required
def send_ticket_message(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    content = request.json.get('content')
    
    if not content:
        return jsonify({'error': 'No content provided'}), 400
        
    # Отправляем сообщение в Discord через очередь задач
    channel = bot.get_channel(int(ticket.channel_id))
    if channel:
        bot.loop.create_task(channel.send(content))
    
    # Сохраняем в БД
    message = TicketMessage(
        ticket_id=ticket_id,
        author_id=str(bot.user.id),
        content=content,
        timestamp=datetime.now()
    )
    db.session.add(message)
    db.session.commit()
    
    return jsonify({'status': 'success'})

@app.route('/api/tickets/<int:ticket_id>/close', methods=['POST'])
@login_required
def close_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    ticket.status = 'closed'
    db.session.commit()
    
    # Закрываем канал в Discord через очередь задач
    channel = bot.get_channel(int(ticket.channel_id))
    if channel:
        bot.loop.create_task(channel.edit(archived=True))
    
    return jsonify({'status': 'success'})

if __name__ == '__main__':
    import threading
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()
    socketio.run(app, debug=True) 