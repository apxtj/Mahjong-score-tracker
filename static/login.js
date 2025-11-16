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


document.getElementById("loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = document.getElementById("username").value.trim();
    const errorDiv = document.getElementById("error");
    errorDiv.textContent = "";

    if (!username) {
        errorDiv.textContent = "ユーザー名を入力してください";
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
            challenge: challengeBuffer,  // challenge は正しく変換されたものを使用
            rp: options.rp,  // rp が存在するか確認
            user: {
                id: await base64ToArrayBuffer(options.user.id),  // user.id を変換
                name: options.user.name,
                displayName: options.user.displayName
            },
            pubKeyCredParams: options.pubKeyCredParams,
            authenticatorSelection: options.authenticatorSelection,
            timeout: options.timeout,
            attestation: options.attestation
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
            alert("ログイン成功！");
            window.location.href = "/";
        } else {
            errorDiv.textContent = "ログイン失敗: " + (result.error || "不明なエラー");
        }

    } catch (err) {
        console.error(err);
        errorDiv.textContent = "エラーが発生しました: " + err;
    }
});
