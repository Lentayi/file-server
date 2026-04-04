import os
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

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
from sqlalchemy import inspect
from werkzeug.security import check_password_hash, generate_password_hash

from models import File, SharedFolder, SharedFolderAccess, User, UserPermission, db

app = Flask(__name__)
app.config['SECRET_KEY'] = 'supersecret'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024  # 10GB

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)

DEFAULT_SHARED_FOLDERS = [
    {"name": "Музыка", "slug": "music"},
    {"name": "Видео", "slug": "video"},
    {"name": "Фото", "slug": "photo"},
    {"name": "Документы", "slug": "documents"},
]
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
ALLOWED_AVATAR_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
ALLOWED_THEME_PREFERENCES = {"light", "dark", "auto"}
RUNTIME_SCHEMA_READY = False


@login_manager.user_loader
def load_user(user_id):
    ensure_runtime_schema()
    return User.query.get(int(user_id))


def get_upload_root():
    upload_root = Path(app.config['UPLOAD_FOLDER']).resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    return upload_root


def get_shared_root():
    shared_root = get_upload_root() / "shared"
    shared_root.mkdir(parents=True, exist_ok=True)
    return shared_root


def get_avatar_root():
    avatar_root = get_upload_root() / "avatars"
    avatar_root.mkdir(parents=True, exist_ok=True)
    return avatar_root


def ensure_runtime_schema():
    global RUNTIME_SCHEMA_READY
    if RUNTIME_SCHEMA_READY:
        return

    inspector = inspect(db.engine)
    if "user" not in inspector.get_table_names():
        return

    user_columns = {column["name"] for column in inspector.get_columns("user")}
    if "theme_preference" not in user_columns:
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE user ADD COLUMN theme_preference VARCHAR(20) NOT NULL DEFAULT 'light'"
            )

    RUNTIME_SCHEMA_READY = True


def normalize_theme_preference(raw_value):
    return raw_value if raw_value in ALLOWED_THEME_PREFERENCES else "light"


def get_theme_preference(user):
    return normalize_theme_preference(getattr(user, "theme_preference", "light"))


def resolve_active_theme(user, hour=None):
    preference = get_theme_preference(user)
    if preference != "auto":
        return preference

    current_hour = datetime.now().hour if hour is None else hour
    return "light" if 6 <= current_hour < 18 else "dark"


def ensure_default_shared_folders():
    if SharedFolder.query.count() == 0:
        for folder in DEFAULT_SHARED_FOLDERS:
            db.session.add(SharedFolder(name=folder["name"], slug=folder["slug"]))
        db.session.commit()

    default_names = {folder["slug"]: folder["name"] for folder in DEFAULT_SHARED_FOLDERS}
    needs_commit = False
    for folder in SharedFolder.query.all():
        normalized_name = default_names.get(folder.slug)
        if normalized_name and folder.name != normalized_name:
            folder.name = normalized_name
            needs_commit = True

    if needs_commit:
        db.session.commit()

    for folder in SharedFolder.query.all():
        get_shared_storage_root(folder)


def ensure_user_permission_record(user):
    permission = UserPermission.query.filter_by(user_id=user.id).first()
    if permission:
        return permission

    permission = UserPermission(user_id=user.id)
    db.session.add(permission)
    db.session.commit()
    return permission


def get_user_permission_record(user):
    permission = UserPermission.query.filter_by(user_id=user.id).first()
    if permission:
        return permission
    return UserPermission(user_id=user.id, can_create_shared_folders=False, can_edit_shared_folders=False)


def can_create_shared_folders(user):
    return user.is_admin or get_user_permission_record(user).can_create_shared_folders


def can_edit_shared_folders(user):
    return user.is_admin or get_user_permission_record(user).can_edit_shared_folders


def get_role_label(user):
    return "Администратор" if user.is_admin else "Пользователь"


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


def get_avatar_path(user):
    avatar_root = get_avatar_root()
    matches = sorted(avatar_root.glob(f"user_{user.id}.*"))
    return matches[0] if matches else None


def get_avatar_url(user):
    avatar_path = get_avatar_path(user)
    if avatar_path and avatar_path.exists():
        return url_for("avatar", user_id=user.id)
    return None


