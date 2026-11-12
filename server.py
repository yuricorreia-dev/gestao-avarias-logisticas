#!/usr/bin/env python3
import base64
import csv
import contextvars
from contextlib import contextmanager
import datetime
import email.message
import hashlib
import hmac
import io
import json
import math
import os
import re
import secrets
import sqlite3
import smtplib
import threading
import time
import unicodedata
import uuid
from datetime import date
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("AVARIAS_DB", ROOT / "avarias.db"))
IDENTITY_DB_PATH = Path(os.environ.get("AVARIAS_IDENTITY_DB", ROOT / "identity.db"))
TENANT_DB_DIR = Path(os.environ.get("AVARIAS_TENANT_DB_DIR", ROOT / "tenant-data"))
LEGACY_CORPORATION_ID = "legacy"
ACTIVE_DATABASE = contextvars.ContextVar("active_corporation_database", default=None)
ACTIVE_USER = contextvars.ContextVar("active_identity_user", default=None)
COOKIE_NAME = "avarias_session"
SESSION_SECONDS = 12 * 60 * 60
ROLES = {"admin", "analyst", "approver"}
INVITATION_DAYS = 7
TOKEN_SECONDS = 60 * 60
LOGIN_ATTEMPTS = {}
LOGIN_LOCK = threading.Lock()
IDENTITY_SECRET_PATH = IDENTITY_DB_PATH.with_suffix(".secret")
LOGIN_LIMITS = {}
TENANT_DB_LOCK = threading.Lock()
INITIALIZED_TENANT_DBS = set()


