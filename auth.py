from flask import Blueprint, request, jsonify, session, render_template
import logging
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

# RP (Relying Party) and origin should be configurable for production
# Use environment variables when deployed (Render), otherwise keep localhost defaults
RP_ID = os.environ.get("RP_ID", "localhost")
ORIGIN = os.environ.get("ORIGIN", "http://localhost:5000")

rp = PublicKeyCredentialRpEntity(id=RP_ID, name=os.environ.get("RP_NAME", "Mahjong App"))
server = Fido2Server(rp)

@bp.route("/login")
def login_page():
    return render_template("login.html")

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
    return render_template("register.html")

@bp.route("/register_start", methods=["POST"])
def register_start():
    try:
        username = request.json.get("username")
        if not username:
            return jsonify({"error": "Username required"}), 400

        # 既に登録済みかチェック
        user = User.query.filter_by(username=username).first()
        if user:
            return jsonify({"error": "Username already exists"}), 400

        # ユーザーを DB に保存せず、セッションに仮保存
        # register_finish で credential 作成成功後に初めて DB に保存
        session["register_username"] = username
        
        # FIDO2 サーバーにユーザー情報を渡す
        # 仮の user_id として username のハッシュを使用
        user_id_bytes = username.encode("utf-8")
        
        options, state = server.register_begin(
            {
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

        # base64urlデコード
        decoded_raw_id = base64url_decode(data["id"])
        
        logger.debug("=== register_finish start ===")
        logger.debug("Username: %s", username)
        
        # register_complete 呼び出し
        credential = server.register_complete(
            session["register_state"],
            {
                "id": decoded_raw_id,  # id をデコードしてバイト列に変換
                "rawId": websafe_decode(data["rawId"]),  # rawId はデコードしてバイト列に
                "response": {
                    "clientDataJSON": websafe_decode(data["clientDataJSON"]),
                    "attestationObject": websafe_decode(data["attestationObject"]),
                },
            }
        )
        
        logger.debug("register_complete succeeded")
        
        # ここまできたら、credential が返ってきているはず
        cred_data = credential.credential_data
        logger.debug("credential_id (hex): %s", cred_data.credential_id.hex())
        logger.debug("credential_id (len): %d bytes", len(cred_data.credential_id))

        # credential 作成成功後に初めてユーザーを作成
        user = User(username=username)
        db.session.add(user)
        db.session.flush()  # ID を生成させるが、コミットはまだ
        logger.debug("User created with id: %d", user.id)
        
        # 公開鍵をバイト列として保存
        cred = Credential(
            user_id=user.id,
            credential_id=cred_data.credential_id,
            public_key=bytes(cred_data),  # AttestedCredentialDataのバイト表現をそのまま保存
            sign_count=credential.counter,
        )
        db.session.add(cred)
        db.session.commit()
        logger.debug("Credential saved successfully")

        # セッションをクリア
        session.pop("register_state", None)
        session.pop("register_username", None)
        logger.debug("Session cleared after registration")

        return jsonify({"status": "ok"})

    except Exception as e:
        # エラー時はロールバック（User と Credential が保存されない）
        db.session.rollback()
        logger.exception("register_finish failed")
        return jsonify({"error": "Registration failed"}), 400
