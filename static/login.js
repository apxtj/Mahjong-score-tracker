async function base64ToArrayBuffer(base64) {
    try {
        const binary = atob(base64.replace(/_/g, '/').replace(/-/g, '+'));
        const len = binary.length;
        const buffer = new ArrayBuffer(len);
        const view = new Uint8Array(buffer);
        for (let i = 0; i < len; i++) {
            view[i] = binary.charCodeAt(i);
        }
        return buffer;
    } catch (e) {
        console.error("base64ToArrayBuffer error:", e);
        throw new Error("Base64 decoding failed.");
    }
}

async function arrayBufferToBase64(buffer) {
    try {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
    } catch (e) {
        console.error("arrayBufferToBase64 error:", e);
        throw new Error("ArrayBuffer to Base64 encoding failed.");
    }
}


const DEFAULT_ADMIN_USERNAME = window.DEFAULT_ADMIN_USERNAME || 'admin';

async function registerAdminPasskey(username, errorDiv) {
    errorDiv.textContent = "管理者の Passkey を初回登録しています...";

    try {
        const startResp = await fetch("/register_start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username })
        });
        const options = await startResp.json();
        if (options.error) {
            errorDiv.textContent = options.error;
            return false;
        }

        const publicKey = {
            challenge: await base64ToArrayBuffer(options.challenge),
            rp: options.rp,
            user: {
                id: await base64ToArrayBuffer(options.user.id),
                name: options.user.name,
                displayName: options.user.displayName,
            },
            pubKeyCredParams: options.pubKeyCredParams,
            authenticatorSelection: options.authenticatorSelection,
            attestation: options.attestation,
            timeout: options.timeout,
        };

        const cred = await navigator.credentials.create({ publicKey });
        const data = {
            id: cred.id,
            rawId: await arrayBufferToBase64(cred.rawId),
            attestationObject: await arrayBufferToBase64(cred.response.attestationObject),
            clientDataJSON: await arrayBufferToBase64(cred.response.clientDataJSON)
        };

        const finishResp = await fetch("/register_finish", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data)
        });
        const result = await finishResp.json();

        if (result.status === "ok") {
            alert("管理者の Passkey を登録しました。ログインしてください。");
            window.location.reload();
            return true;
        }

        errorDiv.textContent = "Passkey 登録に失敗しました: " + (result.error || "不明なエラー");
        return false;
    } catch (err) {
        console.error(err);
        errorDiv.textContent = "Passkey 登録エラー: " + err;
        return false;
    }
}

async function loginWithPasskey(username, errorDiv) {
    errorDiv.textContent = "";

    if (!window.PublicKeyCredential) {
        errorDiv.textContent = "このブラウザはPasskeyに対応していません。";
        return;
    }

    try {
        // 1️⃣ サーバーに challenge をリクエスト（username なしなら端末の Passkey から選ぶ）
        const startResp = await fetch("/login_start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(username ? { username } : {})
        });
        const options = await startResp.json();

        if (options.error) {
            if (username === DEFAULT_ADMIN_USERNAME && options.error.includes("No credential registered")) {
                await registerAdminPasskey(username, errorDiv);
                return;
            }
            errorDiv.textContent = options.error === "User not found" ? "ユーザーが見つかりません。" : options.error;
            return;
        }

        const publicKey = {
            challenge: await base64ToArrayBuffer(options.challenge),
            timeout: options.timeout,
            rpId: options.rpId,
            userVerification: options.userVerification,
        };
        if (options.allowCredentials && options.allowCredentials.length > 0) {
            publicKey.allowCredentials = await Promise.all(
                options.allowCredentials.map(async cred => ({
                    id: await base64ToArrayBuffer(cred.id),
                    type: cred.type || "public-key"
                }))
            );
        }

        // 2️⃣ デバイスで生体認証
        const credential = await navigator.credentials.get({ publicKey });
        if (!credential) {
            errorDiv.textContent = "認証に失敗しました。";
            return;
        }

        // 3️⃣ 認証結果をサーバーに送信
        const authData = {
            rawId: await arrayBufferToBase64(credential.rawId),
            clientDataJSON: await arrayBufferToBase64(credential.response.clientDataJSON),
            authenticatorData: await arrayBufferToBase64(credential.response.authenticatorData),
            signature: await arrayBufferToBase64(credential.response.signature)
        };

        const finishResp = await fetch("/login_finish", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(authData)
        });
        const result = await finishResp.json().catch(() => ({}));

        if (finishResp.ok && result.status === "ok") {
            window.location.href = "/";
            return;
        }
        errorDiv.textContent = "ログインできませんでした。この端末の Passkey が登録されていない可能性があります。";
    } catch (err) {
        console.error(err);
        if (err && err.name === "NotAllowedError") {
            errorDiv.textContent = "認証がキャンセルされました。";
            return;
        }
        errorDiv.textContent = "エラーが発生しました: " + err;
    }
}

const errorDiv = document.getElementById("error");

document.getElementById("passkeyLoginButton").addEventListener("click", () => {
    loginWithPasskey("", errorDiv);
});

const adminSetupButton = document.getElementById("adminSetupButton");
if (adminSetupButton) {
    adminSetupButton.addEventListener("click", () => {
        registerAdminPasskey(DEFAULT_ADMIN_USERNAME, errorDiv);
    });
}

document.getElementById("loginForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const username = document.getElementById("username").value.trim();
    if (!username) {
        errorDiv.textContent = "ユーザー名を入力してください";
        return;
    }
    loginWithPasskey(username, errorDiv);
});
