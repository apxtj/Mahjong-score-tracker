from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse
from uuid import uuid4
from fido2.server import Fido2Server,AuthenticationResponse
from fido2.webauthn import PublicKeyCredentialRequestOptions, AttestedCredentialData, CollectedClientData, AuthenticatorData, AuthenticatorAssertionResponse, AuthenticationResponse as AuthenticationResponseWA
from fido2.utils import websafe_encode, websafe_decode
from models import db, User, Credential, Player
import os
import traceback

# Configure logger with debug mode
debug_mode = os.environ.get("FLASK_DEBUG", "False") == "True"
log_level = logging.DEBUG if debug_mode else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)

from fido2.webauthn import PublicKeyCredentialRpEntity, PublicKeyCredentialDescriptor
import json

bp = Blueprint("auth", __name__)


def _public_registration_enabled():
    return str(os.environ.get("ALLOW_PUBLIC_REGISTRATION", "False")).strip().lower() in {
        "1", "true", "yes", "on"
    }


def _get_default_admin_username():
    return os.environ.get("ADMIN_USERNAME", "admin")


def _can_register_username(username):
    if _public_registration_enabled():
        return True

    admin_username = _get_default_admin_username()
    if not username or username.lower() != admin_username.lower():
        return False

    user = User.query.filter_by(username=admin_username).first()
    return user is None or not user.credentials


def ensure_default_admin_user():
    db.create_all()
    admin_username = _get_default_admin_username()
    existing = User.query.filter_by(username=admin_username).first()
    if existing:
        return existing

    admin_user = User(username=admin_username)
    db.session.add(admin_user)
    db.session.commit()
    logger.info("Created default admin user: %s", admin_username)
    return admin_user


def _resolve_origin():
    return os.environ.get("ORIGIN") or os.environ.get("APP_URL") or "http://localhost:5000"


def _resolve_rp_id():
    rp_id = os.environ.get("RP_ID")
    if rp_id:
        return rp_id
    origin = _resolve_origin()
    parsed = urlparse(origin)
    return parsed.hostname or "localhost"


def _resolve_request_rp_id(request_obj=None):
    rp_id = os.environ.get("RP_ID")
    if rp_id:
        return rp_id

    if request_obj is not None:
        host = request_obj.host or "localhost"
        return host.split(":", 1)[0] or "localhost"

    return _resolve_rp_id()


def _make_webauthn_server(request_obj=None, rp_id=None):
    effective_rp_id = rp_id or _resolve_request_rp_id(request_obj)
    rp = PublicKeyCredentialRpEntity(
        id=effective_rp_id,
        name=os.environ.get("RP_NAME", "Mahjong App"),
    )
    return Fido2Server(rp), effective_rp_id


# RP (Relying Party) and origin should be configurable for production.
# Set ORIGIN / APP_URL / RP_ID in the deployment environment; localhost is only for local development.
RP_ID = _resolve_rp_id()
ORIGIN = _resolve_origin()

rp = PublicKeyCredentialRpEntity(id=RP_ID, name=os.environ.get("RP_NAME", "Mahjong App"))
server = Fido2Server(rp)

def _admin_needs_passkey_setup():
    admin_user = User.query.filter_by(username=_get_default_admin_username()).first()
    return admin_user is None or not admin_user.credentials


@bp.route("/login")
def login_page():
    ensure_default_admin_user()
    return render_template(
        "login.html",
        users=User.query.order_by(User.username.asc()).all(),
        default_admin_username=_get_default_admin_username(),
        admin_needs_setup=_admin_needs_passkey_setup(),
    )

@bp.route("/admin/users", methods=["GET", "POST"])
def admin_users():
    current_user_id = session.get("login_user_id")
    if not current_user_id:
        return redirect(url_for("auth.login_page"))

    current_user = User.query.get(current_user_id)
    admin_username = _get_default_admin_username()
    if not current_user or current_user.username != admin_username:
        return jsonify({"error": "Admin access required"}), 403

    error_message = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        if not username:
            error_message = "ユーザー名を入力してください。"
        elif username.lower() == admin_username.lower():
            error_message = "管理者アカウントは追加できません。"
        elif User.query.filter_by(username=username).first():
            error_message = "そのユーザー名はすでに存在します。"
        else:
            db.session.add(User(username=username))
            db.session.commit()
            return redirect(url_for("auth.admin_users", message=f"ユーザー '{username}' を作成しました。"))

    return render_template(
        "admin_users.html",
        users=User.query.order_by(User.username.asc()).all(),
        players=Player.query.order_by(Player.id).all(),
        error_message=error_message,
        message=request.args.get("message"),
        admin_username=admin_username,
    )

