import os
import shutil
from datetime import datetime
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
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

SHARED_FOLDERS = [
    {"slug": "music", "name": "Музыка"},
    {"slug": "video", "name": "Видео"},
    {"slug": "photo", "name": "Фото"},
    {"slug": "documents", "name": "Документы"},
]
SHARED_FOLDER_MAP = {folder["slug"]: folder["name"] for folder in SHARED_FOLDERS}
INVALID_PATH_CHARS = '<>:"/\\|?*'
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def get_upload_root():
    upload_root = Path(app.config['UPLOAD_FOLDER']).resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    return upload_root


def get_shared_root():
    shared_root = get_upload_root() / "shared"
    shared_root.mkdir(parents=True, exist_ok=True)
    return shared_root


def ensure_shared_folders():
    shared_root = get_shared_root()
    for folder in SHARED_FOLDERS:
        (shared_root / folder["slug"]).mkdir(parents=True, exist_ok=True)


def sanitize_relative_path(raw_path):
    cleaned = (raw_path or "").replace("\\", "/").strip("/")
    if not cleaned:
        return ""

    parts = [part for part in cleaned.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        abort(400)
    return "/".join(parts)


def sanitize_entry_name(raw_name):
    name = (raw_name or "").strip().rstrip(". ")
    if not name:
        return None

    cleaned = "".join(ch for ch in name if ch not in INVALID_PATH_CHARS and ord(ch) >= 32)
    cleaned = cleaned.strip().rstrip(". ")
    if not cleaned:
        return None
    if cleaned.upper() in WINDOWS_RESERVED_NAMES:
        return None
    return cleaned


def resolve_shared_path(folder_slug, relative_path=""):
    ensure_shared_folders()

    if folder_slug not in SHARED_FOLDER_MAP:
        abort(404)

    root = (get_shared_root() / folder_slug).resolve()
    safe_relative_path = sanitize_relative_path(relative_path)
    target = (root / safe_relative_path).resolve()

    if target != root and root not in target.parents:
        abort(403)

    return root, target, safe_relative_path


def create_available_path(destination_dir, desired_name):
    candidate = destination_dir / desired_name
    if not candidate.exists():
        return candidate

    stem = Path(desired_name).stem or desired_name
    suffix = Path(desired_name).suffix
    counter = 1

    while True:
        if suffix:
            candidate = destination_dir / f"{stem} ({counter}){suffix}"
        else:
            candidate = destination_dir / f"{desired_name} ({counter})"
        if not candidate.exists():
            return candidate
        counter += 1


def format_size(size_bytes):
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    size = float(size_bytes)

    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "Б":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024


def describe_file_type(path_obj):
    if path_obj.is_dir():
        return "Папка"

    extension = path_obj.suffix.lower()
    mapping = {
        ".mp3": "Аудиофайл",
        ".wav": "Аудиофайл",
        ".flac": "Аудиофайл",
        ".mp4": "Видео",
        ".mkv": "Видео",
        ".avi": "Видео",
        ".jpg": "Изображение",
        ".jpeg": "Изображение",
        ".png": "Изображение",
        ".gif": "Изображение",
        ".pdf": "PDF",
        ".doc": "Документ Word",
        ".docx": "Документ Word",
        ".xls": "Таблица Excel",
        ".xlsx": "Таблица Excel",
        ".txt": "Текстовый файл",
        ".zip": "Архив",
        ".rar": "Архив",
    }
    return mapping.get(extension, "Файл")


def build_shared_items(folder_slug, current_relative_path):
    root_directory, current_directory, current_relative_path = resolve_shared_path(folder_slug, current_relative_path)
    items = []

    for child in sorted(current_directory.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.lower())):
        child_relative_path = child.relative_to(root_directory).as_posix()
        stats = child.stat()
        items.append(
            {
                "name": child.name,
                "relative_path": child_relative_path,
                "is_dir": child.is_dir(),
                "modified_at": datetime.fromtimestamp(stats.st_mtime).strftime("%d.%m.%Y %H:%M"),
                "size": "" if child.is_dir() else format_size(stats.st_size),
                "type_label": describe_file_type(child),
                "open_url": (
                    url_for("shared_explorer", folder_slug=folder_slug, path=child_relative_path)
                    if child.is_dir()
                    else url_for("shared_download", folder_slug=folder_slug, path=child_relative_path)
                ),
            }
        )

    return items


