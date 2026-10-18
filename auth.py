from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for
import logging
from urllib.parse import urlparse
from uuid import uuid4
from fido2.server import Fido2Server,AuthenticationResponse
from fido2.webauthn import PublicKeyCredentialRequestOptions, AttestedCredentialData, CollectedClientData, AuthenticatorData, AuthenticatorAssertionResponse, AuthenticationResponse as AuthenticationResponseWA
from fido2.utils import websafe_encode, websafe_decode
from models import db, User, Credential
import os
import traceback

# Configure logger with debug mode
debug_mode = os.environ.get("FLASK_DEBUG", "False") == "True"
log_level = logging.DEBUG if debug_mode else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)

from fido2.webauthn import PublicKeyCredentialRpEntity
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


# RP (Relying Party) and origin should be configurable for production.
# Set ORIGIN / APP_URL / RP_ID in the deployment environment; localhost is only for local development.
RP_ID = _resolve_rp_id()
ORIGIN = _resolve_origin()

rp = PublicKeyCredentialRpEntity(id=RP_ID, name=os.environ.get("RP_NAME", "Mahjong App"))
server = Fido2Server(rp)

@bp.route("/login")
def login_page():
    ensure_default_admin_user()
    users = User.query.order_by(User.username.asc()).all()
    return render_template(
        "login.html",
        users=users,
        default_admin_username=_get_default_admin_username(),
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
        error_message=error_message,
        message=request.args.get("message"),
        admin_username=admin_username,
    )

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
    if user.username == admin_username:
        return redirect(url_for("auth.admin_users", message="管理者にはPasskey登録URLを発行できません。"))

    token = uuid4().hex
    user.passkey_enrollment_token = token
    user.passkey_enrollment_expires_at = datetime.utcnow() + timedelta(hours=24)
    db.session.commit()

    url = url_for("auth.enroll_user_passkey", token=token, _external=True)
    return redirect(url_for("auth.admin_users", message=f"{user.username} の登録URL: {url}"))

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

@bp.route("/login_start", methods=["POST"])
def login_start():
    try:
        username = request.json.get("username")
        if not username:
            return jsonify({"error": "Username required"}), 400

        logger.debug("=== login_start ===")
        logger.debug("Username: %s", username)

        user = User.query.filter_by(username=username).first()
        if not user:
            logger.debug("User not found")
            return jsonify({"error": "User not found"}), 404

        logger.debug("User found with id: %d", user.id)

        # 登録されているCredentialを取得
        credential = Credential.query.filter_by(user_id=user.id).first()
        if not credential:
            logger.debug("No credential registered for user")
            return jsonify({"error": "No credential registered"}), 400

        logger.debug("Credential found with id (DB pk): %d", credential.id)
        logger.debug("Credential credential_id (hex): %s", credential.credential_id.hex())

        # public_keyはAttestedCredentialDataのバイト表現なので、直接復元する
        cred_data = AttestedCredentialData(credential.public_key)

        # FIDO2 サーバーで認証開始（チャレンジとオプションを生成）
        options, state = server.authenticate_begin([cred_data])

        # 認証状態をセッションに保存（バイナリはセッションに入れない）
        session["auth_state"] = state
        # ← login_user_id はまだ設定しない（login_finish で認証成功後に設定）
        session["auth_user_id"] = user.id  # 認証用の一時的なID（ログイン状態ではない）
        # 後で使用するためにDBレコードIDのみを保存（バイナリはDBに保持）
        session["auth_credential_id"] = credential.id
        
        logger.debug("Session updated: auth_user_id=%d (temp), auth_credential_id=%d", user.id, credential.id)

        # チャレンジをbase64urlエンコードしてクライアントに渡す
        challenge_encoded = websafe_encode(options.public_key.challenge)

        # デバッグ：DBに保存されているcredential_idをログ出力
        logger.debug("=== login_start debug ===")
        logger.debug("RP_ID: %s", RP_ID)
        logger.debug("ORIGIN: %s", ORIGIN)
        logger.debug("DB credential_id (hex): %s", credential.credential_id.hex())
        logger.debug("DB credential_id (len): %d bytes", len(credential.credential_id))
        
        # allow_credentials に含まれる credential_id もログ出力
        allow_creds_list = []
        for cred in options.public_key.allow_credentials:
            cred_id_hex = cred.id.hex()
            logger.debug("allow_credentials id (hex): %s", cred_id_hex)
            logger.debug("allow_credentials id (len): %d bytes", len(cred.id))
            allow_creds_list.append({
                "type": cred.type,
                "id": websafe_encode(cred.id)
            })

        return jsonify({
            "challenge": challenge_encoded,
            "rpId": options.public_key.rp_id,
            "timeout": options.public_key.timeout,
            "allowCredentials": allow_creds_list,
            "userVerification": options.public_key.user_verification,
            "rp": options.public_key.rp_id,
            "user": {
                "id": websafe_encode(str(user.id).encode()),
                "name": user.username,
                "displayName": user.username
            }
        })
    except Exception as e:
        logger.exception("login_start failed")
        return jsonify({"error": "Login start failed"}), 500