@bp.route("/admin/users/<int:user_id>/player", methods=["POST"])
def admin_link_user_player(user_id):
    current_user_id = session.get("login_user_id")
    if not current_user_id:
        return redirect(url_for("auth.login_page"))

    current_user = User.query.get(current_user_id)
    if not current_user or current_user.username != _get_default_admin_username():
        return jsonify({"error": "Admin access required"}), 403

    user = User.query.get_or_404(user_id)
    player_id = request.form.get("player_id", type=int)

    if player_id:
        player = Player.query.get_or_404(player_id)
        other = User.query.filter(User.player_id == player.id, User.id != user.id).first()
        if other:
            return redirect(url_for(
                "auth.admin_users",
                message=f"プレイヤー '{player.name}' はすでに '{other.username}' に紐付いています。",
            ))
        user.player_id = player.id
        message = f"'{user.username}' をプレイヤー '{player.name}' に紐付けました。"
    else:
        user.player_id = None
        message = f"'{user.username}' のプレイヤー紐付けを解除しました。"

    db.session.commit()
    return redirect(url_for("auth.admin_users", message=message))

@bp.route("/admin/users/<int:user_id>/passkey", methods=["POST"])
def admin_issue_passkey_registration(user_id):
    current_user_id = session.get("login_user_id")
    if not current_user_id:
        return redirect(url_for("auth.login_page"))

    current_user = User.query.get(current_user_id)
    admin_username = _get_default_admin_username()
    if not current_user or current_user.username != admin_username:
        return jsonify({"error": "Admin access required"}), 403

    user = User.query.get_or_404(user_id)

    token = uuid4().hex
    user.passkey_enrollment_token = token
    user.passkey_enrollment_expires_at = datetime.utcnow() + timedelta(hours=24)
    db.session.commit()

    url = url_for("auth.enroll_user_passkey", token=token, _external=True)
    return redirect(url_for("auth.admin_users", message=f"{user.username} の登録URLをクリップボードにコピーしました: {url}"))

@bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
def admin_delete_user(user_id):
    current_user_id = session.get("login_user_id")
    if not current_user_id:
        return redirect(url_for("auth.login_page"))

    current_user = User.query.get(current_user_id)
    admin_username = _get_default_admin_username()
    if not current_user or current_user.username != admin_username:
        return jsonify({"error": "Admin access required"}), 403

    user = User.query.get_or_404(user_id)
    if user.username == admin_username:
        return redirect(url_for("auth.admin_users", message="管理者アカウントは削除できません。"))

    for credential in list(user.credentials):
        db.session.delete(credential)
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for("auth.admin_users", message=f"ユーザー '{user.username}' を削除しました。"))

@bp.route("/admin/credentials/<int:credential_id>/delete", methods=["POST"])
def admin_delete_credential(credential_id):
    current_user = _logged_in_user()
    if not current_user:
        return redirect(url_for("auth.login_page"))
    admin_username = _get_default_admin_username()
    if current_user.username != admin_username:
        return jsonify({"error": "Admin access required"}), 403

    credential = Credential.query.get_or_404(credential_id)
    owner = credential.user
    # 管理者の Passkey が0件になると、ログイン画面から誰でも管理者を初回登録できてしまうため残す
    if owner.username == admin_username and len(owner.credentials) <= 1:
        return redirect(url_for("auth.admin_users", message="管理者の最後の Passkey は削除できません。"))

    db.session.delete(credential)
    db.session.commit()
    return redirect(url_for("auth.admin_users", message=f"'{owner.username}' の Passkey を1件削除しました。"))

def _clear_auth_session():
    for key in ("auth_state", "auth_user_id", "auth_rp_id", "auth_members_only"):
        session.pop(key, None)