def save_avatar(user, uploaded_file):
    extension = Path(uploaded_file.filename).suffix.lower()
    if extension not in ALLOWED_AVATAR_EXTENSIONS:
        return False

    current_avatar = get_avatar_path(user)
    if current_avatar and current_avatar.exists():
        current_avatar.unlink()

    destination_path = get_avatar_root() / f"user_{user.id}{extension}"
    uploaded_file.save(destination_path)
    return True


def build_user_badges(user):
    permission = get_user_permission_record(user)
    badges = [get_role_label(user)]
    if permission.can_create_shared_folders:
        badges.append("Создание общих папок")
    if permission.can_edit_shared_folders:
        badges.append("Изменение общих папок")
    return badges


def get_shared_storage_root(folder):
    safe_slug = sanitize_entry_name(folder.slug) or f"folder-{folder.id}"
    folder_root = get_shared_root() / f"folder_{folder.id}_{safe_slug}"
    folder_root.mkdir(parents=True, exist_ok=True)
    return folder_root


def get_accessible_shared_folders(user):
    folders = SharedFolder.query.order_by(SharedFolder.id.asc()).all()
    if user.is_admin:
        return folders

    accessible_ids = {
        row.shared_folder_id
        for row in SharedFolderAccess.query.filter_by(user_id=user.id, can_access=True).all()
    }
    return [folder for folder in folders if folder.id in accessible_ids]


def user_has_shared_access(user, folder):
    if user.is_admin:
        return True

    return SharedFolderAccess.query.filter_by(
        user_id=user.id,
        shared_folder_id=folder.id,
        can_access=True,
    ).first() is not None


def get_folder_by_id(folder_id):
    folder = SharedFolder.query.get_or_404(folder_id)
    if not user_has_shared_access(current_user, folder):
        abort(403)
    return folder


def resolve_shared_path(folder, relative_path=""):
    root = get_shared_storage_root(folder).resolve()
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


def build_shared_items(folder, current_relative_path):
    root_directory, current_directory, _ = resolve_shared_path(folder, current_relative_path)
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
                    url_for("shared_explorer", folder_id=folder.id, path=child_relative_path)
                    if child.is_dir()
                    else url_for("shared_download", folder_id=folder.id, path=child_relative_path)
                ),
            }
        )

    return items


def build_shared_breadcrumbs(folder, current_relative_path):
    breadcrumbs = [
        {
            "label": folder.name,
            "url": url_for("shared_explorer", folder_id=folder.id),
        }
    ]

    current_parts = [part for part in sanitize_relative_path(current_relative_path).split("/") if part]
    for index, part in enumerate(current_parts):
        relative_path = "/".join(current_parts[: index + 1])
        breadcrumbs.append(
            {
                "label": part,
                "url": url_for("shared_explorer", folder_id=folder.id, path=relative_path),
            }
        )

    return breadcrumbs


def copy_entry(source_path, destination_path):
    if source_path.is_dir():
        shutil.copytree(source_path, destination_path)
    else:
        shutil.copy2(source_path, destination_path)


def get_clipboard(session_key):
    clipboard = session.get(session_key)
    if not clipboard or not clipboard.get("items"):
        return None
    return clipboard


def get_personal_root(user):
    personal_root = get_upload_root() / "personal" / f"user_{user.id}"
    personal_root.mkdir(parents=True, exist_ok=True)
    return personal_root


def sync_legacy_personal_files(user):
    personal_root = get_personal_root(user)
    updated = False

    for file_record in File.query.filter_by(owner_id=user.id).all():
        source_path = Path(file_record.path).resolve()
        if not source_path.exists() or source_path.parent == personal_root:
            continue

        destination_path = create_available_path(personal_root, sanitize_entry_name(source_path.name) or source_path.name)
        shutil.move(str(source_path), str(destination_path))
        file_record.path = str(destination_path)
        file_record.filename = destination_path.name
        updated = True

    if updated:
        db.session.commit()


def resolve_personal_path(user, relative_path=""):
    root = get_personal_root(user).resolve()
    safe_relative_path = sanitize_relative_path(relative_path)
    target = (root / safe_relative_path).resolve()

    if target != root and root not in target.parents:
        abort(403)

    return root, target, safe_relative_path


def build_personal_items(user, current_relative_path):
    root_directory, current_directory, _ = resolve_personal_path(user, current_relative_path)
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
                    url_for("storage", path=child_relative_path)
                    if child.is_dir()
                    else url_for("storage_download", path=child_relative_path)
                ),
            }
        )

    return items