@contextmanager
def connect(path=None):
    database = Path(path or ACTIVE_DATABASE.get() or DB_PATH)
    database.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def identity_connect():
    IDENTITY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(IDENTITY_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


DEFAULT_ROLE_PERMISSIONS = {
    "owner": ["manage_corporation", "manage_users", "manage_roles", "manage_departments", "manage_targets", "manage_products", "view_all_sectors", "view_financials", "create_occurrences", "edit_own_occurrences", "edit_any_occurrence", "analyze_occurrences", "approve_discount", "cancel_occurrence", "export_reports", "view_audit", "manage_security"],
    "admin": ["manage_users", "manage_roles", "manage_departments", "manage_targets", "manage_products", "view_all_sectors", "view_financials", "create_occurrences", "edit_any_occurrence", "analyze_occurrences", "export_reports", "view_audit", "manage_security"],
    "manager": ["view_all_sectors", "view_financials", "analyze_occurrences", "export_reports"],
    "supervisor": ["view_assigned_sectors", "create_occurrences", "edit_own_occurrences", "edit_assigned_occurrences", "analyze_occurrences"],
    "analyst": ["view_assigned_sectors", "create_occurrences", "edit_own_occurrences", "analyze_occurrences"],
    "operator": ["view_assigned_sectors", "create_occurrences", "edit_own_occurrences"],
    "viewer": ["view_assigned_sectors"],
    "legacy_admin": ["manage_users", "manage_roles", "manage_departments", "manage_targets", "manage_products", "view_all_sectors", "view_financials", "create_occurrences", "edit_any_occurrence", "analyze_occurrences", "approve_discount", "cancel_occurrence", "export_reports", "view_audit", "manage_security"],
    "legacy_analyst": ["view_all_sectors", "create_occurrences", "edit_own_occurrences", "analyze_occurrences"],
    "legacy_approver": ["view_all_sectors", "create_occurrences", "analyze_occurrences", "approve_discount", "cancel_occurrence"],
}
DEFAULT_ROLE_NAMES = {
    "owner": "Proprietário", "admin": "Administrador", "manager": "Gestor", "supervisor": "Supervisor",
    "analyst": "Analista", "operator": "Operador", "viewer": "Visualizador",
    "legacy_admin": "Administrador legado", "legacy_analyst": "Analista legado", "legacy_approver": "Aprovador legado",
}


def init_identity_db():
    with identity_connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS corporations (
                id TEXT PRIMARY KEY, slug TEXT UNIQUE NOT NULL, legal_name TEXT NOT NULL,
                display_name TEXT NOT NULL, cnpj TEXT UNIQUE, email TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT '', logo_path TEXT NOT NULL DEFAULT '', logo_type TEXT NOT NULL DEFAULT '',
                logo_data BLOB, status TEXT NOT NULL,
                email_verified_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS identity_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, email TEXT UNIQUE,
                display_name TEXT NOT NULL, password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                email_verified_at TEXT, last_login_at TEXT, mfa_secret TEXT, mfa_enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS departments (
                id TEXT PRIMARY KEY, corporation_id TEXT NOT NULL REFERENCES corporations(id),
                name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(corporation_id,name)
            );
            CREATE TABLE IF NOT EXISTS roles (
                id TEXT PRIMARY KEY, corporation_id TEXT NOT NULL REFERENCES corporations(id),
                code TEXT NOT NULL, name TEXT NOT NULL, protected INTEGER NOT NULL DEFAULT 0,
                landing_page TEXT NOT NULL DEFAULT 'dashboard', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(corporation_id,code)
            );
            CREATE TABLE IF NOT EXISTS permissions (
                code TEXT PRIMARY KEY, description TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS role_permissions (
                role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                permission_code TEXT NOT NULL REFERENCES permissions(code),
                PRIMARY KEY(role_id,permission_code)
            );
            CREATE TABLE IF NOT EXISTS corporation_members (
                id TEXT PRIMARY KEY, corporation_id TEXT NOT NULL REFERENCES corporations(id),
                user_id INTEGER NOT NULL REFERENCES identity_users(id), status TEXT NOT NULL DEFAULT 'active',
                joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(corporation_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS member_roles (
                member_id TEXT NOT NULL REFERENCES corporation_members(id) ON DELETE CASCADE,
                role_id TEXT NOT NULL REFERENCES roles(id),
                PRIMARY KEY(member_id,role_id)
            );
            CREATE TABLE IF NOT EXISTS member_departments (
                member_id TEXT NOT NULL REFERENCES corporation_members(id) ON DELETE CASCADE,
                department_id TEXT NOT NULL REFERENCES departments(id), is_primary INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(member_id,department_id)
            );
            CREATE TABLE IF NOT EXISTS invitations (
                id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, corporation_id TEXT NOT NULL REFERENCES corporations(id),
                email TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '', role_id TEXT NOT NULL REFERENCES roles(id),
                department_ids TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'pending',
                expires_at TEXT NOT NULL, invited_by INTEGER NOT NULL REFERENCES identity_users(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL REFERENCES identity_users(id),
                corporation_id TEXT NOT NULL REFERENCES corporations(id), expires_at TEXT NOT NULL,
                revoked_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS auth_tokens (
                id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, purpose TEXT NOT NULL,
                user_id INTEGER NOT NULL REFERENCES identity_users(id), corporation_id TEXT REFERENCES corporations(id),
                expires_at TEXT NOT NULL, used_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS identity_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, corporation_id TEXT REFERENCES corporations(id),
                actor_user_id INTEGER REFERENCES identity_users(id), target_user_id INTEGER REFERENCES identity_users(id),
                action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS corporation_settings (
                corporation_id TEXT PRIMARY KEY REFERENCES corporations(id), require_admin_mfa INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_membership_user ON corporation_members(user_id,status);
            CREATE INDEX IF NOT EXISTS idx_session_user ON sessions(user_id,revoked_at);
            CREATE INDEX IF NOT EXISTS idx_invite_email ON invitations(email,status);
        """)
        permission_descriptions = {
            "manage_corporation": "Administrar dados e segurança da corporação", "manage_users": "Convidar e administrar colaboradores",
            "manage_roles": "Criar cargos e configurar permissões", "manage_departments": "Administrar setores",
            "manage_targets": "Configurar limites e metas", "manage_products": "Cadastrar e importar produtos",
            "view_all_sectors": "Consultar todos os setores", "view_assigned_sectors": "Consultar setores vinculados",
            "view_financials": "Consultar valores financeiros", "create_occurrences": "Criar lançamentos",
            "edit_own_occurrences": "Editar lançamentos próprios", "edit_any_occurrence": "Editar lançamentos da corporação",
            "edit_assigned_occurrences": "Editar lançamentos dos setores vinculados", "analyze_occurrences": "Analisar ocorrências",
            "approve_discount": "Aprovar desconto financeiro", "cancel_occurrence": "Cancelar lançamentos",
            "export_reports": "Exportar relatórios", "view_audit": "Consultar auditoria", "manage_security": "Configurar segurança e MFA",
        }
        db.executemany("INSERT OR IGNORE INTO permissions(code,description) VALUES(?,?)", permission_descriptions.items())
        db.execute("""INSERT OR IGNORE INTO corporations(id,slug,legal_name,display_name,email,status,email_verified_at)
            VALUES(?,?,?,?,?,'active',CURRENT_TIMESTAMP)""",
            (LEGACY_CORPORATION_ID, "ambiente-legado", "Ambiente legado", "Ambiente legado", "legacy@local.invalid"))
        for code, permissions in DEFAULT_ROLE_PERMISSIONS.items():
            role_id = f"{LEGACY_CORPORATION_ID}:{code}"
            db.execute("INSERT OR IGNORE INTO roles(id,corporation_id,code,name,protected) VALUES(?,?,?,?,1)",
                (role_id, LEGACY_CORPORATION_ID, code, DEFAULT_ROLE_NAMES[code]))
            db.executemany("INSERT OR IGNORE INTO role_permissions(role_id,permission_code) VALUES(?,?)",
                [(role_id, permission) for permission in permissions])
        db.execute("INSERT OR IGNORE INTO corporation_settings(corporation_id) VALUES(?)", (LEGACY_CORPORATION_ID,))


def migrate_legacy_identity():
    with identity_connect() as identity, connect(DB_PATH) as legacy:
        for old_user in legacy.execute("SELECT id,username,display_name,password_hash,role,active FROM users"):
            identity.execute("""INSERT OR IGNORE INTO identity_users(id,username,email,display_name,password_hash,active,email_verified_at)
                VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)""", (old_user["id"], old_user["username"], None, old_user["display_name"],
                old_user["password_hash"], old_user["active"]))
            role_code = {"admin": "legacy_admin", "analyst": "legacy_analyst", "approver": "legacy_approver"}.get(old_user["role"], "viewer")
            member_id = f"legacy:{old_user['id']}"
            identity.execute("INSERT OR IGNORE INTO corporation_members(id,corporation_id,user_id,status) VALUES(?,?,?,?)",
                (member_id, LEGACY_CORPORATION_ID, old_user["id"], "active" if old_user["active"] else "suspended"))
            identity.execute("INSERT OR IGNORE INTO member_roles(member_id,role_id) VALUES(?,?)",
                (member_id, f"{LEGACY_CORPORATION_ID}:{role_code}"))


def identity_audit(db, corporation_id, actor_id, action, details, target_id=None):
    db.execute("""INSERT INTO identity_audit_logs(corporation_id,actor_user_id,target_user_id,action,details)
        VALUES(?,?,?,?,?)""", (corporation_id, actor_id, target_id, action, json.dumps(details, ensure_ascii=False, default=str)))


def member_context(db, user_id, corporation_id):
    member = db.execute("""SELECT m.id,m.status,c.id AS corporation_id,c.slug,c.display_name,c.status AS corporation_status,
        u.id AS user_id,u.username,u.email,u.display_name AS user_name,u.active,u.mfa_enabled
        FROM corporation_members m JOIN corporations c ON c.id=m.corporation_id
        JOIN identity_users u ON u.id=m.user_id WHERE m.user_id=? AND m.corporation_id=?""", (user_id, corporation_id)).fetchone()
    if not member or member["status"] != "active" or not member["active"] or member["corporation_status"] != "active":
        return None
    roles = [dict(row) for row in db.execute("""SELECT r.id,r.code,r.name,r.landing_page FROM member_roles mr
        JOIN roles r ON r.id=mr.role_id WHERE mr.member_id=?""", (member["id"],))]
    departments = [dict(row) for row in db.execute("""SELECT d.id,d.name,md.is_primary FROM member_departments md
        JOIN departments d ON d.id=md.department_id WHERE md.member_id=? AND d.active=1""", (member["id"],))]
    permissions = {row[0] for row in db.execute("""SELECT DISTINCT rp.permission_code FROM member_roles mr
        JOIN role_permissions rp ON rp.role_id=mr.role_id WHERE mr.member_id=?""", (member["id"],))}
    role_codes = {role["code"] for role in roles}
    legacy_role = "admin" if permissions & {"manage_users", "manage_corporation"} else "approver" if "approve_discount" in permissions else "analyst"
    database = ensure_operational_database(corporation_id)
    ACTIVE_DATABASE.set(database)
    mirror_tenant_user(database, member["user_id"], member["username"] or member["email"], member["user_name"],
        legacy_role, member["active"])
    return {"id": member["user_id"], "username": member["username"] or member["email"], "email": member["email"],
        "display_name": member["user_name"], "role": legacy_role, "role_code": roles[0]["code"] if roles else "viewer",
        "roles": roles, "permissions": sorted(permissions), "departments": departments, "active": bool(member["active"]),
        "corporation_id": corporation_id, "corporation_name": member["display_name"], "corporation_slug": member["slug"],
        "membership_id": member["id"]}


def mirror_tenant_user(database, user_id, username, display_name, role, active):
    with connect(database) as db:
        db.execute("""INSERT INTO users(id,username,display_name,password_hash,role,active)
            VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username,
            display_name=excluded.display_name,role=excluded.role,active=excluded.active""",
            (user_id, username, display_name, "managed-by-central-identity", role, int(bool(active))))


def issue_identity_session(db, user_id, corporation_id):
    token = make_token(); session_id = str(uuid.uuid4())
    db.execute("INSERT INTO sessions(id,token_hash,user_id,corporation_id,expires_at) VALUES(?,?,?,?,?)",
        (session_id, hash_token(token), user_id, corporation_id, expiry_timestamp(SESSION_SECONDS)))
    return token


def issue_auth_token(db, user_id, purpose, corporation_id=None, seconds=TOKEN_SECONDS):
    token = make_token(); token_id = str(uuid.uuid4())
    db.execute("INSERT INTO auth_tokens(id,token_hash,purpose,user_id,corporation_id,expires_at) VALUES(?,?,?,?,?,?)",
        (token_id, hash_token(token), purpose, user_id, corporation_id, expiry_timestamp(seconds)))
    return token


def create_corporation_roles(db, corporation_id):
    for code, permissions in DEFAULT_ROLE_PERMISSIONS.items():
        if code.startswith("legacy_"): continue
        role_id = f"{corporation_id}:{code}"
        db.execute("INSERT OR IGNORE INTO roles(id,corporation_id,code,name,protected) VALUES(?,?,?,?,?)",
            (role_id, corporation_id, code, DEFAULT_ROLE_NAMES[code], int(code in ("owner", "admin", "viewer"))))
        db.executemany("INSERT OR IGNORE INTO role_permissions(role_id,permission_code) VALUES(?,?)",
            [(role_id, permission) for permission in permissions])


def corporate_role(db, corporation_id, code):
    row = db.execute("SELECT id FROM roles WHERE corporation_id=? AND code=?", (corporation_id, code)).fetchone()
    if not row: raise ValueError("Cargo não encontrado nesta corporação")
    return row["id"]


def memberships_for_user(db, user_id):
    rows = db.execute("""SELECT c.id,c.slug,c.display_name,c.status,m.id AS member_id,r.code AS role_code,r.name AS role_name
        FROM corporation_members m JOIN corporations c ON c.id=m.corporation_id
        JOIN member_roles mr ON mr.member_id=m.id JOIN roles r ON r.id=mr.role_id
        WHERE m.user_id=? AND m.status='active' AND c.status='active' ORDER BY c.display_name""", (user_id,)).fetchall()
    grouped = {}
    for row in rows:
        item = grouped.setdefault(row["id"], {"id": row["id"], "slug": row["slug"], "name": row["display_name"],
            "roles": [], "member_id": row["member_id"]})
        item["roles"].append({"code": row["role_code"], "name": row["role_name"]})
    return list(grouped.values())


def cookie_header(token, max_age=SESSION_SECONDS):
    secure = "; Secure" if os.environ.get("APP_ENV") == "production" else ""
    return f"{COOKIE_NAME}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}{secure}"


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def expiry_timestamp(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


def make_token():
    return secrets.token_urlsafe(32)


def normalize_email(value):
    email = str(value or "").strip().casefold()
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Informe um e-mail válido")
    return email


def normalize_slug(value):
    slug = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    if len(slug) < 3 or len(slug) > 70:
        raise ValueError("Nome fantasia deve gerar um identificador de 3 a 70 caracteres")
    return slug


def valid_cnpj(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits: return True
    if len(digits) != 14 or len(set(digits)) == 1: return False
    def check_digit(prefix, weights):
        remainder = sum(int(number) * weight for number, weight in zip(prefix, weights)) % 11
        return "0" if remainder < 2 else str(11 - remainder)
    first = check_digit(digits[:12], (5,4,3,2,9,8,7,6,5,4,3,2))
    second = check_digit(digits[:12] + first, (6,5,4,3,2,9,8,7,6,5,4,3,2))
    return digits[-2:] == first + second


def totp_code(secret, at_time=None):
    moment = int(at_time if at_time is not None else time.time()) // 30
    key = base64.b32decode(secret.upper() + "=" * ((8 - len(secret) % 8) % 8))
    digest = hmac.new(key, moment.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = int.from_bytes(digest[offset:offset + 4], "big") & 0x7fffffff
    return f"{value % 1_000_000:06d}"


def verify_totp(secret, code):
    supplied = str(code or "").strip()
    return bool(secret and supplied) and any(hmac.compare_digest(totp_code(secret, time.time() + delta * 30), supplied) for delta in (-1, 0, 1))


def send_notification(address, subject, text):
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        if os.environ.get("APP_ENV", "development") == "production":
            raise RuntimeError("E-mail transacional não configurado. Configure SMTP antes de ativar este fluxo.")
        return False
    message = email.message.EmailMessage()
    message["Subject"] = subject; message["From"] = os.environ.get("SMTP_FROM", "noreply@localhost"); message["To"] = address
    message.set_content(text)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=15) as smtp:
        smtp.starttls()
        username = os.environ.get("SMTP_USERNAME"); password = os.environ.get("SMTP_PASSWORD")
        if username: smtp.login(username, password or "")
        smtp.send_message(message)
    return True


def corporation_database(corporation_id):
    if corporation_id == LEGACY_CORPORATION_ID:
        return DB_PATH
    try:
        tenant_id = uuid.UUID(corporation_id).hex
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Identificador de corporação inválido")
    return TENANT_DB_DIR / f"{tenant_id}.db"


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return base64.urlsafe_b64encode(salt + digest).decode()


def verify_password(password, encoded):
    raw = base64.urlsafe_b64decode(encoded.encode())
    return hmac.compare_digest(password_hash(password, raw[:16]), encoded)


def session_secret():
    if not IDENTITY_SECRET_PATH.exists():
        IDENTITY_SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        IDENTITY_SECRET_PATH.write_bytes(secrets.token_bytes(32))
    return IDENTITY_SECRET_PATH.read_bytes()


def init_db(path=None, seed_users=True, seed_demo=True):
    with connect(path) as db:
        database = Path(path or DB_PATH).resolve()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','analyst','approver')),
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK(id = 1), categories TEXT NOT NULL,
                monthly_limit REAL NOT NULL, daily_limit REAL NOT NULL, gross_categories TEXT NOT NULL DEFAULT '["Avaria"]',
                discount_rule TEXT NOT NULL, approval_roles TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS occurrences (
                id INTEGER PRIMARY KEY AUTOINCREMENT, product TEXT NOT NULL,
                quantity REAL NOT NULL CHECK(quantity > 0), unit_value REAL NOT NULL CHECK(unit_value >= 0),
                occurrence_date TEXT NOT NULL, sector TEXT NOT NULL, aisle TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL, initial_classification TEXT NOT NULL,
                signaled INTEGER NOT NULL DEFAULT 0, supplier TEXT NOT NULL DEFAULT '',
                product_code TEXT NOT NULL DEFAULT '', occurred_time TEXT NOT NULL DEFAULT '00:00',
                notes TEXT NOT NULL DEFAULT '', record_status TEXT NOT NULL DEFAULT 'active',
                cancelled_by INTEGER REFERENCES users(id), cancelled_at TEXT, cancellation_reason TEXT NOT NULL DEFAULT '',
                responsible TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, occurrence_id INTEGER REFERENCES occurrences(id),
                quantity REAL NOT NULL CHECK(quantity > 0), classification TEXT NOT NULL,
                decision TEXT NOT NULL DEFAULT 'pending' CHECK(decision IN ('pending','include','exclude')),
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','under_review','approved')),
                recovery_value REAL NOT NULL DEFAULT 0 CHECK(recovery_value >= 0),
                recovery_confirmed INTEGER NOT NULL DEFAULT 0,
                collected_value REAL NOT NULL DEFAULT 0 CHECK(collected_value >= 0),
                collected_confirmed INTEGER NOT NULL DEFAULT 0,
                rationale TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '',
                approved_by INTEGER REFERENCES users(id), approved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, occurrence_id INTEGER REFERENCES occurrences(id),
                allocation_id INTEGER REFERENCES allocations(id), user_id INTEGER REFERENCES users(id),
                action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sector_targets (
                sector TEXT NOT NULL, period TEXT NOT NULL,
                monthly_limit REAL NOT NULL CHECK(monthly_limit >= 0),
                daily_limit REAL NOT NULL CHECK(daily_limit >= 0),
                updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(sector,period)
            );
            CREATE TABLE IF NOT EXISTS products (
                code TEXT PRIMARY KEY, description TEXT NOT NULL,
                unit_value REAL NOT NULL DEFAULT 0 CHECK(unit_value >= 0), active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, occurrence_id INTEGER NOT NULL REFERENCES occurrences(id),
                filename TEXT NOT NULL, content_type TEXT NOT NULL, data BLOB NOT NULL,
                user_id INTEGER REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_occ_date ON occurrences(occurrence_date);
            CREATE INDEX IF NOT EXISTS idx_occ_sector ON occurrences(sector);
            CREATE INDEX IF NOT EXISTS idx_alloc_status ON allocations(status, decision);
        """)
        allocation_columns = {row["name"] for row in db.execute("PRAGMA table_info(allocations)")}
        if "collected_value" not in allocation_columns:
            db.execute("ALTER TABLE allocations ADD COLUMN collected_value REAL NOT NULL DEFAULT 0 CHECK(collected_value >= 0)")
        if "collected_confirmed" not in allocation_columns:
            db.execute("ALTER TABLE allocations ADD COLUMN collected_confirmed INTEGER NOT NULL DEFAULT 0")
        occurrence_columns = {row["name"] for row in db.execute("PRAGMA table_info(occurrences)")}
        occurrence_additions = {
            "product_code": "TEXT NOT NULL DEFAULT ''", "occurred_time": "TEXT NOT NULL DEFAULT '00:00'",
            "notes": "TEXT NOT NULL DEFAULT ''", "record_status": "TEXT NOT NULL DEFAULT 'active'",
            "cancelled_by": "INTEGER REFERENCES users(id)", "cancelled_at": "TEXT",
            "cancellation_reason": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in occurrence_additions.items():
            if column not in occurrence_columns:
                db.execute(f"ALTER TABLE occurrences ADD COLUMN {column} {definition}")
        settings_columns = {row["name"] for row in db.execute("PRAGMA table_info(settings)")}
        if "gross_categories" not in settings_columns:
            db.execute("ALTER TABLE settings ADD COLUMN gross_categories TEXT NOT NULL DEFAULT '[\"Avaria\"]'")
        audit_occurrence = next(row for row in db.execute("PRAGMA table_info(audit_log)") if row["name"] == "occurrence_id")
        if audit_occurrence["notnull"]:
            db.executescript("""
                CREATE TABLE audit_log_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, occurrence_id INTEGER REFERENCES occurrences(id),
                    allocation_id INTEGER REFERENCES allocations(id), user_id INTEGER REFERENCES users(id),
                    action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO audit_log_new(id,occurrence_id,allocation_id,user_id,action,details,created_at)
                    SELECT id,occurrence_id,allocation_id,user_id,action,details,created_at FROM audit_log;
                DROP TABLE audit_log;
                ALTER TABLE audit_log_new RENAME TO audit_log;
            """)
        if seed_users and db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            db.executemany("INSERT INTO users(username,display_name,password_hash,role) VALUES(?,?,?,?)", [
                ("admin", "Administrador", password_hash("admin123"), "admin"),
                ("analista", "Analista de avarias", password_hash("analista123"), "analyst"),
                ("aprovador", "Aprovador", password_hash("aprovador123"), "approver"),
            ])
        initial_limit = 25000 if seed_demo else 0
        initial_daily = 1500 if seed_demo else 0
        db.execute("""INSERT OR IGNORE INTO settings
            (id,categories,monthly_limit,daily_limit,discount_rule,approval_roles)
            VALUES(1,?,?,?,?,?)""", (
                json.dumps(["Avaria", "Reembalagem", "Fornecedor"], ensure_ascii=False),
                initial_limit, initial_daily,
                "Defina aqui a regra interna de desconto; nenhuma categoria gera desconto automaticamente.",
                json.dumps(["admin", "approver"]),
            ))
        if seed_demo and db.execute("SELECT COUNT(*) FROM occurrences").fetchone()[0] == 0:
            samples = [
                ("Arroz Tipo 1 5 kg", 3, 28.9, date.today().isoformat(), "Mercearia", "A-12", "Embalagem rasgada", "Avaria", 1, "", "Marina Costa", "Reembalagem", "pending", 0),
                ("Detergente neutro 500 ml", 8, 2.45, date.today().isoformat(), "Limpeza", "B-04", "Caixa amassada", "Avaria", 0, "Higienix", "Paulo Lima", "Fornecedor", "under_review", 0),
                ("Biscoito integral 200 g", 5, 6.2, date.today().isoformat(), "Mercearia", "A-07", "Lacre violado", "Avaria", 1, "", "Marina Costa", "Avaria", "approved", 1),
            ]
            for row in samples:
                product, qty, value, day, sector, aisle, reason, initial, signaled, supplier, responsible, category, status, confirmed = row
                cur = db.execute("""INSERT INTO occurrences(product,quantity,unit_value,occurrence_date,sector,aisle,reason,
                    initial_classification,signaled,supplier,responsible) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (product, qty, value, day, sector, aisle, reason, initial, signaled, supplier, responsible))
                decision = "exclude" if category == "Fornecedor" else ("include" if status == "approved" else "pending")
                db.execute("""INSERT INTO allocations(occurrence_id,quantity,classification,decision,status,recovery_value,
                    recovery_confirmed,rationale,evidence,approved_by,approved_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (cur.lastrowid, qty, category, decision, status, 0, confirmed,
                     "Decisão inicial registrada para demonstração." if status == "approved" else "", "", 1 if confirmed else None,
                     time.strftime("%Y-%m-%d %H:%M:%S") if confirmed else None))
    INITIALIZED_TENANT_DBS.add(database)


def ensure_operational_database(corporation_id):
    database = corporation_database(corporation_id).resolve()
    if database in INITIALIZED_TENANT_DBS: return database
    with TENANT_DB_LOCK:
        if database not in INITIALIZED_TENANT_DBS:
            init_db(database, seed_users=False, seed_demo=False)
    return database


def sign(payload):
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(session_secret(), body.encode(), hashlib.sha256).hexdigest()
    return body + "." + signature


def unsign(token):
    try:
        body, signature = token.rsplit(".", 1)
        expected = hmac.new(session_secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return data if data.get("exp", 0) > time.time() else None
    except (ValueError, json.JSONDecodeError, TypeError):
        return None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, value, status=200, headers=None):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(body)

    def body_json(self):
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 8_000_000)
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            raise ValueError("Corpo JSON inválido")

    def current_user(self):
        ACTIVE_DATABASE.set(None)
        ACTIVE_USER.set(None)
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(COOKIE_NAME)
        if not morsel: return None
        with identity_connect() as db:
            session = db.execute("""SELECT id,user_id,corporation_id FROM sessions
                WHERE token_hash=? AND revoked_at IS NULL AND expires_at>CURRENT_TIMESTAMP""", (hash_token(morsel.value),)).fetchone()
            if not session: return None
            user = member_context(db, session["user_id"], session["corporation_id"])
        if user:
            user["session_id"] = session["id"]
            ACTIVE_USER.set(user)
            return user
        return None

    def public_json(self, value, status=200):
        return self.send_json(value, status)

    def issue_session_response(self, user_id, corporation_id, status=200):
        with identity_connect() as db:
            user = db.execute("SELECT id,username,email,display_name FROM identity_users WHERE id=? AND active=1", (user_id,)).fetchone()
            context = member_context(db, user_id, corporation_id)
            if not user or not context: raise ValueError("Vínculo corporativo inativo")
            token = issue_identity_session(db, user_id, corporation_id)
            identity_audit(db, corporation_id, user_id, "Login", {"corporation": context["corporation_name"]})
        response_user = {key: value for key,value in context.items() if key != "session_id"}
        response_user["landing_page"] = next((role["landing_page"] for role in context["roles"] if role.get("landing_page")), "dashboard")
        return self.send_json({"user": response_user}, status, {"Set-Cookie": cookie_header(token)})

    def login(self, data):
        address = self.client_address[0]
        now = time.time()
        with LOGIN_LOCK:
            attempts = [stamp for stamp in LOGIN_LIMITS.get(address, []) if now - stamp < 15 * 60]
            if len(attempts) >= 8:
                LOGIN_LIMITS[address] = attempts
                return self.send_json({"error": "Muitas tentativas. Tente novamente em 15 minutos."}, 429)
        account = str(data.get("username") or data.get("email") or "").strip().casefold()
        password = str(data.get("password", ""))
        with identity_connect() as db:
            user = db.execute("SELECT * FROM identity_users WHERE (username=? OR email=?) AND active=1", (account, account)).fetchone()
            valid = bool(user and verify_password(password, user["password_hash"]))
            if not valid:
                with LOGIN_LOCK: LOGIN_LIMITS[address] = attempts + [now]
                return self.send_json({"error": "E-mail/usuário ou senha inválidos"}, 401)
            with LOGIN_LOCK: LOGIN_LIMITS.pop(address, None)
            if not user["email_verified_at"]:
                return self.send_json({"error": "Confirme seu e-mail antes de entrar"}, 403)
            if user["mfa_enabled"] and not verify_totp(user["mfa_secret"], data.get("mfa_code")):
                return self.send_json({"mfa_required": True, "error": "Informe o código do autenticador"}, 200)
            corporations = memberships_for_user(db, user["id"])
            if not corporations: return self.send_json({"error": "Esta conta não possui uma corporação ativa"}, 403)
            selected = str(data.get("corporation_id", ""))
            if selected:
                if selected not in {item["id"] for item in corporations}:
                    return self.send_json({"error": "Sem vínculo com a corporação selecionada"}, 403)
            elif len(corporations) > 1:
                return self.send_json({"choose_corporation": True, "corporations": corporations})
            else:
                selected = corporations[0]["id"]
            policy = db.execute("SELECT require_admin_mfa FROM corporation_settings WHERE corporation_id=?", (selected,)).fetchone()
            target_member = next(item for item in corporations if item["id"] == selected)
            has_admin_role = any(role["code"] in ("owner", "admin", "legacy_admin") for role in target_member["roles"])
            if policy and policy["require_admin_mfa"] and has_admin_role and not user["mfa_enabled"]:
                return self.send_json({"error": "Esta corporação exige MFA para contas administrativas"}, 403)
            db.execute("UPDATE identity_users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?", (user["id"],))
        return self.issue_session_response(user["id"], selected)

    def public_link(self, path, token):
        scheme = "https" if os.environ.get("APP_ENV") == "production" else "http"
        host = self.headers.get("Host", "127.0.0.1:8000")
        return f"{scheme}://{host}{path}?token={quote(token)}"

    def register_corporation(self, data):
        if os.environ.get("APP_ENV") == "production" and not os.environ.get("SMTP_HOST"):
            raise ValueError("Configure SMTP antes de aceitar cadastro público de corporações")
        company = {key: str(data.get(key, "")).strip() for key in ("legal_name", "display_name", "phone", "address", "city", "state")}
        if not company["legal_name"] or not company["display_name"] or not company["address"] or not company["city"] or not company["state"]:
            raise ValueError("Preencha razão social, nome fantasia e endereço completo")
        email = normalize_email(data.get("email")); owner_email = normalize_email(data.get("owner_email"))
        cnpj = re.sub(r"\D", "", str(data.get("cnpj", "")))
        if cnpj and not valid_cnpj(cnpj): raise ValueError("CNPJ inválido")
        password = str(data.get("owner_password", ""))
        owner_name = str(data.get("owner_name", "")).strip()
        if len(password) < 10 or not owner_name: raise ValueError("Informe o nome do administrador e senha com pelo menos 10 caracteres")
        slug = normalize_slug(company["display_name"]); corporation_id = str(uuid.uuid4())
        with identity_connect() as db:
            if cnpj and db.execute("SELECT 1 FROM corporations WHERE cnpj=?", (cnpj,)).fetchone():
                raise ValueError("Já existe uma corporação cadastrada com este CNPJ")
            if db.execute("SELECT 1 FROM identity_users WHERE email=?", (owner_email,)).fetchone():
                raise ValueError("Este e-mail já possui uma conta. Entre e aceite um convite, ou use outro administrador inicial")
            suffix = 2; base_slug = slug
            while db.execute("SELECT 1 FROM corporations WHERE slug=?", (slug,)).fetchone():
                slug = f"{base_slug}-{suffix}"; suffix += 1
            logo_type, logo_data = "", None
            if data.get("logo_data"):
                logo_type = str(data.get("logo_type", ""))
                if logo_type not in ("image/png", "image/jpeg", "image/webp"): raise ValueError("Logo aceita PNG, JPG ou WebP")
                logo_data = base64.b64decode(data["logo_data"], validate=True)
                if len(logo_data) > 2 * 1024 * 1024: raise ValueError("Logo deve ter até 2 MB")
            db.execute("""INSERT INTO corporations(id,slug,legal_name,display_name,cnpj,email,phone,address,city,state,logo_type,logo_data,status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'pending_verification')""",
                (corporation_id, slug, company["legal_name"], company["display_name"], cnpj or None, email,
                 company["phone"], company["address"], company["city"], company["state"], logo_type, logo_data))
            create_corporation_roles(db, corporation_id)
            user_cursor = db.execute("INSERT INTO identity_users(username,email,display_name,password_hash) VALUES(?,?,?,?)",
                (owner_email, owner_email, owner_name, password_hash(password)))
            owner_id = user_cursor.lastrowid; member_id = str(uuid.uuid4())
            db.execute("INSERT INTO corporation_members(id,corporation_id,user_id) VALUES(?,?,?)", (member_id, corporation_id, owner_id))
            db.execute("INSERT INTO member_roles(member_id,role_id) VALUES(?,?)", (member_id, corporate_role(db, corporation_id, "owner")))
            db.execute("INSERT INTO corporation_settings(corporation_id) VALUES(?)", (corporation_id,))
            token = issue_auth_token(db, owner_id, "verify_corporation_email", corporation_id, 24 * 60 * 60)
            identity_audit(db, corporation_id, owner_id, "Corporação cadastrada", {"slug": slug, "status": "pending_verification"})
        verification_url = self.public_link("/api/verify-email", token)
        send_notification(owner_email, "Confirme sua corporação", f"Confirme o e-mail do administrador da corporação: {verification_url}")
        response = {"ok": True, "message": "Cadastro iniciado. Confirme o e-mail para ativar a corporação."}
        if os.environ.get("APP_ENV", "development") != "production": response["verification_url"] = verification_url
        return self.send_json(response, 201)

    def verify_email(self, token):
        if not token: return self.send_json({"error": "Token ausente"}, 400)
        with identity_connect() as db:
            row = db.execute("""SELECT * FROM auth_tokens WHERE token_hash=? AND purpose='verify_corporation_email'
                AND used_at IS NULL AND expires_at>CURRENT_TIMESTAMP""", (hash_token(token),)).fetchone()
            if not row: return self.send_json({"error": "Link inválido ou expirado"}, 400)
            db.execute("UPDATE identity_users SET email_verified_at=CURRENT_TIMESTAMP WHERE id=?", (row["user_id"],))
            db.execute("UPDATE corporations SET status='active',email_verified_at=CURRENT_TIMESTAMP WHERE id=?", (row["corporation_id"],))
            db.execute("UPDATE auth_tokens SET used_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
            identity_audit(db, row["corporation_id"], row["user_id"], "E-mail corporativo confirmado", {})
        return self.send_json({"ok": True, "message": "E-mail confirmado. Entre para acessar o painel."})

    def create_invitation(self, data, actor):
        email = normalize_email(data.get("email")); role_code = str(data.get("role_code", "operator"))
        name = str(data.get("display_name", "")).strip()
        department_ids = list(dict.fromkeys(str(item) for item in data.get("department_ids", [])))
        corp = actor["corporation_id"]
        with identity_connect() as db:
            role_id = corporate_role(db, corp, role_code)
            if role_code == "owner" and actor["role_code"] != "owner":
                return self.send_json({"error": "Somente o proprietário pode conceder a função de proprietário"}, 403)
            role_permissions = {row[0] for row in db.execute("SELECT permission_code FROM role_permissions WHERE role_id=?", (role_id,))}
            actor_permissions = set(actor["permissions"])
            if actor["role_code"] != "owner" and not role_permissions.issubset(actor_permissions):
                return self.send_json({"error": "Você não pode conceder permissões que não possui"}, 403)
            for department_id in department_ids:
                if not db.execute("SELECT 1 FROM departments WHERE id=? AND corporation_id=? AND active=1", (department_id, corp)).fetchone():
                    raise ValueError("Setor inválido para esta corporação")
            user = db.execute("SELECT id FROM identity_users WHERE email=?", (email,)).fetchone()
            if user and db.execute("SELECT 1 FROM corporation_members WHERE user_id=? AND corporation_id=?", (user["id"], corp)).fetchone():
                raise ValueError("Este colaborador já participa da corporação")
            db.execute("UPDATE invitations SET status='replaced' WHERE corporation_id=? AND email=? AND status='pending'", (corp, email))
            token = make_token(); invitation_id = str(uuid.uuid4())
            db.execute("""INSERT INTO invitations(id,token_hash,corporation_id,email,display_name,role_id,department_ids,expires_at,invited_by)
                VALUES(?,?,?,?,?,?,?,?,?)""", (invitation_id, hash_token(token), corp, email, name, role_id,
                json.dumps(department_ids), expiry_timestamp(INVITATION_DAYS * 86400), actor["id"]))
            identity_audit(db, corp, actor["id"], "Convite enviado", {"email": email, "role": role_code})
        link = self.public_link("/", token)
        sent = send_notification(email, "Convite para sua corporação", f"Aceite o convite para entrar na empresa: {link}")
        response = {"ok": True, "message": "Convite enviado por e-mail." if sent else "Convite criado; e-mail não configurado no ambiente local."}
        if not sent: response["invitation_url"] = link
        return self.send_json(response, 201)

    def lookup_invitation(self, token):
        with identity_connect() as db:
            row = db.execute("""SELECT i.email,i.display_name,i.expires_at,r.name AS role_name,c.display_name AS corporation_name
                FROM invitations i JOIN roles r ON r.id=i.role_id JOIN corporations c ON c.id=i.corporation_id
                WHERE i.token_hash=? AND i.status='pending' AND i.expires_at>CURRENT_TIMESTAMP AND c.status='active'""",
                (hash_token(token),)).fetchone()
        if not row: return self.send_json({"error": "Convite inválido, expirado ou revogado"}, 404)
        return self.send_json({"invitation": dict(row), "email_hint": row["email"][:2] + "***" + row["email"][row["email"].find("@"):]})

    def accept_invitation(self, data):
        token = str(data.get("token", "")); password = str(data.get("password", "")); display_name = str(data.get("display_name", "")).strip()
        if len(password) < 10: raise ValueError("Defina uma senha com pelo menos 10 caracteres")
        with identity_connect() as db:
            invite = db.execute("""SELECT * FROM invitations WHERE token_hash=? AND status='pending' AND expires_at>CURRENT_TIMESTAMP""",
                (hash_token(token),)).fetchone()
            if not invite: raise ValueError("Convite inválido ou expirado")
            user = db.execute("SELECT * FROM identity_users WHERE email=?", (invite["email"],)).fetchone()
            if user and user["email_verified_at"] and not verify_password(password, user["password_hash"]):
                raise ValueError("Esta conta já existe; informe a senha atual para vincular a corporação")
            if user:
                user_id = user["id"]
                if not user["email_verified_at"]:
                    if not display_name: raise ValueError("Informe seu nome completo")
                    db.execute("UPDATE identity_users SET password_hash=?,display_name=?,email_verified_at=CURRENT_TIMESTAMP WHERE id=?",
                        (password_hash(password), display_name, user_id))
            else:
                if not display_name: display_name = invite["display_name"] or invite["email"].split("@")[0]
                cursor = db.execute("INSERT INTO identity_users(username,email,display_name,password_hash,email_verified_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP)",
                    (invite["email"], invite["email"], display_name, password_hash(password)))
                user_id = cursor.lastrowid
            member_id = str(uuid.uuid4())
            db.execute("INSERT INTO corporation_members(id,corporation_id,user_id) VALUES(?,?,?)", (member_id, invite["corporation_id"], user_id))
            db.execute("INSERT INTO member_roles(member_id,role_id) VALUES(?,?)", (member_id, invite["role_id"]))
            for department_id in json.loads(invite["department_ids"]):
                db.execute("INSERT OR IGNORE INTO member_departments(member_id,department_id) VALUES(?,?)", (member_id, department_id))
            db.execute("UPDATE invitations SET status='accepted' WHERE id=?", (invite["id"],))
            identity_audit(db, invite["corporation_id"], user_id, "Convite aceito", {"invitation_id": invite["id"]})
        session_response = self.issue_session_response(user_id, invite["corporation_id"])
        return session_response

    def request_password_reset(self, data):
        response = {"ok": True, "message": "Se a conta existir, enviaremos instruções de recuperação."}
        email = str(data.get("email", "")).strip().casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email): return self.send_json(response)
        with identity_connect() as db:
            user = db.execute("SELECT id FROM identity_users WHERE email=? AND active=1", (email,)).fetchone()
            if user:
                token = issue_auth_token(db, user["id"], "password_reset", seconds=3600)
        if user:
            link = self.public_link("/", token)
            send_notification(email, "Recuperação de senha", f"Redefina sua senha neste link (válido por 1 hora): {link}")
            if os.environ.get("APP_ENV", "development") != "production": response["reset_url"] = link
        return self.send_json(response)

    def reset_password(self, data):
        token = str(data.get("token", "")); password = str(data.get("password", ""))
        if len(password) < 10: raise ValueError("Senha deve ter pelo menos 10 caracteres")
        with identity_connect() as db:
            auth = db.execute("""SELECT * FROM auth_tokens WHERE token_hash=? AND purpose='password_reset'
                AND used_at IS NULL AND expires_at>CURRENT_TIMESTAMP""", (hash_token(token),)).fetchone()
            if not auth: raise ValueError("Link inválido, expirado ou já utilizado")
            db.execute("UPDATE identity_users SET password_hash=? WHERE id=?", (password_hash(password), auth["user_id"]))
            db.execute("UPDATE auth_tokens SET used_at=CURRENT_TIMESTAMP WHERE id=?", (auth["id"],))
            db.execute("UPDATE sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND revoked_at IS NULL", (auth["user_id"],))
            identity_audit(db, auth["corporation_id"], auth["user_id"], "Senha redefinida", {})
        return self.send_json({"ok": True, "message": "Senha redefinida. Entre novamente."})

    def start_mfa_enrollment(self, user):
        if not self.require_permission(user, "manage_security"): return
        secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        with identity_connect() as db:
            db.execute("UPDATE identity_users SET mfa_secret=?,mfa_enabled=0 WHERE id=?", (secret, user["id"]))
            identity_audit(db, user["corporation_id"], user["id"], "MFA preparada", {})
        account = quote(user["email"] or user["username"])
        issuer = quote(user["corporation_name"])
        return self.send_json({"secret": secret, "otpauth_uri": f"otpauth://totp/{issuer}:{account}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"})

    def confirm_mfa(self, data, user):
        if not self.require_permission(user, "manage_security"): return
        with identity_connect() as db:
            row = db.execute("SELECT mfa_secret FROM identity_users WHERE id=?", (user["id"],)).fetchone()
            if not row["mfa_secret"] or not verify_totp(row["mfa_secret"], data.get("code")):
                raise ValueError("Código MFA inválido")
            db.execute("UPDATE identity_users SET mfa_enabled=1 WHERE id=?", (user["id"],))
            identity_audit(db, user["corporation_id"], user["id"], "MFA ativada", {})
        return self.send_json({"ok": True})

    def get_corporation(self, user):
        with identity_connect() as db:
            corp = db.execute("""SELECT id,slug,legal_name,display_name,cnpj,email,phone,address,city,state,logo_type,status
                FROM corporations WHERE id=?""", (user["corporation_id"],)).fetchone()
            summary = db.execute("""SELECT (SELECT COUNT(*) FROM corporation_members WHERE corporation_id=? AND status='active') AS members,
                (SELECT COUNT(*) FROM invitations WHERE corporation_id=? AND status='pending' AND expires_at>CURRENT_TIMESTAMP) AS invites,
                (SELECT COUNT(*) FROM departments WHERE corporation_id=? AND active=1) AS departments,
                (SELECT COUNT(*) FROM roles WHERE corporation_id=?) AS roles""",
                (user["corporation_id"],) * 4).fetchone()
        return self.send_json({"corporation": dict(corp), "summary": dict(summary)})

    def update_corporation(self, data, user):
        allowed = {"legal_name", "display_name", "phone", "address", "city", "state"}
        updates = {key: str(data[key]).strip() for key in allowed if key in data}
        if not updates: raise ValueError("Nenhuma alteração enviada")
        if any(not value for key,value in updates.items() if key in ("legal_name", "display_name", "address", "city", "state")):
            raise ValueError("Dados obrigatórios da corporação não podem ficar vazios")
        with identity_connect() as db:
            row = db.execute("SELECT * FROM corporations WHERE id=?", (user["corporation_id"],)).fetchone()
            db.execute("UPDATE corporations SET " + ",".join(f"{key}=?" for key in updates) + " WHERE id=?",
                (*updates.values(), user["corporation_id"]))
            identity_audit(db, user["corporation_id"], user["id"], "Dados da corporação atualizados",
                {key: {"before": row[key], "after": value} for key,value in updates.items()})
        return self.get_corporation(user)

    def get_departments(self, user):
        with identity_connect() as db:
            departments = [dict(row) for row in db.execute("SELECT id,name,active FROM departments WHERE corporation_id=? ORDER BY name", (user["corporation_id"],))]
        return self.send_json({"departments": departments})

    def create_department(self, data, user):
        name = str(data.get("name", "")).strip()
        if not name or len(name) > 100: raise ValueError("Informe um nome de setor válido")
        department_id = str(uuid.uuid4())
        with identity_connect() as db:
            db.execute("INSERT INTO departments(id,corporation_id,name) VALUES(?,?,?)", (department_id, user["corporation_id"], name))
            identity_audit(db, user["corporation_id"], user["id"], "Setor criado", {"name": name})
        return self.send_json({"id": department_id}, 201)

    def update_department(self, department_id, data, user):
        with identity_connect() as db:
            row = db.execute("SELECT * FROM departments WHERE id=? AND corporation_id=?", (department_id, user["corporation_id"])).fetchone()
            if not row: return self.send_json({"error": "Setor não encontrado"}, 404)
            name = str(data.get("name", row["name"])).strip(); active = int(bool(data.get("active", row["active"])))
            if not name: raise ValueError("Nome do setor não pode ficar vazio")
            db.execute("UPDATE departments SET name=?,active=? WHERE id=?", (name, active, department_id))
            identity_audit(db, user["corporation_id"], user["id"], "Setor atualizado", {"before": row["name"], "after": name, "active": active})
        return self.send_json({"ok": True})

    def get_roles(self, user):
        with identity_connect() as db:
            roles = []
            for row in db.execute("SELECT id,code,name,protected,landing_page FROM roles WHERE corporation_id=? ORDER BY protected DESC,name", (user["corporation_id"],)):
                role = dict(row); role["permissions"] = [item[0] for item in db.execute("SELECT permission_code FROM role_permissions WHERE role_id=? ORDER BY permission_code", (role["id"],))]
                roles.append(role)
            permissions = [dict(row) for row in db.execute("SELECT code,description FROM permissions ORDER BY code")]
        return self.send_json({"roles": roles, "permissions": permissions})

    def create_role(self, data, user):
        name = str(data.get("name", "")).strip(); code = re.sub(r"[^a-z0-9_]+", "_", name.casefold()).strip("_")
        permissions = list(dict.fromkeys(str(item) for item in data.get("permissions", [])))
        if not name or not code: raise ValueError("Informe um nome de cargo")
        if any(permission not in user["permissions"] for permission in permissions):
            raise ValueError("Não é possível conceder permissões que você não possui")
        role_id = str(uuid.uuid4())
        with identity_connect() as db:
            db.execute("INSERT INTO roles(id,corporation_id,code,name,protected,landing_page) VALUES(?,?,?,?,0,?)",
                (role_id, user["corporation_id"], code, name, str(data.get("landing_page", "dashboard"))))
            db.executemany("INSERT INTO role_permissions(role_id,permission_code) VALUES(?,?)", [(role_id, p) for p in permissions])
            identity_audit(db, user["corporation_id"], user["id"], "Cargo criado", {"name": name, "permissions": permissions})
        return self.send_json({"id": role_id}, 201)

    def update_corporation_role(self, role_id, data, user):
        with identity_connect() as db:
            role = db.execute("SELECT * FROM roles WHERE id=? AND corporation_id=?", (role_id, user["corporation_id"])).fetchone()
            if not role: return self.send_json({"error": "Cargo não encontrado"}, 404)
            if role["code"] == "owner" and user["role_code"] != "owner":
                return self.send_json({"error": "Somente o proprietário pode alterar o cargo proprietário"}, 403)
            if "name" in data:
                name = str(data["name"]).strip()
                if not name: raise ValueError("Nome do cargo não pode ficar vazio")
                db.execute("UPDATE roles SET name=? WHERE id=?", (name, role_id))
            if "permissions" in data:
                permissions = list(dict.fromkeys(str(item) for item in data["permissions"]))
                if any(db.execute("SELECT 1 FROM permissions WHERE code=?", (code,)).fetchone() is None for code in permissions):
                    raise ValueError("Permissão desconhecida")
                if user["role_code"] != "owner" and any(code not in user["permissions"] for code in permissions):
                    raise ValueError("Não é possível conceder permissões que você não possui")
                db.execute("DELETE FROM role_permissions WHERE role_id=?", (role_id,))
                db.executemany("INSERT INTO role_permissions(role_id,permission_code) VALUES(?,?)", [(role_id, code) for code in permissions])
            identity_audit(db, user["corporation_id"], user["id"], "Cargo atualizado", {"role_id": role_id})
        return self.send_json({"ok": True})

    def get_members(self, user):
        with identity_connect() as db:
            members = [dict(row) for row in db.execute("""SELECT m.id,m.status,m.joined_at,u.id AS user_id,u.display_name,u.email,u.last_login_at,
                r.id AS role_id,r.name AS role_name,r.code AS role_code FROM corporation_members m
                JOIN identity_users u ON u.id=m.user_id LEFT JOIN member_roles mr ON mr.member_id=m.id
                LEFT JOIN roles r ON r.id=mr.role_id WHERE m.corporation_id=? ORDER BY u.display_name""", (user["corporation_id"],))]
            for member in members:
                member["departments"] = [row[0] for row in db.execute("SELECT d.name FROM member_departments md JOIN departments d ON d.id=md.department_id WHERE md.member_id=? ORDER BY d.name", (member["id"],))]
        return self.send_json({"users": members})

    def get_invitations(self, user):
        with identity_connect() as db:
            invitations = [dict(row) for row in db.execute("""SELECT i.id,i.email,i.display_name,r.name AS role_name,i.status,i.expires_at,u.display_name AS invited_by
                FROM invitations i JOIN roles r ON r.id=i.role_id JOIN identity_users u ON u.id=i.invited_by
                WHERE i.corporation_id=? AND i.status='pending' ORDER BY i.created_at DESC""", (user["corporation_id"],))]
        return self.send_json({"invitations": invitations})

    def get_identity_audit(self, user):
        with identity_connect() as db:
            logs = [dict(row) for row in db.execute("""SELECT l.*,actor.display_name AS actor_name,target.display_name AS target_name
                FROM identity_audit_logs l LEFT JOIN identity_users actor ON actor.id=l.actor_user_id
                LEFT JOIN identity_users target ON target.id=l.target_user_id WHERE l.corporation_id=?
                ORDER BY l.id DESC LIMIT 300""", (user["corporation_id"],))]
        return self.send_json({"entries": logs})

    def update_member(self, member_id, data, actor):
        with identity_connect() as db:
            member = db.execute("""SELECT m.*,u.email,u.display_name FROM corporation_members m JOIN identity_users u ON u.id=m.user_id
                WHERE m.id=? AND m.corporation_id=?""", (member_id, actor["corporation_id"])).fetchone()
            if not member: return self.send_json({"error": "Colaborador não encontrado"}, 404)
            if "role_id" in data:
                role = db.execute("SELECT id,code FROM roles WHERE id=? AND corporation_id=?", (data["role_id"], actor["corporation_id"])).fetchone()
                if not role: raise ValueError("Cargo inválido para esta corporação")
                if role["code"] == "owner" and actor["role_code"] != "owner": return self.send_json({"error": "Somente o proprietário pode conceder o cargo de proprietário"}, 403)
                permissions = {row[0] for row in db.execute("SELECT permission_code FROM role_permissions WHERE role_id=?", (role["id"],))}
                if actor["role_code"] != "owner" and not permissions.issubset(set(actor["permissions"])):
                    return self.send_json({"error": "Não é possível conceder permissões que você não possui"}, 403)
                db.execute("DELETE FROM member_roles WHERE member_id=?", (member_id,))
                db.execute("INSERT INTO member_roles(member_id,role_id) VALUES(?,?)", (member_id, role["id"]))
            if "department_ids" in data:
                department_ids = list(dict.fromkeys(data["department_ids"]))
                for department_id in department_ids:
                    if not db.execute("SELECT 1 FROM departments WHERE id=? AND corporation_id=?", (department_id, actor["corporation_id"])).fetchone():
                        raise ValueError("Setor inválido para esta corporação")
                db.execute("DELETE FROM member_departments WHERE member_id=?", (member_id,))
                db.executemany("INSERT INTO member_departments(member_id,department_id,is_primary) VALUES(?,?,?)",
                    [(member_id, department_id, int(index == 0)) for index,department_id in enumerate(department_ids)])
            if "active" in data:
                status = "active" if data["active"] else "suspended"
                if member["user_id"] == actor["id"] and status != "active": raise ValueError("Não é permitido suspender o próprio acesso")
                db.execute("UPDATE corporation_members SET status=? WHERE id=?", (status, member_id))
                if status != "active": db.execute("UPDATE sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND corporation_id=? AND revoked_at IS NULL", (member["user_id"], actor["corporation_id"]))
            identity_audit(db, actor["corporation_id"], actor["id"], "Colaborador atualizado", {"member_id": member_id, "changes": {key: data[key] for key in data}}, member["user_id"])
        return self.send_json({"ok": True})

    def start_mfa_enrollment(self, user):
        if not self.require_permission(user, "manage_security"): return
        secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        with identity_connect() as db:
            db.execute("UPDATE identity_users SET mfa_secret=?,mfa_enabled=0 WHERE id=?", (secret, user["id"]))
            identity_audit(db, user["corporation_id"], user["id"], "MFA preparada", {})
        account = quote(user["email"] or user["username"]); issuer = quote(user["corporation_name"])
        return self.send_json({"secret": secret, "otpauth_uri": f"otpauth://totp/{issuer}:{account}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"})

    def confirm_mfa(self, data, user):
        if not self.require_permission(user, "manage_security"): return
        with identity_connect() as db:
            row = db.execute("SELECT mfa_secret FROM identity_users WHERE id=?", (user["id"],)).fetchone()
            if not row["mfa_secret"] or not verify_totp(row["mfa_secret"], data.get("code")): raise ValueError("Código MFA inválido")
            db.execute("UPDATE identity_users SET mfa_enabled=1 WHERE id=?", (user["id"],))
            identity_audit(db, user["corporation_id"], user["id"], "MFA ativada", {})
        return self.send_json({"ok": True})

    def require_user(self):
        user = self.current_user()
        if not user:
            self.send_json({"error": "Autenticação necessária"}, 401)
            return None
        return user

    def require_role(self, user, *roles):
        role_permissions = {"admin": {"manage_users", "manage_corporation"}, "analyst": {"analyze_occurrences"}, "approver": {"approve_discount"}}
        if any(user["role"] == role or bool(role_permissions.get(role, set()) & set(user.get("permissions", []))) for role in roles):
            return True
        else:
            self.send_json({"error": "Permissão insuficiente"}, 403)
            return False

    def require_permission(self, user, permission):
        if permission in user.get("permissions", []): return True
        self.send_json({"error": "Você não possui permissão para esta operação"}, 403)
        return False

    def sector_allowed(self, user, sector):
        return "view_all_sectors" in user.get("permissions", []) or sector in {item["name"] for item in user.get("departments", [])}

    def occurrence_allowed(self, user, occurrence):
        return "view_all_sectors" in user.get("permissions", []) or occurrence["sector"] in {item["name"] for item in user.get("departments", [])}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            query = parse_qs(parsed.query)
            if parsed.path == "/api/verify-email": return self.verify_email(query.get("token", [""])[0])
            if parsed.path == "/api/invitations/lookup": return self.lookup_invitation(query.get("token", [""])[0])
            user = self.require_user()
            if not user:
                return
            if parsed.path == "/api/me":
                with identity_connect() as db: corporations = memberships_for_user(db, user["id"])
                user["corporations"] = corporations
                return self.send_json({"user": user})
            if parsed.path == "/api/corporations/current":
                return self.get_corporation(user)
            if parsed.path == "/api/departments":
                return self.get_departments(user)
            if parsed.path == "/api/roles":
                return self.get_roles(user)
            if parsed.path == "/api/invitations":
                if not self.require_permission(user, "manage_users"): return
                return self.get_invitations(user)
            if parsed.path == "/api/identity-audit":
                if not self.require_permission(user, "view_audit"): return
                return self.get_identity_audit(user)
            if parsed.path == "/api/settings":
                with connect() as db:
                    row = db.execute("SELECT * FROM settings WHERE id=1").fetchone()
                return self.send_json({"categories": json.loads(row["categories"]), "monthly_limit": row["monthly_limit"],
                    "daily_limit": row["daily_limit"], "discount_rule": row["discount_rule"],
                    "approval_roles": json.loads(row["approval_roles"]), "gross_categories": json.loads(row["gross_categories"])})
            if parsed.path == "/api/occurrences":
                return self.list_occurrences(parse_qs(parsed.query))
            if parsed.path.startswith("/api/attachments/"):
                attachment_id = int(parsed.path.rsplit("/", 1)[1])
                with connect() as db:
                    attachment = db.execute("""SELECT a.filename,a.content_type,a.data,o.sector,o.responsible
                        FROM attachments a JOIN occurrences o ON o.id=a.occurrence_id WHERE a.id=?""", (attachment_id,)).fetchone()
                if not attachment: return self.send_json({"error": "Anexo não encontrado"}, 404)
                if not self.occurrence_allowed(user, attachment): return self.send_json({"error": "Anexo fora do setor autorizado"}, 403)
                body = attachment["data"]
                self.send_response(200); self.send_header("Content-Type", attachment["content_type"])
                self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{quote(attachment['filename'])}")
                self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
                self.end_headers(); self.wfile.write(body); return
            if parsed.path == "/api/options":
                with connect() as db:
                    result = {field: [row[0] for row in db.execute(f"SELECT DISTINCT {field} FROM occurrences WHERE {field}!='' ORDER BY {field}")]
                        for field in ("sector", "aisle", "reason", "supplier", "responsible")}
                return self.send_json(result)
            if parsed.path == "/api/sectors":
                period = parse_qs(parsed.query).get("period", [date.today().strftime("%Y-%m")])[0]
                with connect() as db:
                    settings = db.execute("SELECT monthly_limit,daily_limit FROM settings WHERE id=1").fetchone()
                    names = {row[0] for row in db.execute("SELECT DISTINCT sector FROM occurrences WHERE record_status!='cancelled'")}
                    names.update(row[0] for row in db.execute("SELECT DISTINCT sector FROM sector_targets"))
                    names.update(("Fracionado", "Caixa", "Organização"))
                    targets = {row["sector"]: dict(row) for row in db.execute("SELECT * FROM sector_targets WHERE period=?", (period,))}
                return self.send_json({"period": period, "sectors": sorted(names, key=lambda value: value.casefold()),
                    "targets": targets, "fallback": {"monthly_limit": settings["monthly_limit"], "daily_limit": settings["daily_limit"]}})
            if parsed.path == "/api/products":
                query = parse_qs(parsed.query).get("search", [""])[0].strip()
                with connect() as db:
                    products = [dict(row) for row in db.execute("""SELECT code,description,unit_value FROM products
                        WHERE active=1 AND (code LIKE ? OR description LIKE ?) ORDER BY description LIMIT 30""", (f"%{query}%", f"%{query}%"))]
                return self.send_json({"products": products})
            if parsed.path == "/api/users":
                if not self.require_permission(user, "manage_users"): return
                return self.get_members(user)
            if parsed.path == "/api/summary":
                return self.summary(parse_qs(parsed.query))
            if parsed.path == "/api/audit":
                with connect() as db:
                    entries = [dict(row) for row in db.execute("""SELECT l.*,u.display_name AS user_name,o.product,
                        o.id AS occurrence_number FROM audit_log l LEFT JOIN users u ON u.id=l.user_id
                        LEFT JOIN occurrences o ON o.id=l.occurrence_id ORDER BY l.id DESC LIMIT 200""")]
                return self.send_json({"entries": entries})
            if parsed.path == "/api/export.csv":
                if not self.require_permission(user, "export_reports"): return
                return self.export_csv(parse_qs(parsed.query))
            self.send_json({"error": "Rota não encontrada"}, 404)
            return
        if parsed.path in ("/", "/index.html", "/app.css", "/app-responsive.css", "/table-performance.css", "/app.js", "/dist/sectors.js", "/dist/sectors.css"):
            return super().do_GET()
        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            data = self.body_json()
            if parsed.path == "/api/corporations": return self.register_corporation(data)
            if parsed.path == "/api/invitations/accept": return self.accept_invitation(data)
            if parsed.path == "/api/password/forgot": return self.request_password_reset(data)
            if parsed.path == "/api/password/reset": return self.reset_password(data)
            if parsed.path == "/api/login":
                return self.login(data)
            user = self.require_user()
            if not user:
                return
            if parsed.path == "/api/logout":
                with identity_connect() as db:
                    db.execute("UPDATE sessions SET revoked_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? AND revoked_at IS NULL", (user["session_id"], user["id"]))
                return self.send_json({"ok": True}, headers={"Set-Cookie": cookie_header("", 0)})
            if parsed.path == "/api/invitations":
                if not self.require_permission(user, "manage_users"): return
                return self.create_invitation(data, user)
            if parsed.path == "/api/departments":
                if not self.require_permission(user, "manage_departments"): return
                return self.create_department(data, user)
            if parsed.path == "/api/roles":
                if not self.require_permission(user, "manage_roles"): return
                return self.create_role(data, user)
            if parsed.path == "/api/mfa/enroll": return self.start_mfa_enrollment(user)
            if parsed.path == "/api/mfa/confirm": return self.confirm_mfa(data, user)
            if parsed.path == "/api/occurrences":
                if not self.require_permission(user, "create_occurrences"): return
                return self.create_occurrence(data, user)
            if parsed.path == "/api/targets":
                if not self.require_permission(user, "manage_targets"): return
                return self.save_targets(data, user)
            if parsed.path == "/api/products":
                if not self.require_permission(user, "manage_products"): return
                return self.save_product(data)
            if parsed.path == "/api/users":
                if not self.require_permission(user, "manage_users"): return
                return self.create_invitation(data, user)
            if parsed.path.startswith("/api/occurrences/") and parsed.path.endswith("/attachments"):
                return self.add_attachment(parsed.path.split("/")[3], data, user)
            if parsed.path.startswith("/api/occurrences/") and parsed.path.endswith("/cancel"):
                return self.cancel_occurrence(parsed.path.split("/")[3], data, user)
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "occurrences"] and parts[3] == "split":
                return self.split_occurrence(parts[2], data, user)
            self.send_json({"error": "Rota não encontrada"}, 404)
        except (ValueError, KeyError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except sqlite3.IntegrityError as exc:
            self.send_json({"error": f"Dados inválidos: {exc}"}, 400)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        user = self.require_user()
        if not user:
            return
        try:
            data = self.body_json()
            if parsed.path == "/api/settings":
                if not self.require_permission(user, "manage_targets"):
                    return
                return self.update_settings(data, user)
            if parsed.path == "/api/corporations/current":
                if not self.require_permission(user, "manage_corporation"): return
                return self.update_corporation(data, user)
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["api", "allocations"]:
                return self.update_allocation(parts[2], data, user)
            if len(parts) == 3 and parts[:2] == ["api", "occurrences"]:
                return self.update_occurrence(parts[2], data, user)
            if len(parts) == 3 and parts[:2] == ["api", "users"]:
                if not self.require_permission(user, "manage_users"): return
                return self.update_member(parts[2], data, user)
            if len(parts) == 3 and parts[:2] == ["api", "departments"]:
                if not self.require_permission(user, "manage_departments"): return
                return self.update_department(parts[2], data, user)
            if len(parts) == 3 and parts[:2] == ["api", "roles"]:
                if not self.require_permission(user, "manage_roles"): return
                return self.update_corporation_role(parts[2], data, user)
            self.send_json({"error": "Rota não encontrada"}, 404)
        except (ValueError, KeyError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except sqlite3.IntegrityError as exc:
            self.send_json({"error": f"Dados inválidos: {exc}"}, 400)

    def filters(self, query, alias="o", joined=False):
        clauses, params = [], []
        user = ACTIVE_USER.get()
        if user and "view_all_sectors" not in user.get("permissions", []):
            sectors = [item["name"] for item in user.get("departments", [])]
            if not sectors:
                clauses.append("1=0")
            else:
                clauses.append(f"{alias}.sector IN ({','.join('?' for _ in sectors)})")
                params.extend(sectors)
        mapping = {"date_from": f"{alias}.occurrence_date >= ?", "date_to": f"{alias}.occurrence_date <= ?",
                   "sector": f"{alias}.sector = ?", "aisle": f"{alias}.aisle = ?", "reason": f"{alias}.reason = ?",
                   "responsible": f"{alias}.responsible = ?", "supplier": f"{alias}.supplier = ?", "id": f"{alias}.id = ?"}
        for key, expr in mapping.items():
            value = query.get(key, [""])[0]
            if value:
                clauses.append(expr); params.append(value)
        for key, expr in (("product", f"{alias}.product LIKE ?"),):
            value = query.get(key, [""])[0]
            if value:
                clauses.append(expr); params.append(f"%{value}%")
        classification = query.get("classification", [""])[0]
        if classification:
            clauses.append("a.classification=?" if joined else "EXISTS (SELECT 1 FROM allocations ax WHERE ax.occurrence_id=o.id AND ax.classification=?)")
            params.append(classification)
        decision = query.get("decision", [""])[0]
        if decision:
            clauses.append("a.decision=?" if joined else "EXISTS (SELECT 1 FROM allocations ax WHERE ax.occurrence_id=o.id AND ax.decision=?)")
            params.append(decision)
        status = query.get("status", [""])[0]
        if status:
            clauses.append("a.status=?" if joined else "EXISTS (SELECT 1 FROM allocations ax WHERE ax.occurrence_id=o.id AND ax.status=?)")
            params.append(status)
        return (" WHERE " + " AND ".join(clauses) if clauses else "", params)

    def list_occurrences(self, query):
        where, params = self.filters(query)
        try:
            limit = min(max(int(query.get("limit", [500])[0]), 1), 1000)
            offset = max(int(query.get("offset", [0])[0]), 0)
        except ValueError:
            raise ValueError("Paginação inválida")
        with connect() as db:
            occurrences = [dict(row) for row in db.execute(f"SELECT o.* FROM occurrences o{where} ORDER BY o.occurrence_date DESC,o.id DESC LIMIT ? OFFSET ?", (*params, limit, offset))]
            for occurrence in occurrences:
                occurrence["signaled"] = bool(occurrence["signaled"])
                allocation_clauses, allocation_params = ["a.occurrence_id=?"], [occurrence["id"]]
                for key in ("classification", "decision", "status"):
                    value = query.get(key, [""])[0]
                    if value:
                        allocation_clauses.append(f"a.{key}=?")
                        allocation_params.append(value)
                occurrence["allocations"] = [dict(row) for row in db.execute(f"""SELECT a.*,u.display_name AS approved_by_name FROM allocations a
                    LEFT JOIN users u ON u.id=a.approved_by WHERE {' AND '.join(allocation_clauses)} ORDER BY a.id""", allocation_params)]
                occurrence["history"] = [dict(row) for row in db.execute("""SELECT l.*,u.display_name AS user_name FROM audit_log l
                    LEFT JOIN users u ON u.id=l.user_id WHERE l.occurrence_id=? ORDER BY l.id DESC""", (occurrence["id"],))]
                occurrence["attachments"] = [dict(row) for row in db.execute("SELECT id,filename,content_type,created_at FROM attachments WHERE occurrence_id=? ORDER BY id", (occurrence["id"],))]
            total = db.execute(f"SELECT COUNT(*) FROM occurrences o{where}", params).fetchone()[0]
        self.send_json({"occurrences": occurrences, "total": total, "limit": limit, "offset": offset, "has_more": offset + len(occurrences) < total})

    def summary(self, query):
        where, params = self.filters(query, joined=True)
        where += " AND o.record_status='active'" if where else " WHERE o.record_status='active'"
        with connect() as db:
            rows = db.execute(f"""SELECT o.*,a.id AS allocation_id,a.quantity AS allocated_quantity,a.classification,
                a.decision,a.status,a.recovery_value,a.recovery_confirmed,a.collected_value,a.collected_confirmed FROM occurrences o
                JOIN allocations a ON a.occurrence_id=o.id{where.replace('o.', 'o.')}""", params).fetchall()
            occurrences = [dict(row) for row in rows]
            settings = db.execute("SELECT monthly_limit,daily_limit,gross_categories FROM settings WHERE id=1").fetchone()
            gross_categories = set(json.loads(settings["gross_categories"]))
            gross = sum(x["allocated_quantity"] * x["unit_value"] for x in occurrences if x["classification"] in gross_categories)
            recorded_total = sum(x["allocated_quantity"] * x["unit_value"] for x in occurrences)
            pending_value = sum(x["allocated_quantity"] * x["unit_value"] for x in occurrences if x["status"] != "approved")
            repack_value = sum(x["allocated_quantity"] * x["unit_value"] for x in occurrences if x["classification"].casefold() == "reembalagem")
            supplier_value = sum(x["allocated_quantity"] * x["unit_value"] for x in occurrences if x["classification"].casefold() == "fornecedor")
            recovered_total = sum(x["recovery_value"] for x in occurrences if x["recovery_confirmed"])
            recovered = sum(x["recovery_value"] for x in occurrences if x["recovery_confirmed"] and x["classification"] in gross_categories)
            collected = sum(x["collected_value"] for x in occurrences if x["collected_confirmed"])
            discount = sum(x["allocated_quantity"] * x["unit_value"] for x in occurrences if x["decision"] == "include" and x["status"] == "approved")
            excluded = sum(x["allocated_quantity"] * x["unit_value"] for x in occurrences if x["decision"] == "exclude" and x["status"] == "approved")
            pending = sum(1 for x in occurrences if x["status"] != "approved")
            daily = {}
            sectors = {}
            reasons = {}
            products = {}
            aisles = {}
            signal = {"Sinalizado": set(), "Não sinalizado": set()}
            signal_values = {"Sinalizado": 0, "Não sinalizado": 0}
            classes = {}
            classes_detail = {}
            recovery_repack = {"quantity": 0, "value": 0}
            for x in occurrences:
                amount = x["allocated_quantity"] * x["unit_value"]
                day = daily.setdefault(x["occurrence_date"], {"gross": 0, "recovered": 0, "discounted": 0, "excluded": 0})
                if x["classification"] in gross_categories:
                    day["gross"] += amount
                    if x["recovery_confirmed"]: day["recovered"] += x["recovery_value"]
                    sectors[x["sector"]] = sectors.get(x["sector"], 0) + amount
                    reasons[x["reason"]] = reasons.get(x["reason"], 0) + amount
                    product = products.setdefault(x["product"], {"quantity": 0, "value": 0})
                    product["quantity"] += x["allocated_quantity"]
                    product["value"] += amount
                    aisles[x["aisle"] or "Sem rua"] = aisles.get(x["aisle"] or "Sem rua", 0) + amount
                if x["decision"] == "include" and x["status"] == "approved": day["discounted"] += amount
                if x["decision"] == "exclude" and x["status"] == "approved": day["excluded"] += amount
                signal_key = "Sinalizado" if x["signaled"] else "Não sinalizado"
                signal[signal_key].add(x["id"])
                signal_values[signal_key] += amount
                classes[x["classification"]] = classes.get(x["classification"], 0) + amount
                class_detail = classes_detail.setdefault(x["classification"], {"recorded": 0, "recovered": 0, "pending": 0, "discounted": 0})
                class_detail["recorded"] += amount
                if x["recovery_confirmed"]: class_detail["recovered"] += x["recovery_value"]
                if x["status"] != "approved": class_detail["pending"] += amount
                if x["decision"] == "include" and x["status"] == "approved": class_detail["discounted"] += amount
                if x["classification"].lower() == "reembalagem" and x["recovery_confirmed"]:
                    recovery_repack["quantity"] += x["allocated_quantity"]
                    recovery_repack["value"] += x["recovery_value"]
            def pareto(values):
                items = sorted(values.items(), key=lambda item: item[1], reverse=True)
                total, running = sum(values.values()), 0
                output = []
                for name, value in items:
                    running += value
                    output.append({"name": name, "value": round(value, 2), "cumulative": round(running / total * 100, 1) if total else 0})
                return output
        month_prefix = query.get("month", [date.today().strftime("%Y-%m")])[0]
        month_gross = sum(x["allocated_quantity"] * x["unit_value"] for x in occurrences if x["occurrence_date"].startswith(month_prefix) and x["decision"] == "include" and x["status"] == "approved")
        today = date.today().isoformat()
        today_gross = sum(x["allocated_quantity"] * x["unit_value"] for x in occurrences if x["occurrence_date"] == today and x["decision"] == "include" and x["status"] == "approved")
        self.send_json({"gross": round(gross, 2), "recorded_total": round(recorded_total, 2), "pending_value": round(pending_value, 2),
            "repack_value": round(repack_value, 2), "supplier_value": round(supplier_value, 2),
            "recovered": round(recovered_total, 2), "recovery_applied": round(recovered, 2), "collected": round(collected, 2), "net": round(gross - recovered, 2),
            "discount_included": round(discount, 2), "discount_excluded": round(excluded, 2), "pending": pending,
            "occurrences_count": len({x["id"] for x in occurrences}),
            "monthly_limit": settings["monthly_limit"], "daily_limit": settings["daily_limit"],
            "month_realized": round(month_gross, 2), "month_percent": round(month_gross / settings["monthly_limit"] * 100, 1) if settings["monthly_limit"] else 0,
            "today_realized": round(today_gross, 2), "today_percent": round(today_gross / settings["daily_limit"] * 100, 1) if settings["daily_limit"] else 0,
            "daily": [{"date": key, **value} for key, value in sorted(daily.items())], "sectors": pareto(sectors),
            "reasons": pareto(reasons), "aisles": pareto(aisles), "signal": {key: len(value) for key,value in signal.items()}, "signal_values": signal_values, "classifications": classes, "classification_details": classes_detail,
            "top_products": [{"name": k, **v} for k, v in sorted(products.items(), key=lambda item: item[1]["value"], reverse=True)[:10]],
            "repack_recovery": recovery_repack})

    def create_occurrence(self, data, user):
        is_draft = data.get("record_status") == "draft"
        required = ("product", "quantity", "unit_value", "occurrence_date", "sector", "reason", "classification", "responsible")
        if not is_draft and any(not str(data.get(key, "")).strip() for key in required):
            raise ValueError("Preencha produto, quantidade, valor, data, setor, motivo, classificação e responsável")
        with connect() as db:
            settings = db.execute("SELECT categories FROM settings WHERE id=1").fetchone()
            categories = json.loads(settings["categories"])
        classification = str(data.get("classification") or categories[0])
        product = str(data.get("product") or ("" if is_draft else "Produto pendente")).strip()
        requested_sector = str(data.get("sector") or "").strip()
        if requested_sector and not self.sector_allowed(user, requested_sector):
            return self.send_json({"error": "Setor não autorizado para este usuário"}, 403)
        quantity, unit_value = float(data.get("quantity") or 1), float(data.get("unit_value") or 0)
        if quantity <= 0 or unit_value < 0: raise ValueError("Quantidade e valor devem ser válidos")
        with connect() as db:
            if classification not in categories: raise ValueError("Classificação não configurada")
            cur = db.execute("""INSERT INTO occurrences(product,quantity,unit_value,occurrence_date,sector,aisle,reason,
                initial_classification,signaled,supplier,responsible,product_code,occurred_time,notes,record_status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (product, quantity, unit_value, data.get("occurrence_date") or date.today().isoformat(), str(data.get("sector") or ("" if is_draft else "A definir")).strip(),
                 str(data.get("aisle", "")).strip(), str(data.get("reason") or "").strip(), classification,
                 int(bool(data.get("signaled"))), str(data.get("supplier", "")).strip(), str(data.get("responsible") or ("" if is_draft else user["display_name"])).strip(),
                 str(data.get("product_code", "")).strip(), str(data.get("occurred_time", "00:00")),
                 str(data.get("notes", "")).strip(), "draft" if is_draft else "active"))
            occurrence_id = cur.lastrowid
            product_code = str(data.get("product_code", "")).strip()
            if product_code and product:
                db.execute("""INSERT INTO products(code,description,unit_value) VALUES(?,?,?)
                    ON CONFLICT(code) DO UPDATE SET description=excluded.description,unit_value=excluded.unit_value,updated_at=CURRENT_TIMESTAMP""",
                    (product_code, product, unit_value))
            alloc = db.execute("INSERT INTO allocations(occurrence_id,quantity,classification) VALUES(?,?,?)",
                (occurrence_id, quantity, classification))
            self.audit(db, occurrence_id, alloc.lastrowid, user["id"], "Rascunho criado" if is_draft else "Registro criado", {"initial_classification": classification, "quantity": quantity})
        self.send_json({"id": occurrence_id}, 201)

    def split_occurrence(self, occurrence_id, data, user):
        if not self.require_permission(user, "analyze_occurrences"): return
        source_id = int(data.get("allocation_id", 0)); split_qty = float(data.get("quantity", 0))
        with connect() as db:
            source = db.execute("SELECT * FROM allocations WHERE id=? AND occurrence_id=?", (source_id, occurrence_id)).fetchone()
            if not source or split_qty <= 0 or split_qty >= source["quantity"]:
                raise ValueError("A quantidade dividida deve ser maior que zero e menor que o saldo do destino")
            settings = db.execute("SELECT approval_roles,categories FROM settings WHERE id=1").fetchone()
            approval_roles = json.loads(settings["approval_roles"])
            if user["role"] not in approval_roles:
                return self.send_json({"error": "Seu perfil não está autorizado a dividir quantidades"}, 403)
            classification = str(data.get("classification", source["classification"]))
            if classification not in json.loads(settings["categories"]): raise ValueError("Classificação não configurada")
            split_recovery = source["recovery_value"] * split_qty / source["quantity"]
            split_collected = source["collected_value"] * split_qty / source["quantity"]
            source_quantity = source["quantity"]
            db.execute("UPDATE allocations SET quantity=quantity-? WHERE id=?", (split_qty, source_id))
            db.execute("UPDATE allocations SET recovery_value=recovery_value-? WHERE id=?", (split_recovery, source_id))
            db.execute("UPDATE allocations SET collected_value=collected_value-? WHERE id=?", (split_collected, source_id))
            cur = db.execute("""INSERT INTO allocations(occurrence_id,quantity,classification,recovery_value,recovery_confirmed,
                collected_value,collected_confirmed) VALUES(?,?,?,?,?,?,?)""", (occurrence_id, split_qty, classification,
                split_recovery, source["recovery_confirmed"], split_collected, source["collected_confirmed"]))
            self.audit(db, int(occurrence_id), cur.lastrowid, user["id"], "Quantidade dividida", {
                "source_allocation": source_id, "source_quantity": {"before": source_quantity, "after": source_quantity - split_qty},
                "new_quantity": split_qty, "classification": classification})
        self.send_json({"ok": True})

    def update_allocation(self, allocation_id, data, user):
        if not ("analyze_occurrences" in user.get("permissions", []) or "approve_discount" in user.get("permissions", [])):
            return self.send_json({"error": "Você não possui permissão para analisar esta ocorrência"}, 403)
        with connect() as db:
            row = db.execute("SELECT * FROM allocations WHERE id=?", (allocation_id,)).fetchone()
            if not row: return self.send_json({"error": "Destino não encontrado"}, 404)
            settings = db.execute("SELECT approval_roles,categories FROM settings WHERE id=1").fetchone()
            approval_roles = json.loads(settings["approval_roles"])
            occurrence_date = db.execute("SELECT occurrence_date FROM occurrences WHERE id=?", (row["occurrence_id"],)).fetchone()[0]
            closed_period = occurrence_date[:7] < date.today().strftime("%Y-%m")
            if (row["status"] == "approved" or closed_period) and user["role"] not in approval_roles:
                return self.send_json({"error": "Somente aprovador pode alterar uma decisão aprovada"}, 403)
            allowed = {"classification", "decision", "status", "recovery_value", "recovery_confirmed", "collected_value", "collected_confirmed", "rationale", "evidence"}
            updates = {key: data[key] for key in allowed if key in data}
            if not updates: raise ValueError("Nenhuma alteração enviada")
            if "classification" in updates and updates["classification"] not in json.loads(settings["categories"]): raise ValueError("Classificação não configurada")
            if updates.get("classification", row["classification"]) != row["classification"]:
                if not str(data.get("rationale", "")).strip() or not str(data.get("evidence", "")).strip():
                    raise ValueError("Informe justificativa e evidência para alterar a classificação")
            if closed_period and not str(data.get("rationale", "")).strip():
                raise ValueError("Período encerrado exige justificativa para qualquer ajuste")
            if "decision" in updates and updates["decision"] not in ("pending", "include", "exclude"): raise ValueError("Decisão inválida")
            if "status" in updates and updates["status"] not in ("pending", "under_review", "approved"): raise ValueError("Situação inválida")
            for value_field, flag_field, label in (("recovery_value", "recovery_confirmed", "Recuperação"), ("collected_value", "collected_confirmed", "Valor recolhido")):
                if value_field in updates:
                    updates[value_field] = float(updates[value_field])
                    if updates[value_field] < 0: raise ValueError(f"{label} não pode ser negativo")
                if flag_field in updates: updates[flag_field] = int(bool(updates[flag_field]))
            if updates.get("status") == "approved":
                if user["role"] not in approval_roles:
                    return self.send_json({"error": "A aprovação da decisão exige perfil aprovador"}, 403)
                if updates.get("decision", row["decision"]) == "pending": raise ValueError("Selecione incluir ou excluir antes de aprovar")
                updates["approved_by"] = user["id"]; updates["approved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            occurrence = db.execute("SELECT unit_value FROM occurrences WHERE id=?", (row["occurrence_id"],)).fetchone()
            max_recovery = row["quantity"] * occurrence["unit_value"]
            if updates.get("recovery_value", row["recovery_value"]) > max_recovery:
                raise ValueError("A recuperação não pode superar o valor bruto deste destino")
            if updates.get("collected_value", row["collected_value"]) > max_recovery:
                raise ValueError("O valor recolhido não pode superar o valor bruto deste destino")
            if "status" in updates and updates["status"] != "approved":
                updates["approved_by"] = None; updates["approved_at"] = None
            set_sql = ",".join(f"{key}=?" for key in updates)
            db.execute(f"UPDATE allocations SET {set_sql} WHERE id=?", (*updates.values(), allocation_id))
            changed = {key: {"before": row[key], "after": updates[key]} for key in updates if key not in ("approved_by", "approved_at")}
            self.audit(db, row["occurrence_id"], int(allocation_id), user["id"], "Destino atualizado", changed)
        self.send_json({"ok": True})

    def update_occurrence(self, occurrence_id, data, user):
        if not ("edit_any_occurrence" in user.get("permissions", []) or "edit_own_occurrences" in user.get("permissions", [])):
            return self.send_json({"error": "Você não possui permissão para editar ocorrências"}, 403)
        allowed = {"product", "product_code", "quantity", "unit_value", "occurrence_date", "occurred_time", "sector", "aisle", "reason", "signaled", "supplier", "responsible", "notes", "record_status", "classification"}
        updates = {key: data[key] for key in allowed if key in data}
        classification = updates.pop("classification", None)
        with connect() as db:
            occurrence = db.execute("SELECT * FROM occurrences WHERE id=?", (occurrence_id,)).fetchone()
            if not occurrence: return self.send_json({"error": "Ocorrência não encontrada"}, 404)
            if not self.occurrence_allowed(user, occurrence): return self.send_json({"error": "Ocorrência fora do setor autorizado"}, 403)
            if "edit_any_occurrence" not in user.get("permissions", []) and occurrence["responsible"] != user["display_name"]:
                return self.send_json({"error": "Você só pode editar seus próprios lançamentos"}, 403)
            if occurrence["record_status"] == "cancelled": return self.send_json({"error": "Lançamento cancelado não pode ser editado"}, 409)
            changed_updates = {}
            for key, value in updates.items():
                if key in ("quantity", "unit_value"):
                    changed = float(value) != float(occurrence[key])
                elif key == "signaled":
                    changed = bool(value) != bool(occurrence[key])
                else:
                    changed = value != occurrence[key]
                if changed: changed_updates[key] = value
            updates = changed_updates
            if not updates and not classification: raise ValueError("Nenhuma alteração enviada")
            approved = db.execute("SELECT COUNT(*) FROM allocations WHERE occurrence_id=? AND status='approved'", (occurrence_id,)).fetchone()[0] > 0
            allocation = None
            classification_changed = False
            if classification:
                settings = db.execute("SELECT categories FROM settings WHERE id=1").fetchone()
                if classification not in json.loads(settings["categories"]): raise ValueError("Classificação não configurada")
                allocation_id = int(data.get("allocation_id") or 0)
                allocation = db.execute("SELECT * FROM allocations WHERE id=? AND occurrence_id=?", (allocation_id, occurrence_id)).fetchone()
                if not allocation: raise ValueError("Selecione um destino válido para reclassificar")
                classification_changed = allocation["classification"] != classification
                if classification_changed and (not str(data.get("evidence", "")).strip() or not str(data.get("change_reason", "")).strip()):
                    raise ValueError("Informe justificativa e evidência para alterar a classificação")
            material_fields = {"quantity", "unit_value", "sector"}
            change_reason = str(data.get("change_reason", "")).strip()
            approval_roles = json.loads(db.execute("SELECT approval_roles FROM settings WHERE id=1").fetchone()[0])
            material_changed = any(key in updates and (float(updates[key]) != float(occurrence[key]) if key in ("quantity", "unit_value") else updates[key] != occurrence[key]) for key in material_fields)
            if not updates and not classification_changed: raise ValueError("Nenhuma alteração enviada")
            closed_period = occurrence["occurrence_date"][:7] < date.today().strftime("%Y-%m")
            if (approved or closed_period) and user["role"] not in approval_roles:
                return self.send_json({"error": "Registro aprovado exige perfil autorizador para edição"}, 403)
            if (approved or closed_period) and (material_changed or classification_changed or closed_period) and not bool(data.get("authorized_adjustment")):
                raise ValueError("Marque ajuste autorizado para modificar registro aprovado ou de período encerrado")
            if material_changed and occurrence["record_status"] != "draft" and not change_reason:
                raise ValueError("Informe a justificativa para alterar quantidade, valor ou setor")
            if classification_changed:
                db.execute("UPDATE allocations SET classification=?,rationale=?,evidence=? WHERE id=?",
                    (classification, change_reason, data["evidence"], allocation["id"]))
                self.audit(db, int(occurrence_id), allocation["id"], user["id"], "Destino reclassificado", {
                    "classification": {"before": allocation["classification"], "after": classification},
                    "justification": change_reason, "evidence": data["evidence"]})
            if "quantity" in updates:
                new_qty = float(updates["quantity"])
                allocated = db.execute("SELECT COALESCE(SUM(quantity),0) FROM allocations WHERE occurrence_id=?", (occurrence_id,)).fetchone()[0]
                if new_qty <= 0 or not math.isclose(new_qty, allocated, rel_tol=0, abs_tol=1e-8): raise ValueError("A quantidade original deve corresponder exatamente à soma dos destinos registrados")
            if "signaled" in updates: updates["signaled"] = int(bool(updates["signaled"]))
            if "unit_value" in updates:
                updates["unit_value"] = float(updates["unit_value"])
                if updates["unit_value"] < 0: raise ValueError("Valor unitário não pode ser negativo")
            resulting_value = float(updates.get("unit_value", occurrence["unit_value"])) * float(updates.get("quantity", occurrence["quantity"]))
            confirmed = db.execute("""SELECT COALESCE(SUM(CASE WHEN recovery_confirmed=1 THEN recovery_value ELSE 0 END),0),
                COALESCE(SUM(CASE WHEN collected_confirmed=1 THEN collected_value ELSE 0 END),0) FROM allocations WHERE occurrence_id=?""", (occurrence_id,)).fetchone()
            if max(confirmed) > resulting_value:
                raise ValueError("O novo valor bruto não pode ficar abaixo de recuperações ou recolhimentos confirmados")
            if "record_status" in updates and updates["record_status"] not in ("active", "draft"):
                raise ValueError("Situação de lançamento inválida")
            if updates.get("record_status") == "active":
                required_fields = {"product": updates.get("product", occurrence["product"]),
                    "sector": updates.get("sector", occurrence["sector"]), "reason": updates.get("reason", occurrence["reason"]),
                    "responsible": updates.get("responsible", occurrence["responsible"])}
                if any(not str(value).strip() for value in required_fields.values()):
                    raise ValueError("Preencha produto, setor, motivo e responsável antes de finalizar o rascunho")
            if "product_code" in updates and updates["product_code"] and updates.get("product", occurrence["product"]):
                db.execute("""INSERT INTO products(code,description,unit_value) VALUES(?,?,?)
                    ON CONFLICT(code) DO UPDATE SET description=excluded.description,unit_value=excluded.unit_value,updated_at=CURRENT_TIMESTAMP""",
                    (updates["product_code"], updates.get("product", occurrence["product"]), updates.get("unit_value", occurrence["unit_value"])))
            if updates:
                db.execute("UPDATE occurrences SET " + ",".join(f"{key}=?" for key in updates) + " WHERE id=?", (*updates.values(), occurrence_id))
            changed = {key: {"before": occurrence[key], "after": updates[key]} for key in updates}
            if classification_changed:
                changed["classification"] = {"before": allocation["classification"], "after": classification}
            if change_reason: changed["justification"] = change_reason
            self.audit(db, int(occurrence_id), None, user["id"], "Lançamento original atualizado", changed)
        self.send_json({"ok": True})

    def cancel_occurrence(self, occurrence_id, data, user):
        if not self.require_permission(user, "cancel_occurrence"): return
        reason = str(data.get("reason", "")).strip()
        if not reason: raise ValueError("Informe o motivo do cancelamento")
        if user["role"] not in ("admin", "approver"): return self.send_json({"error": "Perfil sem autorização para cancelar"}, 403)
        with connect() as db:
            occurrence = db.execute("SELECT * FROM occurrences WHERE id=?", (occurrence_id,)).fetchone()
            if not occurrence: return self.send_json({"error": "Ocorrência não encontrada"}, 404)
            if not self.occurrence_allowed(user, occurrence): return self.send_json({"error": "Ocorrência fora do setor autorizado"}, 403)
            if occurrence["record_status"] == "cancelled": raise ValueError("Lançamento já cancelado")
            approved = db.execute("SELECT COUNT(*) FROM allocations WHERE occurrence_id=? AND status='approved'", (occurrence_id,)).fetchone()[0] > 0
            closed_period = occurrence["occurrence_date"][:7] < date.today().strftime("%Y-%m")
            if (approved or closed_period) and (user["role"] != "admin" or not data.get("authorized_adjustment")):
                return self.send_json({"error": "Cancelamento de registro aprovado ou período encerrado exige administrador e ajuste autorizado"}, 403)
            db.execute("UPDATE occurrences SET record_status='cancelled',cancelled_by=?,cancelled_at=CURRENT_TIMESTAMP,cancellation_reason=? WHERE id=?",
                (user["id"], reason, occurrence_id))
            self.audit(db, int(occurrence_id), None, user["id"], "Lançamento cancelado", {
                "record_status": {"before": occurrence["record_status"], "after": "cancelled"}, "reason": reason,
                "authorized_adjustment": bool(data.get("authorized_adjustment"))})
        self.send_json({"ok": True})

    def add_attachment(self, occurrence_id, data, user):
        if not ("create_occurrences" in user.get("permissions", []) or "edit_own_occurrences" in user.get("permissions", []) or "edit_any_occurrence" in user.get("permissions", [])):
            return self.send_json({"error": "Você não possui permissão para anexar fotos"}, 403)
        filename = Path(str(data.get("filename", "foto"))).name[:180]
        content_type = str(data.get("content_type", ""))
        if content_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            raise ValueError("Formato aceito: JPG, PNG, WebP ou GIF")
        try: content = base64.b64decode(data.get("data", ""), validate=True)
        except ValueError: raise ValueError("Arquivo de imagem inválido")
        if not content or len(content) > 5 * 1024 * 1024: raise ValueError("A foto deve ter até 5 MB")
        with connect() as db:
            occurrence = db.execute("SELECT id,record_status,sector,responsible FROM occurrences WHERE id=?", (occurrence_id,)).fetchone()
            if not occurrence: return self.send_json({"error": "Ocorrência não encontrada"}, 404)
            if not self.occurrence_allowed(user, occurrence): return self.send_json({"error": "Ocorrência fora do setor autorizado"}, 403)
            if "edit_any_occurrence" not in user.get("permissions", []) and occurrence["responsible"] != user["display_name"]:
                return self.send_json({"error": "Você só pode anexar fotos aos seus lançamentos"}, 403)
            cur = db.execute("INSERT INTO attachments(occurrence_id,filename,content_type,data,user_id) VALUES(?,?,?,?,?)",
                (occurrence_id, filename, content_type, content, user["id"]))
            self.audit(db, int(occurrence_id), None, user["id"], "Fotografia anexada", {"attachment_id": cur.lastrowid, "filename": filename})
        self.send_json({"id": cur.lastrowid}, 201)

    def save_targets(self, data, user):
        period = str(data.get("period", ""))
        if len(period) != 7 or period[4] != "-" or not period[:4].isdigit() or not period[5:].isdigit() or not 1 <= int(period[5:]) <= 12:
            raise ValueError("Período deve estar no formato AAAA-MM")
        targets = data.get("targets", [])
        if not isinstance(targets, list) or not targets: raise ValueError("Informe ao menos uma meta setorial")
        with connect() as db:
            changes = []
            for item in targets:
                sector = str(item.get("sector", "")).strip()
                monthly = float(item.get("monthly_limit", -1)); daily = float(item.get("daily_limit", -1))
                if not sector or monthly < 0 or daily < 0: raise ValueError("Setor e metas não podem ficar vazios ou negativos")
                before = db.execute("SELECT monthly_limit,daily_limit FROM sector_targets WHERE sector=? AND period=?", (sector, period)).fetchone()
                db.execute("""INSERT INTO sector_targets(sector,period,monthly_limit,daily_limit,updated_by,updated_at)
                    VALUES(?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(sector,period) DO UPDATE SET
                    monthly_limit=excluded.monthly_limit,daily_limit=excluded.daily_limit,
                    updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
                    (sector, period, monthly, daily, user["id"]))
                changes.append({"sector": sector, "before": dict(before) if before else None,
                    "after": {"monthly_limit": monthly, "daily_limit": daily}})
            db.execute("INSERT INTO audit_log(occurrence_id,allocation_id,user_id,action,details) VALUES(NULL,NULL,?,?,?)",
                (user["id"], "Metas setoriais atualizadas", json.dumps({"period": period, "changes": changes}, ensure_ascii=False)))
        self.send_json({"ok": True})

    def save_product(self, data):
        code = str(data.get("code", "")).strip()[:80]
        description = str(data.get("description", "")).strip()[:180]
        unit_value = float(data.get("unit_value", 0))
        if not code or not description or unit_value < 0: raise ValueError("Código, descrição e valor unitário válido são obrigatórios")
        with connect() as db:
            db.execute("""INSERT INTO products(code,description,unit_value) VALUES(?,?,?)
                ON CONFLICT(code) DO UPDATE SET description=excluded.description,unit_value=excluded.unit_value,active=1,updated_at=CURRENT_TIMESTAMP""",
                (code, description, unit_value))
        self.send_json({"ok": True})

    def create_user(self, data, actor):
        username = str(data.get("username", "")).strip().lower()
        display_name = str(data.get("display_name", "")).strip()
        role = str(data.get("role", "analyst"))
        password = str(data.get("password", ""))
        if not username or not all(char.isalnum() or char in "._-" for char in username):
            raise ValueError("Usuário deve conter letras, números, ponto, hífen ou sublinhado")
        if not display_name or role not in ROLES or len(password) < 8:
            raise ValueError("Informe nome, perfil válido e senha com pelo menos 8 caracteres")
        with connect() as db:
            cursor = db.execute("INSERT INTO users(username,display_name,password_hash,role) VALUES(?,?,?,?)",
                (username, display_name, password_hash(password), role))
            db.execute("INSERT INTO audit_log(occurrence_id,allocation_id,user_id,action,details) VALUES(NULL,NULL,?,?,?)",
                (actor["id"], "Usuário criado", json.dumps({"username": username, "role": role}, ensure_ascii=False)))
        self.send_json({"id": cursor.lastrowid}, 201)

    def update_user(self, user_id, data, actor):
        user_id = int(user_id)
        with connect() as db:
            target = db.execute("SELECT id,username,display_name,role,active FROM users WHERE id=?", (user_id,)).fetchone()
            if not target: return self.send_json({"error": "Usuário não encontrado"}, 404)
            updates = {}
            if "display_name" in data:
                name = str(data["display_name"]).strip()
                if not name: raise ValueError("Nome não pode ficar vazio")
                updates["display_name"] = name
            if "role" in data:
                if data["role"] not in ROLES: raise ValueError("Perfil inválido")
                updates["role"] = data["role"]
            if "active" in data:
                if user_id == actor["id"] and not bool(data["active"]): raise ValueError("Não é permitido desativar a própria conta")
                updates["active"] = int(bool(data["active"]))
            if target["role"] == "admin" and target["active"] and (updates.get("role", "admin") != "admin" or updates.get("active", 1) == 0):
                active_admins = db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
                if active_admins <= 1: raise ValueError("O sistema precisa manter ao menos um administrador ativo")
            if "password" in data:
                if len(str(data["password"])) < 8: raise ValueError("Senha deve ter pelo menos 8 caracteres")
                updates["password_hash"] = password_hash(str(data["password"]))
            if not updates: raise ValueError("Nenhuma alteração enviada")
            db.execute("UPDATE users SET " + ",".join(f"{key}=?" for key in updates) + " WHERE id=?", (*updates.values(), user_id))
            changes = {key: {"before": target[key], "after": updates[key] if key != "password_hash" else "[atualizada]"} for key in updates}
            db.execute("INSERT INTO audit_log(occurrence_id,allocation_id,user_id,action,details) VALUES(NULL,NULL,?,?,?)",
                (actor["id"], "Permissões ou conta atualizadas", json.dumps({"username": target["username"], "changes": changes}, ensure_ascii=False)))
        self.send_json({"ok": True})

    def update_settings(self, data, user):
        with connect() as db:
            current = db.execute("SELECT * FROM settings WHERE id=1").fetchone()
            categories = data.get("categories", json.loads(current["categories"]))
            categories = list(dict.fromkeys(str(item).strip() for item in categories if str(item).strip()))
            if not categories: raise ValueError("Informe ao menos uma classificação")
            gross_categories = data.get("gross_categories", json.loads(current["gross_categories"]))
            gross_categories = list(dict.fromkeys(item for item in gross_categories if item in categories))
            if not gross_categories: raise ValueError("Selecione ao menos uma classificação que compõe a avaria bruta")
            monthly = float(data.get("monthly_limit", current["monthly_limit"]))
            daily = float(data.get("daily_limit", current["daily_limit"]))
            if monthly < 0 or daily < 0: raise ValueError("Limites não podem ser negativos")
            rule = str(data.get("discount_rule", current["discount_rule"]))[:2000]
            roles = data.get("approval_roles", json.loads(current["approval_roles"]))
            roles = list(dict.fromkeys(["admin", *(role for role in roles if role in ROLES)]))
            before = {"categories": json.loads(current["categories"]), "monthly_limit": current["monthly_limit"],
                "daily_limit": current["daily_limit"], "discount_rule": current["discount_rule"],
                "approval_roles": json.loads(current["approval_roles"]), "gross_categories": json.loads(current["gross_categories"])}
            after = {"categories": categories, "monthly_limit": monthly, "daily_limit": daily,
                "discount_rule": rule, "approval_roles": roles, "gross_categories": gross_categories}
            db.execute("UPDATE settings SET categories=?,monthly_limit=?,daily_limit=?,discount_rule=?,approval_roles=?,gross_categories=? WHERE id=1",
                (json.dumps(categories, ensure_ascii=False), monthly, daily, rule, json.dumps(roles), json.dumps(gross_categories, ensure_ascii=False)))
            db.execute("INSERT INTO audit_log(occurrence_id,allocation_id,user_id,action,details) VALUES(NULL,NULL,?,?,?)",
                (user["id"], "Parâmetros administrativos atualizados", json.dumps({key: {"before": before[key], "after": after[key]} for key in before}, ensure_ascii=False)))
        self.send_json({"ok": True})

    def audit(self, db, occurrence_id, allocation_id, user_id, action, details):
        if occurrence_id:
            db.execute("INSERT INTO audit_log(occurrence_id,allocation_id,user_id,action,details) VALUES(?,?,?,?,?)",
                (occurrence_id, allocation_id, user_id, action, json.dumps(details, ensure_ascii=False, default=str)))

    def export_csv(self, query):
        where, params = self.filters(query, joined=True)
        where += " AND o.record_status='active'" if where else " WHERE o.record_status='active'"
        with connect() as db:
            rows = db.execute(f"""SELECT o.id,o.product,o.occurrence_date,o.sector,o.aisle,o.reason,o.initial_classification,
                o.signaled,o.responsible,o.supplier,o.unit_value,a.quantity,a.classification,a.decision,a.status,
                a.recovery_value,a.recovery_confirmed,a.collected_value,a.collected_confirmed,a.rationale,a.evidence FROM occurrences o JOIN allocations a
                ON a.occurrence_id=o.id{where} ORDER BY o.occurrence_date DESC,o.id DESC""", params).fetchall()
        output = io.StringIO(); writer = csv.writer(output)
        writer.writerow(["ID", "Produto", "Data", "Setor", "Rua", "Motivo", "Classificação inicial", "Sinalizado", "Responsável", "Fornecedor", "Quantidade", "Valor unitário", "Classificação atual", "Decisão", "Situação", "Recuperação confirmada (R$)", "Valor recolhido confirmado (R$)", "Justificativa", "Evidência"])
        for row in rows:
            writer.writerow([row["id"], row["product"], row["occurrence_date"], row["sector"], row["aisle"], row["reason"], row["initial_classification"], "Sim" if row["signaled"] else "Não", row["responsible"], row["supplier"], row["quantity"], row["unit_value"], row["classification"], row["decision"], row["status"], row["recovery_value"] if row["recovery_confirmed"] else 0, row["collected_value"] if row["collected_confirmed"] else 0, row["rationale"], row["evidence"]])
        body = "\ufeff" + output.getvalue()
        encoded = body.encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="relatorio-avarias.csv"')
        self.send_header("Content-Length", str(len(encoded))); self.end_headers(); self.wfile.write(encoded)


if __name__ == "__main__":
    init_db()
    init_identity_db()
    migrate_legacy_identity()
    server = ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "8000"))), Handler)
    print(f"Gestão de Avarias disponível em http://{server.server_address[0]}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