@bp.route("/login_start", methods=["POST"])
def login_start():
    """Passkey ログインを開始する。

    username なし: 登録済みの全 Passkey（管理者分を除く）を候補に指定する（ワンタップログイン）。
        端末に保存されない形式の古い Passkey も、IDを指定すれば端末が応答できる。
        同じ端末に管理者と通常ユーザーの Passkey がある場合は、通常ユーザーとしてログインさせる。
    username あり: そのユーザーの Passkey に限定する（管理者ログイン・予備）
    """
    try:
        payload = request.get_json(silent=True) or {}
        username = (payload.get("username") or "").strip()

        request_server, rp_id = _make_webauthn_server(request)
        logger.debug("Using RP_ID for login: %s", rp_id)

        allow_credentials = None
        user = None
        if username:
            user = User.query.filter_by(username=username).first()
            if not user:
                return jsonify({"error": "User not found"}), 404
            if not user.credentials:
                return jsonify({"error": "No credential registered"}), 400
            allow_credentials = [AttestedCredentialData(c.public_key) for c in user.credentials]
        else:
            admin_user = User.query.filter_by(username=_get_default_admin_username()).first()
            member_credentials = Credential.query.filter(
                Credential.user_id != (admin_user.id if admin_user else -1)
            ).all()
            # 通常ユーザーの Passkey がまだ1件もない間は、管理者の Passkey も候補に入れる
            candidates = member_credentials or Credential.query.all()
            if not candidates:
                return jsonify({"error": "No credential registered"}), 400
            allow_credentials = [
                PublicKeyCredentialDescriptor(type="public-key", id=c.credential_id) for c in candidates
            ]

        options, state = request_server.authenticate_begin(
            allow_credentials,
            user_verification="preferred",
        )

        _clear_auth_session()
        session["auth_state"] = state
        session["auth_rp_id"] = rp_id
        if user:
            # ユーザー指定時は、そのユーザーの Passkey 以外を受け付けない
            session["auth_user_id"] = user.id
        elif member_credentials:
            # ワンタップ時に管理者の Passkey は候補に入れていないので、返ってきても受け付けない
            session["auth_members_only"] = True

        pk = options.public_key
        return jsonify({
            "challenge": websafe_encode(pk.challenge),
            "rpId": pk.rp_id,
            "timeout": pk.timeout,
            "allowCredentials": [
                {"type": c.type, "id": websafe_encode(c.id)} for c in (pk.allow_credentials or [])
            ],
            "userVerification": pk.user_verification,
        })
    except Exception:
        logger.exception("login_start failed")
        return jsonify({"error": "Login start failed"}), 500


@bp.route("/login_finish", methods=["POST"])
def login_finish():
    try:
        auth_state = session.get("auth_state")
        if not auth_state:
            return jsonify({"error": "Authentication session not found"}), 400

        data = request.get_json(silent=True) or {}
        decoded_raw_id = base64url_decode(data["rawId"])

        # 端末から返ってきた Passkey の ID で、DB に登録済みの Passkey を探す
        cred_record = Credential.query.filter_by(credential_id=decoded_raw_id).first()
        if not cred_record:
            logger.debug("Unknown credential id: %s", decoded_raw_id.hex())
            raise ValueError("Credential not registered")

        expected_user_id = session.get("auth_user_id")
        if expected_user_id and cred_record.user_id != expected_user_id:
            raise ValueError("Credential does not belong to the requested user")
        if session.get("auth_members_only") and cred_record.user.username == _get_default_admin_username():
            raise ValueError("Admin passkey was not offered for one-tap login")

        user = cred_record.user
        cred_data = AttestedCredentialData(cred_record.public_key)

        auth_response = AuthenticationResponseWA(
            raw_id=decoded_raw_id,
            response=AuthenticatorAssertionResponse(
                client_data=CollectedClientData(base64url_decode(data["clientDataJSON"])),
                authenticator_data=AuthenticatorData(base64url_decode(data["authenticatorData"])),
                signature=base64url_decode(data["signature"]),
            ),
        )

        auth_server, _ = _make_webauthn_server(rp_id=session.get("auth_rp_id") or RP_ID)
        auth_server.authenticate_complete(auth_state, [cred_data], auth_response)

        # 使われていない古い Passkey を見分けられるよう、最終使用日時を記録する
        cred_record.last_used_at = datetime.utcnow()
        db.session.commit()

        # 認証が成功した場合に初めてログイン状態にする（PERMANENT_SESSION_LIFETIME の間保持）
        _clear_auth_session()
        session.permanent = True
        session["login_user_id"] = user.id
        session["login_username"] = user.username
        session["login_is_admin"] = (user.username == _get_default_admin_username())
        logger.debug("Authentication succeeded: login_user_id set to %d", user.id)

        return jsonify({"status": "ok"})
    except Exception:
        logger.exception("login_finish failed")
        _clear_auth_session()
        session.pop("login_user_id", None)
        return jsonify({"error": "Login failed"}), 400