def build_personal_breadcrumbs(current_relative_path):
    breadcrumbs = [{"label": "Личное хранилище", "url": url_for("storage")}]
    current_parts = [part for part in sanitize_relative_path(current_relative_path).split("/") if part]

    for index, part in enumerate(current_parts):
        relative_path = "/".join(current_parts[: index + 1])
        breadcrumbs.append({"label": part, "url": url_for("storage", path=relative_path)})

    return breadcrumbs


def resolve_admin_path(relative_path=""):
    root = get_upload_root().resolve()
    safe_relative_path = sanitize_relative_path(relative_path)
    target = (root / safe_relative_path).resolve()

    if target != root and root not in target.parents:
        abort(403)

    return root, target, safe_relative_path


def build_admin_items(current_relative_path):
    root_directory, current_directory, _ = resolve_admin_path(current_relative_path)
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
                    url_for("admin_file_browser", path=child_relative_path)
                    if child.is_dir()
                    else url_for("admin_file_download", path=child_relative_path)
                ),
            }
        )

    return items


def build_admin_breadcrumbs(current_relative_path):
    breadcrumbs = [{"label": "Все файлы", "url": url_for("admin_file_browser")}]
    current_parts = [part for part in sanitize_relative_path(current_relative_path).split("/") if part]

    for index, part in enumerate(current_parts):
        relative_path = "/".join(current_parts[: index + 1])
        breadcrumbs.append({"label": part, "url": url_for("admin_file_browser", path=relative_path)})

    return breadcrumbs


def rename_entry(target_path, raw_name):
    new_name = sanitize_entry_name(raw_name)
    if not new_name:
        return None, "Укажите корректное новое имя."
    destination_path = target_path.with_name(new_name)
    if destination_path == target_path:
        return target_path, None
    if destination_path.exists():
        return None, "Элемент с таким именем уже существует."
    target_path.rename(destination_path)
    return destination_path, None

def create_shared_folder(name, grant_user=None):
    safe_name = sanitize_entry_name(name)
    if not safe_name:
        return None

    folder = SharedFolder(name=safe_name, slug=f"folder-{uuid4().hex[:8]}")
    db.session.add(folder)
    db.session.commit()
    get_shared_storage_root(folder)

    if grant_user and not grant_user.is_admin:
        db.session.add(SharedFolderAccess(user_id=grant_user.id, shared_folder_id=folder.id, can_access=True))
        db.session.commit()

    return folder


def update_user_folder_access(user, selected_folder_ids):
    SharedFolderAccess.query.filter_by(user_id=user.id).delete()
    for folder_id in selected_folder_ids:
        db.session.add(SharedFolderAccess(user_id=user.id, shared_folder_id=folder_id, can_access=True))
    db.session.commit()


def build_profile_context():
    ensure_runtime_schema()
    ensure_user_permission_record(current_user)
    all_users = User.query.order_by(User.username.asc()).all()
    theme_preference = get_theme_preference(current_user)

    return {
        "username": current_user.username,
        "role_label": get_role_label(current_user),
        "avatar_url": get_avatar_url(current_user),
        "avatar_text": (current_user.username[:2] or "U").upper(),
        "theme_preference": theme_preference,
        "active_theme": resolve_active_theme(current_user),
        "rights_badges": build_user_badges(current_user),
        "accessible_shared_folders": get_accessible_shared_folders(current_user),
        "can_create_shared_folders": can_create_shared_folders(current_user),
        "can_edit_shared_folders": can_edit_shared_folders(current_user),
        "all_shared_folders": SharedFolder.query.order_by(SharedFolder.id.asc()).all(),
        "all_users": all_users,
        "folder_access_map": {
            user.id: {
                row.shared_folder_id
                for row in SharedFolderAccess.query.filter_by(user_id=user.id, can_access=True).all()
            }
            for user in all_users
        },
        "permission_map": {
            user.id: get_user_permission_record(user)
            for user in all_users
        },
    }


def require_admin():
    if not current_user.is_admin:
        abort(403)


def require_shared_folder_creator():
    if not can_create_shared_folders(current_user):
        abort(403)


def require_shared_folder_editor():
    if not can_edit_shared_folders(current_user):
        abort(403)


def redirect_to_shared(folder, current_relative_path=""):
    safe_relative_path = sanitize_relative_path(current_relative_path)
    if safe_relative_path:
        return redirect(url_for("shared_explorer", folder_id=folder.id, path=safe_relative_path))
    return redirect(url_for("shared_explorer", folder_id=folder.id))


