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
const userButtons = document.querySelectorAll('.user-option');
const usernameInput = document.getElementById('username');

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

userButtons.forEach((button) => {
    button.addEventListener('click', () => {
        const username = button.dataset.username;
        usernameInput.value = username;
        usernameInput.focus();
        const errorDiv = document.getElementById('error');
        if (errorDiv) errorDiv.textContent = '';
    });
});

document.getElementById("loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = usernameInput.value.trim();
    const errorDiv = document.getElementById("error");
    errorDiv.textContent = "";

    if (!username) {
        errorDiv.textContent = "ユーザー名を選択してください";
        return;
    }

    try {
        // 1️⃣ サーバーに challenge をリクエスト
        const startResp = await fetch("/login_start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username })
        });
        const options = await startResp.json();

        if (options.error) {
            if (username === DEFAULT_ADMIN_USERNAME && options.error.includes("No credential registered")) {
                await registerAdminPasskey(username, errorDiv);
                return;
            }
            errorDiv.textContent = options.error;
            return;
        }

        // options.allowCredentials が存在するか確認
        if (!options.allowCredentials) {
            errorDiv.textContent = "認証情報が見つかりません。";
            return;
        }

        // allowCredentials の id を Base64 から ArrayBuffer に変換
        const allowCredentials = await Promise.all(
            options.allowCredentials.map(async cred => ({
                id: await base64ToArrayBuffer(cred.id),  // id を正しく変換
                type: cred.type || "public-key"  // type が無い場合は 'public-key' とする
            }))
        );
        
        // デバッグ：登録されている credential id をログに出力
        console.log("Allow Credentials count:", allowCredentials.length);
        options.allowCredentials.forEach((cred, idx) => {
            console.log(`Credential ${idx} (base64url):`, cred.id);
            console.log(`Credential ${idx} (array):`, new Uint8Array(allowCredentials[idx].id));
        });

        // challenge の存在を確認してから変換
        if (!options.challenge) {
            errorDiv.textContent = "サーバーから challenge が返されていません。";
            return;
        }
        const challengeBuffer = await base64ToArrayBuffer(options.challenge);

        // rp や user.id の存在確認
        if (!options.rp || !options.user || !options.user.id) {
            errorDiv.textContent = "サーバーから必要なユーザーデータが返されていません。";
            return;
        }

        const publicKey = {
            challenge: challengeBuffer,
            timeout: options.timeout,
            rpId: options.rpId,
            userVerification: options.userVerification,
            allowCredentials: allowCredentials,
        };

        // WebAuthn サポートの確認
        if (!window.PublicKeyCredential) {
            errorDiv.textContent = "このブラウザはWebAuthnをサポートしていません。";
            return;
        }

        // 2️⃣ デバイスで生体認証
        const credential = await navigator.credentials.get({ publicKey });

        if (!credential) {
            errorDiv.textContent = "認証に失敗しました。";
            return;
        }

        // デバッグ：スマホから返ってきた credential の rawId をログに出力
        console.log("Credential from device (rawId):", new Uint8Array(credential.rawId));
        console.log("Credential from device (rawId hex):", Array.from(new Uint8Array(credential.rawId)).map(b => b.toString(16).padStart(2, '0')).join(''));

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

        if (!finishResp.ok) {
            errorDiv.textContent = "サーバーエラー: ログイン処理が完了しませんでした。";
            return;
        }

        const result = await finishResp.json();

        if (result.status === "ok") {
            window.location.href = "/";
        } else {
            errorDiv.textContent = "ログイン失敗: " + (result.error || "不明なエラー");
        }

    } catch (err) {
        console.error(err);
        errorDiv.textContent = "エラーが発生しました: " + err;
    }
});