#以下デバッグ用登録処理
@bp.route("/register")
def register_page():
    if not _public_registration_enabled():
        return (
            render_template(
                "login.html",
                registration_disabled=True,
                registration_message="新規ユーザー登録は管理者が承認したユーザーのみです。管理者から届いた登録URLを開いてください。",
                default_admin_username=_get_default_admin_username(),
                admin_needs_setup=_admin_needs_passkey_setup(),
            ),
            403,
        )
    return render_template("register.html", username=None, enrollment_mode=False)

@bp.route("/enroll/<token>")
def enroll_user_passkey(token):
    user = User.query.filter_by(passkey_enrollment_token=token).first()
    if not user:
        # 使用済み・再発行済み・URLの誤りのいずれか（DBにトークンが残っていないので区別できない）
        _clear_enrollment_session()
        return render_template("enroll_error.html", reason="invalid"), 404

    if user.passkey_enrollment_expires_at and user.passkey_enrollment_expires_at < datetime.utcnow():
        _clear_enrollment_session()
        return render_template(
            "enroll_error.html",
            reason="expired",
            expired_at=user.passkey_enrollment_expires_at,
        ), 410

    session["register_username"] = user.username
    session["enrollment_token"] = token
    session["allow_admin_enrollment"] = True
    return render_template("register.html", username=user.username, enrollment_mode=True)

ENROLLMENT_EXPIRED_MESSAGE = "登録URLの有効期限が切れたか、無効になっています。管理者に登録URLの再発行を依頼してください。"


def _get_enrollment_user():
    """セッションの登録トークンから、有効期限内の登録対象ユーザーを返す"""
    token = session.get("enrollment_token")
    if not token:
        return None
    user = User.query.filter_by(passkey_enrollment_token=token).first()
    if not user:
        return None
    if user.passkey_enrollment_expires_at and user.passkey_enrollment_expires_at < datetime.utcnow():
        return None
    return user


def _clear_enrollment_session():
    session.pop("register_username", None)
    session.pop("allow_admin_enrollment", None)
    session.pop("enrollment_token", None)


def _logged_in_user():
    user_id = session.get("login_user_id")
    return db.session.get(User, user_id) if user_id else None


@bp.route("/account/passkeys")
def account_passkeys():
    """ログイン中の本人が、自分の Passkey を確認・この端末に追加する画面"""
    user = _logged_in_user()
    if not user:
        return redirect(url_for("auth.login_page", next=request.path))
    credentials = sorted(user.credentials, key=lambda c: (c.created_at is None, c.created_at or datetime.min))
    return render_template("account_passkeys.html", user=user, credentials=credentials)


@bp.route("/register_start", methods=["POST"])
def register_start():
    try:
        payload = request.get_json(silent=True) or {}
        # ログイン中の本人による追加（前回の途中状態は持ち越さない）
        session.pop("register_self_user_id", None)
        self_add = bool(payload.get("add_to_current_user"))

        admin_enrollment = session.get("allow_admin_enrollment") and not self_add
        if self_add:
            current_user = _logged_in_user()
            if not current_user:
                return jsonify({"error": "Login required"}), 401
            username = current_user.username
            session["register_self_user_id"] = current_user.id
        elif admin_enrollment:
            # 登録URL経由では、トークンに紐づくユーザー以外は登録させない（リクエストのusernameは無視）
            enrollment_user = _get_enrollment_user()
            if not enrollment_user:
                _clear_enrollment_session()
                return jsonify({"error": ENROLLMENT_EXPIRED_MESSAGE}), 400
            username = enrollment_user.username
        else:
            username = (request.json.get("username") or "").strip() if request.is_json and request.json else ""
            session_username = (session.get("register_username") or "").strip()
            if not username and session_username:
                username = session_username
        if not username:
            return jsonify({"error": "Username required"}), 400

        if (not _public_registration_enabled() and not admin_enrollment and not self_add
                and not _can_register_username(username)):
            return jsonify({
                "error": "Public registration is disabled. Please ask an administrator to create your account."
            }), 403

        user = User.query.filter_by(username=username).first()
        # 管理者が発行した登録URL経由、またはログイン中の本人なら、登録済みでも Passkey を追加できる
        if user and user.credentials and not admin_enrollment and not self_add:
            return jsonify({"error": "This user already has a passkey registered"}), 400

        session["register_username"] = username

        register_server, rp_id = _make_webauthn_server(request)
        logger.debug("Using RP_ID for registration: %s", rp_id)
        session["register_rp_id"] = rp_id

        user_id_bytes = str(user.id if user else username).encode("utf-8") if user else username.encode("utf-8")

        options, state = register_server.register_begin(
            user={
                "id": user_id_bytes,
                "name": username,
                "displayName": username,
            },
            credentials=[],
            resident_key_requirement="required",
            user_verification="preferred"
        )

        # state を session に保存
        session["register_state"] = state

        # options.public_key から取得（ここが重要）
        pk = options.public_key

        logger.debug("challenge type: %s", type(pk.challenge))
        logger.debug("encoded: %s", websafe_encode(pk.challenge))

        response = {
            "challenge": websafe_encode(pk.challenge),
            "rp": {
                "id": pk.rp.id,
                "name": pk.rp.name,
            },
            "user": {
                "id": websafe_encode(pk.user.id),
                "name": pk.user.name,
                "displayName": pk.user.display_name if hasattr(pk.user, "display_name") else pk.user.name,
            },
            "pubKeyCredParams": [
                {"type": p.type, "alg": p.alg} for p in pk.pub_key_cred_params
            ],
            # ブラウザ(WebAuthn)が読むのは camelCase のキー。
            # snake_case のまま渡すと residentKey が無視され、ユーザー名なしログインで見つからない Passkey になる
            "authenticatorSelection": {
                "residentKey": "required",
                "requireResidentKey": True,
                "userVerification": "preferred",
            },
            "attestation": "none",
        }

        return jsonify(response)

    except Exception as e:
        logger.exception("register_start failed")
        return jsonify({"error": "Registration start failed"}), 500