def redirect_to_personal(current_relative_path=""):
    safe_relative_path = sanitize_relative_path(current_relative_path)
    if safe_relative_path:
        return redirect(url_for("storage", path=safe_relative_path))
    return redirect(url_for("storage"))


def redirect_to_admin_files(current_relative_path=""):
    safe_relative_path = sanitize_relative_path(current_relative_path)
    if safe_relative_path:
        return redirect(url_for("admin_file_browser", path=safe_relative_path))
    return redirect(url_for("admin_file_browser"))


def redirect_to_profile(return_to=""):
    params = {}
    target = (return_to or "").strip()
    if target:
        params["return_to"] = target
    return redirect(url_for("profile", **params))


@app.route('/', methods=['GET', 'POST'])
def login():
    ensure_runtime_schema()
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
    ensure_runtime_schema()
    hour = datetime.now().hour
    theme_preference = get_theme_preference(current_user)

    if 6 <= hour < 12:
        greeting = "Доброе утро"
    elif 12 <= hour < 18:
        greeting = "Добрый день"
    elif 18 <= hour < 24:
        greeting = "Добрый вечер"
    else:
        greeting = "Доброй ночи"

    ensure_default_shared_folders()
    sync_legacy_personal_files(current_user)
    upload_root = get_upload_root()
    total, used, free = shutil.disk_usage(upload_root)

    return render_template(
        'dashboard.html',
        greeting=greeting,
        percent=int((used / total) * 100),
        username=current_user.username,
        shared_folders=get_accessible_shared_folders(current_user),
        total_gb=round(total / (1024 ** 3), 1),
        used_gb=round(used / (1024 ** 3), 1),
        free_gb=round(free / (1024 ** 3), 1),
        today_label=datetime.now().strftime('%d.%m.%Y'),
        avatar_text=(current_user.username[:2] or "U").upper(),
        avatar_url=get_avatar_url(current_user),
        role_label=get_role_label(current_user),
        theme_preference=theme_preference,
        active_theme=resolve_active_theme(current_user, hour=hour),
    )


@app.route('/shared/<int:folder_id>')
@login_required
def shared_explorer(folder_id):
    ensure_default_shared_folders()
    folder = get_folder_by_id(folder_id)
    _, current_directory, current_relative_path = resolve_shared_path(folder, request.args.get("path", ""))
    if not current_directory.exists() or not current_directory.is_dir():
        abort(404)

    clipboard = get_clipboard("shared_clipboard")
    parent_relative_path = ""
    if current_relative_path:
        parent_relative_path = "/".join(current_relative_path.split("/")[:-1])

    return render_template(
        "shared_explorer.html",
        folder=folder,
        folder_name=folder.name,
        shared_folders=get_accessible_shared_folders(current_user),
        items=build_shared_items(folder, current_relative_path),
        breadcrumbs=build_shared_breadcrumbs(folder, current_relative_path),
        current_relative_path=current_relative_path,
        parent_relative_path=parent_relative_path,
        clipboard=clipboard,
        clipboard_count=len(clipboard["items"]) if clipboard else 0,
        clipboard_mode=clipboard["mode"] if clipboard else "",
        active_theme=resolve_active_theme(current_user),
    )


@app.route('/shared/<int:folder_id>/upload', methods=['POST'])
@login_required
def shared_upload(folder_id):
    folder = get_folder_by_id(folder_id)
    _, destination_directory, current_relative_path = resolve_shared_path(folder, request.form.get("current_path", ""))
    if not destination_directory.is_dir():
        abort(404)

    uploaded_files = [file for file in request.files.getlist("files") if file and file.filename]
    if not uploaded_files:
        flash("Сначала выберите файлы на компьютере.", "error")
        return redirect_to_shared(folder, current_relative_path)

    saved_count = 0
    for uploaded_file in uploaded_files:
        original_name = Path(uploaded_file.filename).name
        safe_name = sanitize_entry_name(original_name)
        if not safe_name:
            continue

        destination_path = create_available_path(destination_directory, safe_name)
        uploaded_file.save(destination_path)
        saved_count += 1

    flash(f"Загружено файлов: {saved_count}." if saved_count else "Не удалось сохранить выбранные файлы.", "success" if saved_count else "error")
    return redirect_to_shared(folder, current_relative_path)