from fido2.client import PublicKeyCredentialDescriptor  # 必要なインポートを追加


from fido2.webauthn import AuthenticatorAssertionResponse

@bp.route("/login_finish", methods=["POST"])
def login_finish():
    try:
        logger.debug("=== login_finish start ===")
        
        # 認証用の一時的なユーザーID を取得（ログイン状態ではない）
        auth_user_id = session.get("auth_user_id")
        if not auth_user_id:
            logger.debug("auth_user_id not found in session")
            return jsonify({"error": "Authentication session not found"}), 400

        user = User.query.get(auth_user_id)
        if not user:
            logger.debug("User from session not found")
            return jsonify({"error": "User not found"}), 400

        logger.debug("User from session: id=%d, username=%s", user.id, user.username)

        data = request.json

        # Base64urlでエンコードされているデータをデコード
        decoded_raw_id = base64url_decode(data["rawId"])
        decoded_client_data_json = base64url_decode(data["clientDataJSON"])
        decoded_authenticator_data = base64url_decode(data["authenticatorData"])
        decoded_signature = base64url_decode(data["signature"])
        
        # デバッグ：スマホから返ってきたrawIdをログ出力
        logger.debug("=== login_finish debug ===")
        logger.debug("Received rawId (hex): %s", decoded_raw_id.hex())
        logger.debug("Received rawId (len): %d bytes", len(decoded_raw_id))

        # 認証状態をセッションから取得
        auth_state = session.get("auth_state")
        if not auth_state:
            logger.debug("auth_state not found in session")
            raise ValueError("Authentication state not found in session")

        # セッションから保存されたCredentialレコードIDを取得し、DBから復元
        auth_cred_id = session.get("auth_credential_id")
        if not auth_cred_id:
            logger.debug("auth_credential_id not found in session")
            raise ValueError("Credential id not found in session")
        
        logger.debug("auth_credential_id from session: %d", auth_cred_id)
        
        cred_record = Credential.query.get(auth_cred_id)
        if not cred_record:
            logger.debug("Credential record not found in DB")
            raise ValueError("Credential record not found")

        logger.debug("Credential record found: credential_id (hex) = %s", cred_record.credential_id.hex())

        # AttestedCredentialDataを復元（public_keyにbytesで保存している）
        cred_data = AttestedCredentialData(cred_record.public_key)

        # CollectedClientDataオブジェクトを作成（バイト列を直接渡す）
        client_data = CollectedClientData(decoded_client_data_json)

        # AuthenticatorDataオブジェクトを作成
        authenticator_data = AuthenticatorData(decoded_authenticator_data)

        # AuthenticatorAssertionResponseオブジェクトを作成
        assertion_response = AuthenticatorAssertionResponse(
            client_data=client_data,
            authenticator_data=authenticator_data,
            signature=decoded_signature,
        )

        # AuthenticationResponseオブジェクトを作成
        auth_response = AuthenticationResponseWA(
            raw_id=decoded_raw_id,
            response=assertion_response,
        )

        logger.debug("About to call authenticate_complete")

        # FIDO2サーバーで認証結果を完了させる
        credential = server.authenticate_complete(
            auth_state,
            [cred_data],
            auth_response
        )

        logger.debug("authenticate_complete succeeded")

        # 認証が成功した場合に初めて login_user_id を設定（ログイン状態にする）
        session["login_user_id"] = user.id
        session["login_username"] = user.username
        session["login_is_admin"] = (user.username == _get_default_admin_username())
        # 認証用の一時データをクリア
        session.pop("auth_state", None)
        session.pop("auth_user_id", None)
        session.pop("auth_credential_id", None)
        logger.debug("Authentication succeeded: login_user_id set to %d", user.id)
        
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.exception("login_finish failed")
        # エラー時はセッションを完全にクリア（認証失敗）
        session.pop("auth_state", None)
        session.pop("auth_user_id", None)
        session.pop("auth_credential_id", None)
        session.pop("login_user_id", None)
        logger.debug("Session completely cleared on error")
        return jsonify({"error": "Login failed"}), 400