def build_breadcrumbs(folder_slug, current_relative_path):
    breadcrumbs = [
        {
            "label": SHARED_FOLDER_MAP[folder_slug],
            "url": url_for("shared_explorer", folder_slug=folder_slug),
        }
    ]

    current_parts = [part for part in sanitize_relative_path(current_relative_path).split("/") if part]
    for index, part in enumerate(current_parts):
        relative_path = "/".join(current_parts[: index + 1])
        breadcrumbs.append(
            {
                "label": part,
                "url": url_for("shared_explorer", folder_slug=folder_slug, path=relative_path),
            }
        )

    return breadcrumbs


def copy_entry(source_path, destination_path):
    if source_path.is_dir():
        shutil.copytree(source_path, destination_path)
    else:
        shutil.copy2(source_path, destination_path)


def get_clipboard():
    clipboard = session.get("shared_clipboard")
    if not clipboard or not clipboard.get("items"):
        return None
    return clipboard


def redirect_to_shared(folder_slug, current_relative_path=""):
    safe_relative_path = sanitize_relative_path(current_relative_path)
    if safe_relative_path:
        return redirect(url_for("shared_explorer", folder_slug=folder_slug, path=safe_relative_path))
    return redirect(url_for("shared_explorer", folder_slug=folder_slug))


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

    ensure_shared_folders()
    upload_root = get_upload_root()
    total, used, free = shutil.disk_usage(upload_root)
    files = File.query.filter_by(owner_id=current_user.id).all()

    return render_template(
        'dashboard.html',
        greeting=greeting,
        percent=int((used / total) * 100),
        username=current_user.username,
        files=files,
        shared_folders=SHARED_FOLDERS,
        total_gb=round(total / (1024 ** 3), 1),
        used_gb=round(used / (1024 ** 3), 1),
        free_gb=round(free / (1024 ** 3), 1),
        today_label=datetime.now().strftime('%d.%m.%Y'),
        avatar_text=(current_user.username[:2] or "U").upper(),
    )


@app.route('/shared/<folder_slug>')
@login_required
def shared_explorer(folder_slug):
    ensure_shared_folders()
    _, current_directory, current_relative_path = resolve_shared_path(folder_slug, request.args.get("path", ""))
    if not current_directory.exists() or not current_directory.is_dir():
        abort(404)

    clipboard = get_clipboard()
    parent_relative_path = ""
    if current_relative_path:
        parent_relative_path = "/".join(current_relative_path.split("/")[:-1])

    return render_template(
        "shared_explorer.html",
        folder_slug=folder_slug,
        folder_name=SHARED_FOLDER_MAP[folder_slug],
        shared_folders=SHARED_FOLDERS,
        items=build_shared_items(folder_slug, current_relative_path),
        breadcrumbs=build_breadcrumbs(folder_slug, current_relative_path),
        current_relative_path=current_relative_path,
        parent_relative_path=parent_relative_path,
        clipboard=clipboard,
        clipboard_count=len(clipboard["items"]) if clipboard else 0,
        clipboard_mode=clipboard["mode"] if clipboard else "",
    )


@app.route('/shared/<folder_slug>/upload', methods=['POST'])
@login_required
def shared_upload(folder_slug):
    _, destination_directory, current_relative_path = resolve_shared_path(folder_slug, request.form.get("current_path", ""))
    if not destination_directory.is_dir():
        abort(404)

    uploaded_files = [file for file in request.files.getlist("files") if file and file.filename]
    if not uploaded_files:
        flash("Сначала выберите файлы на компьютере.", "error")
        return redirect_to_shared(folder_slug, current_relative_path)

    saved_count = 0
    for uploaded_file in uploaded_files:
        original_name = Path(uploaded_file.filename).name
        safe_name = sanitize_entry_name(original_name)
        if not safe_name:
            continue

        destination_path = create_available_path(destination_directory, safe_name)
        uploaded_file.save(destination_path)
        saved_count += 1

    if saved_count:
        flash(f"Загружено файлов: {saved_count}.", "success")
    else:
        flash("Не удалось сохранить выбранные файлы.", "error")
    return redirect_to_shared(folder_slug, current_relative_path)


