from app import app, db, ensure_user_permission_record
from models import User
from werkzeug.security import generate_password_hash

with app.app_context():
    existing_user = User.query.filter_by(username="admin").first()
    if existing_user:
        ensure_user_permission_record(existing_user)
        print("Admin already exists")
        raise SystemExit(0)

    user = User(
        username="admin",
        password=generate_password_hash("1234"),
        is_admin=True
    )

    db.session.add(user)
    db.session.commit()
    ensure_user_permission_record(user)

    print("Admin created")
