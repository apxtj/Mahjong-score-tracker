// Passkey 登録の共通処理（/register_start → navigator.credentials.create → /register_finish）
(function () {
    function base64urlToArrayBuffer(base64url) {
        const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/');
        const binary = atob(base64 + '='.repeat((4 - base64.length % 4) % 4));
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        return bytes.buffer;
    }

    function arrayBufferToBase64url(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
        return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
    }

    // startBody: /register_start に送る JSON。成功時は {status: "ok"}、失敗時は {error: "..."} を返す
    async function registerPasskey(startBody) {
        if (!window.PublicKeyCredential) {
            return { error: "このブラウザはPasskeyに対応していません。" };
        }

        const startResp = await fetch("/register_start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(startBody || {})
        });
        const options = await startResp.json();
        if (options.error) return { error: options.error };

        let cred;
        try {
            cred = await navigator.credentials.create({
                publicKey: {
                    challenge: base64urlToArrayBuffer(options.challenge),
                    rp: options.rp,
                    user: {
                        id: base64urlToArrayBuffer(options.user.id),
                        name: options.user.name,
                        displayName: options.user.displayName,
                    },
                    pubKeyCredParams: options.pubKeyCredParams,
                    authenticatorSelection: options.authenticatorSelection,
                    attestation: options.attestation,
                    timeout: options.timeout,
                }
            });
        } catch (err) {
            if (err && err.name === "NotAllowedError") return { error: "登録がキャンセルされました。" };
            if (err && err.name === "InvalidStateError") return { error: "この端末にはすでに登録されています。" };
            return { error: String(err) };
        }

        const finishResp = await fetch("/register_finish", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                id: cred.id,
                rawId: arrayBufferToBase64url(cred.rawId),
                attestationObject: arrayBufferToBase64url(cred.response.attestationObject),
                clientDataJSON: arrayBufferToBase64url(cred.response.clientDataJSON)
            })
        });
        return finishResp.json().catch(() => ({ error: "登録に失敗しました。" }));
    }

    window.registerPasskey = registerPasskey;
})();
