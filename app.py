import os
import shutil
from datetime import datetime

from flask import Flask, redirect, render_template, request, send_file
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from werkzeug.security import check_password_hash

from models import File, User, db

app = Flask(__name__)
app.config['SECRET_KEY'] = 'supersecret'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024  # 10GB

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.route('/', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect('/dashboard')

    error = None

    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect('/dashboard')
        error = "Неверный логин или пароль"

    return render_template('login.html', error=error)


@app.route('/dashboard')
@login_required
def dashboard():
    hour = datetime.now().hour

    if 6 <= hour < 12:
        greeting = "Доброе утро"
    elif 12 <= hour < 18:
        greeting = "Добрый день"
    elif 18 <= hour < 24:
        greeting = "Добрый вечер"
    else:
        greeting = "Доброй ночи"

    upload_root = os.path.abspath(app.config['UPLOAD_FOLDER'])
    os.makedirs(upload_root, exist_ok=True)

    total, used, free = shutil.disk_usage(upload_root)
    percent = int((used / total) * 100)
    files = File.query.filter_by(owner_id=current_user.id).all()

    shared_folders = [
        {"name": "Отдел продаж", "description": "Общие презентации и отчеты"},
        {"name": "Договоры", "description": "Шаблоны, акты и архив документов"},
        {"name": "Маркетинг", "description": "Баннеры, исходники и медиаматериалы"},
        {"name": "Команда", "description": "Внутренние регламенты и инструкции"},
        {"name": "Проекты", "description": "Текущие рабочие материалы"},
    ]

    return render_template(
        'dashboard.html',
        greeting=greeting,
        percent=percent,
        username=current_user.username,
        files=files,
        shared_folders=shared_folders,
        total_gb=round(total / (1024 ** 3), 1),
        used_gb=round(used / (1024 ** 3), 1),
        free_gb=round(free / (1024 ** 3), 1),
        today_label=datetime.now().strftime('%d.%m.%Y'),
        avatar_text=(current_user.username[:2] or "U").upper(),
    )


@app.route('/upload', methods=['POST'])
@login_required
def upload():
    f = request.files['file']
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    path = os.path.join(app.config['UPLOAD_FOLDER'], f.filename)
    f.save(path)

    file = File(filename=f.filename, path=path, owner_id=current_user.id)
    db.session.add(file)
    db.session.commit()

    return redirect('/dashboard')


@app.route('/profile')
@login_required
def profile():
    return f"Профиль пользователя {current_user.username}"


@app.route('/download/<int:file_id>')
@login_required
def download(file_id):
    file = File.query.get(file_id)

    if file.owner_id != current_user.id:
        return "Access denied", 403

    return send_file(file.path)


@app.route('/storage')
@login_required
def storage():
    files = File.query.filter_by(owner_id=current_user.id).all()
    return render_template("storage.html", files=files, username=current_user.username)


@app.route('/logout')
def logout():
    logout_user()
    return redirect('/')


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000)