#以下デバッグ用登録処理
@bp.route("/register")
def register_page():
    if not _public_registration_enabled():
        return (
            render_template(
                "login.html",
                users=User.query.order_by(User.username.asc()).all(),
                registration_disabled=True,
                registration_message="新規ユーザー登録は管理者が承認したユーザーのみです。ログイン画面で登録済みユーザーを選択してください。",
                default_admin_username=_get_default_admin_username(),
            ),
            403,
        )
    return render_template("register.html")

@bp.route("/enroll/<token>")
def enroll_user_passkey(token):
    user = User.query.filter_by(passkey_enrollment_token=token).first()
    if not user:
        return jsonify({"error": "Invalid or expired enrollment token"}), 400

    if user.passkey_enrollment_expires_at and user.passkey_enrollment_expires_at < datetime.utcnow():
        return jsonify({"error": "Enrollment token expired"}), 400

    session["register_username"] = user.username
    session["enrollment_token"] = token
    session["allow_admin_enrollment"] = True
    return render_template("register.html")

@bp.route("/register_start", methods=["POST"])
def register_start():
    try:
        username = (request.json.get("username") or "").strip()
        if not username:
            return jsonify({"error": "Username required"}), 400

        admin_enrollment = session.get("allow_admin_enrollment")
        if not _public_registration_enabled() and not admin_enrollment and not _can_register_username(username):
            return jsonify({
                "error": "Public registration is disabled. Please ask an administrator to create your account."
            }), 403

        user = User.query.filter_by(username=username).first()
        if user and user.credentials:
            return jsonify({"error": "This user already has a passkey registered"}), 400

        session["register_username"] = username

        user_id_bytes = str(user.id if user else username).encode("utf-8") if user else username.encode("utf-8")

        options, state = server.register_begin(
            user={
                "id": user_id_bytes,
                "name": username,
                "displayName": username,
            },
            credentials=[],
            resident_key_requirement="required",
            authenticator_attachment="platform",
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
            "authenticatorSelection": pk.authenticator_selection,
            "attestation": pk.attestation,
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

        credential = server.register_complete(
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
        if not user:
            user = User(username=username)
            db.session.add(user)
            db.session.flush()
            logger.debug("User created with id: %d", user.id)
        elif user.credentials:
            return jsonify({"error": "This user already has a passkey registered"}), 400

        cred = Credential(
            user_id=user.id,
            credential_id=cred_data.credential_id,
            public_key=bytes(cred_data),
            sign_count=credential.counter,
        )
        db.session.add(cred)
        db.session.commit()
        logger.debug("Credential saved successfully")

        session.pop("register_state", None)
        session.pop("register_username", None)
        session.pop("allow_admin_enrollment", None)
        session.pop("enrollment_token", None)
        if user.passkey_enrollment_token:
            user.passkey_enrollment_token = None
            user.passkey_enrollment_expires_at = None
        logger.debug("Session cleared after registration")

        return jsonify({"status": "ok"})

    except Exception as e:
        db.session.rollback()
        logger.exception("register_finish failed")
        return jsonify({"error": "Registration failed"}), 400