@app.route('/shared/<int:folder_id>/action', methods=['POST'])
@login_required
def shared_action(folder_id):
    folder = get_folder_by_id(folder_id)
    action = request.form.get("action", "")
    current_relative_path = request.form.get("current_path", "")
    _, current_directory, current_relative_path = resolve_shared_path(folder, current_relative_path)
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
            return redirect_to_shared(folder, current_relative_path)
        create_available_path(current_directory, folder_name).mkdir(parents=False, exist_ok=False)
        flash("Папка создана.", "success")
        return redirect_to_shared(folder, current_relative_path)
    if action in {"copy", "cut"}:
        if not selected_relative_paths:
            flash("Выберите хотя бы один файл или папку.", "error")
            return redirect_to_shared(folder, current_relative_path)
        session["shared_clipboard"] = {
            "mode": action,
            "items": [{"folder_id": folder.id, "relative_path": path} for path in selected_relative_paths],
        }
        flash("Элементы добавлены в буфер обмена.", "success")
        return redirect_to_shared(folder, current_relative_path)
    if action == "paste":
        clipboard = get_clipboard("shared_clipboard")
        if not clipboard:
            flash("Буфер обмена пуст.", "error")
            return redirect_to_shared(folder, current_relative_path)
        transferred_count = 0
        for item in clipboard["items"]:
            source_folder = get_folder_by_id(item["folder_id"])
            _, source_path, _ = resolve_shared_path(source_folder, item["relative_path"])
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
        flash("Буфер обмена вставлен." if transferred_count else "Не удалось вставить выбранные элементы.", "success" if transferred_count else "error")
        return redirect_to_shared(folder, current_relative_path)
    if action == "rename":
        if len(selected_relative_paths) != 1:
            flash("Для переименования выберите один файл или одну папку.", "error")
            return redirect_to_shared(folder, current_relative_path)
        _, target_path, _ = resolve_shared_path(folder, selected_relative_paths[0])
        if not target_path.exists():
            flash("Выбранный элемент не найден.", "error")
            return redirect_to_shared(folder, current_relative_path)
        renamed_path, error_message = rename_entry(target_path, request.form.get("new_name", ""))
        if error_message:
            flash(error_message, "error")
            return redirect_to_shared(folder, current_relative_path)
        flash(f"Элемент переименован в «{renamed_path.name}».", "success")
        return redirect_to_shared(folder, current_relative_path)
    if action == "delete":
        if not selected_relative_paths:
            flash("Сначала отметьте элементы для удаления.", "error")
            return redirect_to_shared(folder, current_relative_path)
        deleted_count = 0
        for relative_path in selected_relative_paths:
            _, target_path, _ = resolve_shared_path(folder, relative_path)
            if not target_path.exists():
                continue
            if target_path.is_dir():
                shutil.rmtree(target_path)
            else:
                target_path.unlink()
            deleted_count += 1
        flash(f"Удалено элементов: {deleted_count}.", "success")
        return redirect_to_shared(folder, current_relative_path)
    flash("Неизвестное действие.", "error")
    return redirect_to_shared(folder, current_relative_path)
@app.route('/shared/<int:folder_id>/download')
@login_required
def shared_download(folder_id):
    folder = get_folder_by_id(folder_id)
    _, target_path, _ = resolve_shared_path(folder, request.args.get("path", ""))
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
    ensure_runtime_schema()
    ensure_default_shared_folders()
    ensure_user_permission_record(current_user)
    sync_legacy_personal_files(current_user)
    return render_template("profile.html", **build_profile_context())


@app.route('/profile/password', methods=['POST'])
@login_required
def update_password():
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not check_password_hash(current_user.password, current_password):
        flash("Текущий пароль введен неверно.", "error")
        return redirect_to_profile("password-settings")
    if len(new_password) < 4:
        flash("Новый пароль должен содержать минимум 4 символа.", "error")
        return redirect_to_profile("password-settings")
    if new_password != confirm_password:
        flash("Подтверждение пароля не совпадает.", "error")
        return redirect_to_profile("password-settings")

    current_user.password = generate_password_hash(new_password)
    db.session.commit()
    flash("Пароль обновлен.", "success")
    return redirect_to_profile("password-settings")


