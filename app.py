import os
from flask import Flask, request, redirect, render_template, send_file
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import shutil

from models import db, User, File

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
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect('/dashboard')
    return render_template('login.html')


@app.route('/dashboard')
@login_required
def dashboard():
    now = datetime.now().hour

    if 6 <= now < 12:
        greeting = "Доброе утро"
    elif 12 <= now < 18:
        greeting = "Добрый день"
    elif 18 <= now < 24:
        greeting = "Добрый вечер"
    else:
        greeting = "Доброй ночи"

    # 📊 инфа о диске
    total, used, free = shutil.disk_usage("/")
    percent = int((used / total) * 100)

    files = File.query.filter_by(owner_id=current_user.id).all()

    return render_template(
        'dashboard.html',
        greeting=greeting,
        percent=percent,
        username=current_user.username,
        files=files
    )


@app.route('/upload', methods=['POST'])
@login_required
def upload():
    f = request.files['file']
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

    # 🔥 защита от IDOR
    if file.owner_id != current_user.id:
        return "Access denied", 403

    return send_file(file.path)

@app.route('/storage')
@login_required
def storage():
    files = File.query.filter_by(owner_id=current_user.id).all()
    return render_template("storage.html", files=files)

@app.route('/logout')
def logout():
    logout_user()
    return redirect('/')
    

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000)