from base64 import urlsafe_b64decode

def base64url_decode(base64url):
    # URLセーフなBase64でエンコードされた文字列をデコード
    return urlsafe_b64decode(base64url + '==' if len(base64url) % 4 else base64url)

@bp.route("/register_finish", methods=["POST"])
def register_finish():
    try:
        username = session.get("register_username")
        if not username:
            return jsonify({"error": "Registration session not found"}), 400

        data = request.json

        decoded_raw_id = base64url_decode(data["id"])

        logger.debug("=== register_finish start ===")
        logger.debug("Username: %s", username)

        register_server, _ = _make_webauthn_server(rp_id=session.get("register_rp_id") or RP_ID)
        credential = register_server.register_complete(
            session["register_state"],
            {
                "id": decoded_raw_id,
                "rawId": websafe_decode(data["rawId"]),
                "response": {
                    "clientDataJSON": websafe_decode(data["clientDataJSON"]),
                    "attestationObject": websafe_decode(data["attestationObject"]),
                },
            }
        )

        logger.debug("register_complete succeeded")

        cred_data = credential.credential_data
        logger.debug("credential_id (hex): %s", cred_data.credential_id.hex())
        logger.debug("credential_id (len): %d bytes", len(cred_data.credential_id))

        user = User.query.filter_by(username=username).first()
        self_user_id = session.get("register_self_user_id")
        if self_user_id:
            # ログイン中の本人による追加: 開始時と同じ本人がログインしたままであること
            if not user or user.id != self_user_id or session.get("login_user_id") != self_user_id:
                return jsonify({"error": "Login required"}), 401
        elif session.get("allow_admin_enrollment"):
            enrollment_user = _get_enrollment_user()
            if not enrollment_user or not user or enrollment_user.id != user.id:
                return jsonify({"error": ENROLLMENT_EXPIRED_MESSAGE}), 400
        if not user:
            user = User(username=username)
            db.session.add(user)
            db.session.flush()
            logger.debug("User created with id: %d", user.id)
        elif user.credentials and not self_user_id and not session.get("allow_admin_enrollment"):
            return jsonify({"error": "This user already has a passkey registered"}), 400

        cred = Credential(
            user_id=user.id,
            credential_id=cred_data.credential_id,
            public_key=bytes(cred_data),
            sign_count=credential.counter,
        )
        db.session.add(cred)
        if not self_user_id:
            # 登録URLは使い切りにする（commit前に消さないとDBに反映されない）
            user.passkey_enrollment_token = None
            user.passkey_enrollment_expires_at = None
        db.session.commit()
        logger.debug("Credential saved successfully")

        session.pop("register_state", None)
        session.pop("register_rp_id", None)
        if self_user_id:
            session.pop("register_self_user_id", None)
            session.pop("register_username", None)
        else:
            _clear_enrollment_session()
        logger.debug("Session cleared after registration")

        return jsonify({"status": "ok"})

    except Exception as e:
        db.session.rollback()
        logger.exception("register_finish failed")
        return jsonify({"error": "Registration failed"}), 400