@app.route('/profile/avatar', methods=['POST'])
@login_required
def update_avatar():
    uploaded_file = request.files.get("avatar")
    if not uploaded_file or not uploaded_file.filename:
        flash("Сначала выберите изображение.", "error")
        return redirect_to_profile("avatar-settings")

    if not save_avatar(current_user, uploaded_file):
        flash("Разрешены только PNG, JPG, JPEG, GIF или WEBP.", "error")
        return redirect_to_profile("avatar-settings")

    flash("Аватар обновлен.", "success")
    return redirect_to_profile("avatar-settings")


@app.route('/dashboard/theme', methods=['POST'])
@login_required
def dashboard_theme():
    ensure_runtime_schema()
    selected_theme = normalize_theme_preference(request.form.get("theme", "light"))
    if selected_theme not in {"light", "dark"}:
        selected_theme = "light"

    current_user.theme_preference = selected_theme
    db.session.commit()
    return redirect(url_for("dashboard"))


@app.route('/profile/theme', methods=['POST'])
@login_required
def update_theme():
    ensure_runtime_schema()
    current_user.theme_preference = normalize_theme_preference(
        request.form.get("theme_preference", "light")
    )
    db.session.commit()
    flash("Настройки темы обновлены.", "success")
    return redirect_to_profile("theme-settings")


@app.route('/shared-folders/create', methods=['POST'])
@login_required
def create_shared_folder_route():
    require_shared_folder_creator()
    folder = create_shared_folder(request.form.get("name", ""), grant_user=current_user)

    if not folder:
        flash("Укажите корректное имя общей папки.", "error")
        return redirect_to_profile("shared-folder-create")
    else:
        flash(f"Общая папка «{folder.name}» создана.", "success")
        return redirect_to_profile(f"shared-folder-{folder.id}")


@app.route('/shared-folders/<int:folder_id>/rename', methods=['POST'])
@login_required
def rename_shared_folder_route(folder_id):
    require_shared_folder_editor()
    folder = SharedFolder.query.get_or_404(folder_id)
    new_name = sanitize_entry_name(request.form.get("name", ""))

    if not new_name:
        flash("Укажите корректное новое имя папки.", "error")
        return redirect_to_profile(f"shared-folder-{folder.id}")

    folder.name = new_name
    db.session.commit()
    flash("Название общей папки обновлено.", "success")
    return redirect_to_profile(f"shared-folder-{folder.id}")


@app.route('/admin/users/create', methods=['POST'])
@login_required
def admin_create_user():
    require_admin()

    username = sanitize_entry_name(request.form.get("username", ""))
    password = request.form.get("password", "")
    is_admin = request.form.get("is_admin") == "on"

    if not username:
        flash("Укажите корректный логин нового пользователя.", "error")
        return redirect_to_profile("admin-create-user")
    if len(password) < 4:
        flash("Пароль нового пользователя должен содержать минимум 4 символа.", "error")
        return redirect_to_profile("admin-create-user")
    if User.query.filter_by(username=username).first():
        flash("Пользователь с таким логином уже существует.", "error")
        return redirect_to_profile("admin-create-user")

    user = User(username=username, password=generate_password_hash(password), is_admin=is_admin)
    db.session.add(user)
    db.session.commit()
    ensure_user_permission_record(user)
    flash(f"Пользователь «{username}» создан.", "success")
    return redirect_to_profile(f"user-{user.id}")


@app.route('/admin/users/<int:user_id>/update', methods=['POST'])
@login_required
def admin_update_user(user_id):
    require_admin()
    user = User.query.get_or_404(user_id)
    permission = ensure_user_permission_record(user)
    new_is_admin = request.form.get("is_admin") == "on"

    if user.id == current_user.id and not new_is_admin and User.query.filter_by(is_admin=True).count() <= 1:
        flash("Нельзя снять права у последнего администратора.", "error")
        return redirect_to_profile(f"user-{user.id}")

    user.is_admin = new_is_admin
    permission.can_create_shared_folders = request.form.get("can_create_shared_folders") == "on"
    permission.can_edit_shared_folders = request.form.get("can_edit_shared_folders") == "on"

    selected_folder_ids = {
        int(folder_id)
        for folder_id in request.form.getlist("folder_access")
        if folder_id.isdigit() and SharedFolder.query.get(int(folder_id))
    }
    update_user_folder_access(user, selected_folder_ids)

    db.session.add(permission)
    db.session.commit()
    flash(f"Права пользователя «{user.username}» обновлены.", "success")
    return redirect_to_profile(f"user-{user.id}")