@app.route('/shared/<folder_slug>/action', methods=['POST'])
@login_required
def shared_action(folder_slug):
    action = request.form.get("action", "")
    current_relative_path = request.form.get("current_path", "")
    _, current_directory, current_relative_path = resolve_shared_path(folder_slug, current_relative_path)
    if not current_directory.is_dir():
        abort(404)

    selected_relative_paths = []
    for relative_path in request.form.getlist("selected_paths"):
        safe_relative_path = sanitize_relative_path(relative_path)
        if safe_relative_path:
            selected_relative_paths.append(safe_relative_path)

    if action == "create_folder":
        folder_name = sanitize_entry_name(request.form.get("folder_name", ""))
        if not folder_name:
            flash("Укажите корректное имя папки.", "error")
            return redirect_to_shared(folder_slug, current_relative_path)

        new_folder_path = create_available_path(current_directory, folder_name)
        new_folder_path.mkdir(parents=False, exist_ok=False)
        flash(f"Папка «{new_folder_path.name}» создана.", "success")
        return redirect_to_shared(folder_slug, current_relative_path)

    if action in {"copy", "cut"}:
        if not selected_relative_paths:
            flash("Выберите хотя бы один файл или папку.", "error")
            return redirect_to_shared(folder_slug, current_relative_path)

        session["shared_clipboard"] = {
            "mode": action,
            "items": [{"folder_slug": folder_slug, "relative_path": path} for path in selected_relative_paths],
        }
        flash("Элементы добавлены в буфер обмена.", "success")
        return redirect_to_shared(folder_slug, current_relative_path)

    if action == "paste":
        clipboard = get_clipboard()
        if not clipboard:
            flash("Буфер обмена пуст.", "error")
            return redirect_to_shared(folder_slug, current_relative_path)

        transferred_count = 0
        for item in clipboard["items"]:
            _, source_path, _ = resolve_shared_path(item["folder_slug"], item["relative_path"])
            if not source_path.exists():
                continue

            if source_path.is_dir() and (current_directory == source_path or source_path in current_directory.parents):
                continue

            if clipboard["mode"] == "cut" and source_path.parent == current_directory:
                continue

            destination_path = create_available_path(current_directory, source_path.name)
            if clipboard["mode"] == "copy":
                copy_entry(source_path, destination_path)
            else:
                shutil.move(str(source_path), str(destination_path))
            transferred_count += 1

        if clipboard["mode"] == "cut":
            session.pop("shared_clipboard", None)

        if transferred_count:
            flash("Буфер обмена вставлен.", "success")
        else:
            flash("Не удалось вставить выбранные элементы.", "error")
        return redirect_to_shared(folder_slug, current_relative_path)

    if action == "delete":
        if not selected_relative_paths:
            flash("Сначала отметьте элементы для удаления.", "error")
            return redirect_to_shared(folder_slug, current_relative_path)

        deleted_count = 0
        for relative_path in selected_relative_paths:
            _, target_path, _ = resolve_shared_path(folder_slug, relative_path)
            if not target_path.exists():
                continue
            if target_path.is_dir():
                shutil.rmtree(target_path)
            else:
                target_path.unlink()
            deleted_count += 1

        flash(f"Удалено элементов: {deleted_count}.", "success")
        return redirect_to_shared(folder_slug, current_relative_path)

    flash("Неизвестное действие.", "error")
    return redirect_to_shared(folder_slug, current_relative_path)


@app.route('/shared/<folder_slug>/download')
@login_required
def shared_download(folder_slug):
    _, target_path, _ = resolve_shared_path(folder_slug, request.args.get("path", ""))
    if not target_path.exists() or not target_path.is_file():
        abort(404)
    return send_file(target_path, as_attachment=True, download_name=target_path.name)


@app.route('/upload', methods=['POST'])
@login_required
def upload():
    uploaded_file = request.files['file']
    get_upload_root()
    safe_name = sanitize_entry_name(Path(uploaded_file.filename).name)
    if not safe_name:
        return redirect('/dashboard')

    path = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
    uploaded_file.save(path)

    file = File(filename=safe_name, path=path, owner_id=current_user.id)
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
    ensure_shared_folders()
    app.run(host="0.0.0.0", port=5000)