@app.route('/avatar/<int:user_id>')
@login_required
def avatar(user_id):
    user = User.query.get_or_404(user_id)
    avatar_path = get_avatar_path(user)
    if not avatar_path or not avatar_path.exists():
        abort(404)
    return send_file(avatar_path)


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
    sync_legacy_personal_files(current_user)
    _, current_directory, current_relative_path = resolve_personal_path(current_user, request.args.get("path", ""))
    if not current_directory.exists() or not current_directory.is_dir():
        abort(404)

    clipboard = get_clipboard("personal_clipboard")
    parent_relative_path = ""
    if current_relative_path:
        parent_relative_path = "/".join(current_relative_path.split("/")[:-1])

    return render_template(
        "personal_explorer.html",
        username=current_user.username,
        items=build_personal_items(current_user, current_relative_path),
        breadcrumbs=build_personal_breadcrumbs(current_relative_path),
        current_relative_path=current_relative_path,
        parent_relative_path=parent_relative_path,
        clipboard=clipboard,
        clipboard_count=len(clipboard["items"]) if clipboard else 0,
        clipboard_mode=clipboard["mode"] if clipboard else "",
        active_theme=resolve_active_theme(current_user),
    )


@app.route('/storage/upload', methods=['POST'])
@login_required
def storage_upload():
    sync_legacy_personal_files(current_user)
    _, destination_directory, current_relative_path = resolve_personal_path(current_user, request.form.get("current_path", ""))
    if not destination_directory.is_dir():
        abort(404)

    uploaded_files = [file for file in request.files.getlist("files") if file and file.filename]
    if not uploaded_files:
        flash("Сначала выберите файлы на компьютере.", "error")
        return redirect_to_personal(current_relative_path)

    saved_count = 0
    for uploaded_file in uploaded_files:
        safe_name = sanitize_entry_name(Path(uploaded_file.filename).name)
        if not safe_name:
            continue

        destination_path = create_available_path(destination_directory, safe_name)
        uploaded_file.save(destination_path)
        saved_count += 1

    if saved_count:
        flash(f"Загружено файлов: {saved_count}.", "success")
    else:
        flash("Не удалось сохранить выбранные файлы.", "error")

    return redirect_to_personal(current_relative_path)


@app.route('/storage/action', methods=['POST'])
@login_required
def storage_action():
    sync_legacy_personal_files(current_user)
    action = request.form.get("action", "")
    current_relative_path = request.form.get("current_path", "")
    _, current_directory, current_relative_path = resolve_personal_path(current_user, current_relative_path)
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
            return redirect_to_personal(current_relative_path)
        create_available_path(current_directory, folder_name).mkdir(parents=False, exist_ok=False)
        flash("Папка создана.", "success")
        return redirect_to_personal(current_relative_path)
    if action in {"copy", "cut"}:
        if not selected_relative_paths:
            flash("Выберите хотя бы один файл или папку.", "error")
            return redirect_to_personal(current_relative_path)
        session["personal_clipboard"] = {"mode": action, "items": selected_relative_paths}
        flash("Элементы добавлены в буфер обмена.", "success")
        return redirect_to_personal(current_relative_path)
    if action == "paste":
        clipboard = get_clipboard("personal_clipboard")
        if not clipboard:
            flash("Буфер обмена пуст.", "error")
            return redirect_to_personal(current_relative_path)
        transferred_count = 0
        for relative_path in clipboard["items"]:
            _, source_path, _ = resolve_personal_path(current_user, relative_path)
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
            session.pop("personal_clipboard", None)
        flash("Буфер обмена вставлен." if transferred_count else "Не удалось вставить выбранные элементы.", "success" if transferred_count else "error")
        return redirect_to_personal(current_relative_path)
    if action == "rename":
        if len(selected_relative_paths) != 1:
            flash("Для переименования выберите один файл или одну папку.", "error")
            return redirect_to_personal(current_relative_path)
        _, target_path, _ = resolve_personal_path(current_user, selected_relative_paths[0])
        if not target_path.exists():
            flash("Выбранный элемент не найден.", "error")
            return redirect_to_personal(current_relative_path)
        renamed_path, error_message = rename_entry(target_path, request.form.get("new_name", ""))
        if error_message:
            flash(error_message, "error")
            return redirect_to_personal(current_relative_path)
        flash(f"Элемент переименован в «{renamed_path.name}».", "success")
        return redirect_to_personal(current_relative_path)
    if action == "delete":
        if not selected_relative_paths:
            flash("Сначала отметьте элементы для удаления.", "error")
            return redirect_to_personal(current_relative_path)
        deleted_count = 0
        for relative_path in selected_relative_paths:
            _, target_path, _ = resolve_personal_path(current_user, relative_path)
            if not target_path.exists():
                continue
            if target_path.is_dir():
                shutil.rmtree(target_path)
            else:
                target_path.unlink()
            deleted_count += 1
        flash(f"Удалено элементов: {deleted_count}.", "success")
        return redirect_to_personal(current_relative_path)
    flash("Неизвестное действие.", "error")
    return redirect_to_personal(current_relative_path)
@app.route('/storage/download')
@login_required
def storage_download():
    sync_legacy_personal_files(current_user)
    _, target_path, _ = resolve_personal_path(current_user, request.args.get("path", ""))
    if not target_path.exists() or not target_path.is_file():
        abort(404)
    return send_file(target_path, as_attachment=True, download_name=target_path.name)


@app.route('/admin/files')
@login_required
def admin_file_browser():
    require_admin()
    _, current_directory, current_relative_path = resolve_admin_path(request.args.get("path", ""))
    if not current_directory.exists() or not current_directory.is_dir():
        abort(404)

    parent_relative_path = ""
    if current_relative_path:
        parent_relative_path = "/".join(current_relative_path.split("/")[:-1])

    return render_template(
        "admin_file_browser.html",
        items=build_admin_items(current_relative_path),
        breadcrumbs=build_admin_breadcrumbs(current_relative_path),
        current_relative_path=current_relative_path,
        parent_relative_path=parent_relative_path,
        active_theme=resolve_active_theme(current_user),
    )


@app.route('/admin/files/action', methods=['POST'])
@login_required
def admin_file_action():
    require_admin()
    action = request.form.get("action", "")
    current_relative_path = request.form.get("current_path", "")
    _, current_directory, current_relative_path = resolve_admin_path(current_relative_path)
    if not current_directory.is_dir():
        abort(404)
    selected_relative_paths = []
    for relative_path in request.form.getlist("selected_paths"):
        safe_relative_path = sanitize_relative_path(relative_path)
        if safe_relative_path:
            selected_relative_paths.append(safe_relative_path)
    if action == "rename":
        if len(selected_relative_paths) != 1:
            flash("Для переименования выберите один файл или одну папку.", "error")
            return redirect_to_admin_files(current_relative_path)
        _, target_path, _ = resolve_admin_path(selected_relative_paths[0])
        if not target_path.exists():
            flash("Выбранный элемент не найден.", "error")
            return redirect_to_admin_files(current_relative_path)
        renamed_path, error_message = rename_entry(target_path, request.form.get("new_name", ""))
        if error_message:
            flash(error_message, "error")
            return redirect_to_admin_files(current_relative_path)
        flash(f"Элемент переименован в «{renamed_path.name}».", "success")
        return redirect_to_admin_files(current_relative_path)
    if action == "delete":
        if not selected_relative_paths:
            flash("Сначала отметьте элементы для удаления.", "error")
            return redirect_to_admin_files(current_relative_path)
        deleted_count = 0
        for relative_path in selected_relative_paths:
            _, target_path, _ = resolve_admin_path(relative_path)
            if not target_path.exists():
                continue
            if target_path.is_dir():
                shutil.rmtree(target_path)
            else:
                target_path.unlink()
            deleted_count += 1
        flash(f"Удалено элементов: {deleted_count}.", "success")
        return redirect_to_admin_files(current_relative_path)
    flash("Неизвестное действие.", "error")
    return redirect_to_admin_files(current_relative_path)
@app.route('/admin/files/download')
@login_required
def admin_file_download():
    require_admin()
    _, target_path, _ = resolve_admin_path(request.args.get("path", ""))
    if not target_path.exists() or not target_path.is_file():
        abort(404)
    return send_file(target_path, as_attachment=True, download_name=target_path.name)


@app.route('/logout')
def logout():
    logout_user()
    return redirect('/')


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        ensure_runtime_schema()
        ensure_default_shared_folders()
    app.run(host="0.0.0.0", port=5000)
