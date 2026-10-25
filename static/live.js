// ライブ卓: 点数表示（2秒ごとに最新状態を取得）と、記録係の操作
document.addEventListener('DOMContentLoaded', function () {
    const app = document.getElementById('liveApp');
    if (!app) return;

    const urls = app.dataset;
    const tableId = urls.tableId;
    const POSITIONS = ['bottom', 'right', 'top', 'left']; // 下の人から見て 右=下家, 上=対面, 左=上家
    const HAN_OPTIONS = [
        [1, '1翻'], [2, '2翻'], [3, '3翻'], [4, '4翻'],
        [5, '満貫'], [6, '跳満'], [8, '倍満'], [11, '三倍満'], [13, '役満'],
    ];
    // 翻ごとの「符と点数」の候補（サーバーの点数計算から作られたもの）
    const POINTS_TABLE = JSON.parse(urls.pointsTable || '{}');

    let current = null;          // 最新の表示データ（サーバーの public_state）
    let pendingAction = null;    // 確定待ちの操作（モーダル）
    const modal = new bootstrap.Modal(document.getElementById('actionModal'));
    const storageKey = `live_view_${tableId}`;

    // ---------------- 表示設定（端末ごとに保存） ----------------
    function loadViewSettings() {
        try {
            return JSON.parse(localStorage.getItem(storageKey)) || {};
        } catch (e) {
            return {};
        }
    }
    function saveViewSettings(settings) {
        try { localStorage.setItem(storageKey, JSON.stringify(settings)); } catch (e) { /* 保存できなくても表示は続ける */ }
    }
    const viewSettings = loadViewSettings();
    const viewSeatSelect = document.getElementById('viewSeat');
    const tableModeInput = document.getElementById('tableMode');
    tableModeInput.checked = !!viewSettings.tableMode;
    document.getElementById('board').classList.toggle('table-mode', tableModeInput.checked);

    tableModeInput.addEventListener('change', () => {
        viewSettings.tableMode = tableModeInput.checked;
        saveViewSettings(viewSettings);
        document.getElementById('board').classList.toggle('table-mode', tableModeInput.checked);
    });
    viewSeatSelect.addEventListener('change', () => {
        viewSettings.seat = Number(viewSeatSelect.value);
        saveViewSettings(viewSettings);
        render();
    });

    // ---------------- 通信 ----------------
    async function postJson(url, body) {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
        const data = await response.json().catch(() => ({ error: '通信に失敗しました。' }));
        if (!response.ok && !data.error) data.error = '操作に失敗しました。';
        return data;
    }

    async function poll() {
        if (document.hidden) return;
        try {
            const version = current ? current.version : '';
            const response = await fetch(`${urls.stateUrl}?version=${version}`);
            if (response.status === 404) {
                window.location.href = urls.indexUrl;
                return;
            }
            const data = await response.json();
            if (!data.unchanged && !data.error) {
                current = data;
                render();
            }
        } catch (e) {
            // 電波が悪いときは次回に再試行
        }
    }

    // ---------------- 表示 ----------------
    function formatPoints(value) {
        return value.toLocaleString();
    }
    function formatDelta(value) {
        if (!value) return '';
        return (value > 0 ? '+' : '') + value.toLocaleString();
    }
    function stickIcon(className) {
        const stick = document.createElement('span');
        stick.className = `stick ${className}`;
        stick.setAttribute('aria-hidden', 'true');
        return stick;
    }
    function names() {
        return current.state.players.map(p => p.name);
    }

    function render() {
        if (!current) return;
        const state = current.state;
        const playerNames = names();
        const lastDeltas = state.log.length ? state.log[0].deltas : [0, 0, 0, 0];

        // 「下に表示」の選択肢（初回のみ作る）
        if (!viewSeatSelect.options.length) {
            playerNames.forEach((name, seat) => viewSeatSelect.add(new Option(name, seat)));
        }
        const bottomSeat = Number.isInteger(viewSettings.seat) ? viewSettings.seat : 0;
        viewSeatSelect.value = String(bottomSeat);

        POSITIONS.forEach((pos, offset) => {
            const seat = (bottomSeat + offset) % 4;
            const panel = document.querySelector(`.seat-panel[data-pos="${pos}"]`);
            const score = state.scores[seat];
            panel.classList.toggle('dealer', seat === current.dealer);
            panel.querySelector('.wind').textContent = current.winds[seat] + (seat === current.dealer ? '（親）' : '');
            panel.querySelector('.name').textContent = playerNames[seat];
            const scoreEl = panel.querySelector('.score');
            scoreEl.textContent = formatPoints(score);
            scoreEl.classList.toggle('negative', score < 0);
            // 下に表示している人との点差（本人の席には出さない）
            const gapEl = panel.querySelector('.gap');
            if (seat === bottomSeat) {
                gapEl.replaceChildren();
                gapEl.className = 'gap';
            } else {
                const gap = score - state.scores[bottomSeat];
                const label = document.createElement('span');
                label.className = 'gap-label';
                label.textContent = '差';
                gapEl.replaceChildren(label, gap === 0 ? '±0' : formatDelta(gap));
                gapEl.className = 'gap ' + (gap > 0 ? 'ahead' : gap < 0 ? 'behind' : 'even');
            }

            const deltaEl = panel.querySelector('.delta');
            deltaEl.textContent = formatDelta(lastDeltas[seat]);
            deltaEl.className = 'delta' + (lastDeltas[seat] > 0 ? ' plus' : lastDeltas[seat] < 0 ? ' minus' : '');

            const riichiArea = panel.querySelector('.riichi-area');
            riichiArea.replaceChildren();
            if (state.riichi[seat]) {
                const badge = document.createElement('span');
                badge.className = 'riichi-badge';
                badge.textContent = 'リーチ';
                riichiArea.appendChild(badge);
            } else if (current.can_operate && !state.finished) {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'btn btn-outline-light riichi-button';
                button.textContent = 'リーチ';
                button.addEventListener('click', () => sendAction({ type: 'riichi', seat }));
                riichiArea.appendChild(button);
            }
        });

        document.getElementById('roundLabel').textContent = state.finished ? '終局' : current.round_label;
        // 本場は100点棒、供託は1000点棒のアイコン付きで表示
        document.getElementById('honbaLabel').replaceChildren(stickIcon('stick-100'), `${state.honba}本場`);
        document.getElementById('kyotakuLabel').replaceChildren(stickIcon('stick-1000'), `供託 ${formatPoints(state.kyotaku)}`);
        document.getElementById('operatorLabel').textContent = `記録係: ${current.operator_name}`;

        document.getElementById('operatorTools').classList.toggle('d-none', !current.can_operate);
        document.getElementById('viewerTools').classList.toggle('d-none', current.can_operate);
        document.getElementById('closeButton').classList.toggle('d-none', !current.can_operate);
        document.getElementById('undoButton').disabled = !current.can_undo;
        document.querySelectorAll('#operatorTools [data-open], #finishButton').forEach(button => {
            button.disabled = state.finished;
        });

        // 終了時は順位と「戦績入力へ」
        const banner = document.getElementById('finishedBanner');
        banner.classList.toggle('d-none', !state.finished);
        if (state.finished) {
            const ranking = [0, 1, 2, 3].sort((a, b) => state.scores[b] - state.scores[a] || a - b);
            document.getElementById('finalRanking').textContent = ranking
                .map((seat, i) => `${i + 1}位 ${playerNames[seat]} ${formatPoints(state.scores[seat])}`)
                .join(' / ');
            document.getElementById('toInputButton').href = `${urls.inputUrl}?live=${tableId}`;
        }

        if (focusActive) requestAnimationFrame(fitBoard);

        // 履歴
        document.getElementById('lastLog').textContent = state.log.length ? state.log[0].message : '-';
        const logList = document.getElementById('logList');
        logList.replaceChildren();
        state.log.forEach(entry => {
            const item = document.createElement('li');
            const changes = entry.deltas
                .map((delta, seat) => (delta ? `${playerNames[seat]} ${formatDelta(delta)}` : null))
                .filter(Boolean)
                .join('、');
            item.textContent = entry.message + (changes ? `（${changes}）` : '');
            logList.appendChild(item);
        });
    }

    // ---------------- 操作 ----------------
    function showError(message) {
        document.getElementById('actionError').textContent = message || '';
    }

    async function sendAction(action) {
        showError('');
        const data = await postJson(urls.actionUrl, action);
        if (data.error) {
            showError(data.error);
            return false;
        }
        current = data;
        render();
        return true;
    }

    document.getElementById('undoButton').addEventListener('click', async () => {
        showError('');
        const data = await postJson(urls.undoUrl);
        if (data.error) { showError(data.error); return; }
        current = data;
        render();
    });

    document.getElementById('finishButton').addEventListener('click', () => {
        if (confirm('対局を終了しますか？（残っている供託はトップに入ります。取り消しで戻せます）')) {
            sendAction({ type: 'finish' });
        }
    });

    document.getElementById('takeoverButton').addEventListener('click', async () => {
        if (!confirm('記録係を自分に替えますか？')) return;
        const data = await postJson(urls.takeoverUrl);
        if (data.error) { showError(data.error); return; }
        current = data;
        render();
    });

    document.getElementById('closeButton').addEventListener('click', async () => {
        if (!confirm('この卓を片付けますか？（一覧から消えます。保存済みの戦績には影響しません）')) return;
        const data = await postJson(urls.closeUrl);
        if (data.error) { showError(data.error); return; }
        window.location.href = urls.indexUrl;
    });

    // ---------------- 入力モーダル ----------------
    const modalBody = document.getElementById('actionModalBody');

    function seatSelect(name, label, selected) {
        const options = names()
            .map((n, seat) => `<option value="${seat}" ${seat === selected ? 'selected' : ''}>${escapeHtml(n)}</option>`)
            .join('');
        return `<label class="form-label small mb-1">${label}</label>
                <select class="form-select mb-2" name="${name}"><option value="">選択</option>${options}</select>`;
    }

    function handInputs(tsumo) {
        const hanOptions = HAN_OPTIONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join('');
        const direct = tsumo
            ? `<div class="direct-dealer-win d-none">
                 <label class="form-label small mb-1">1人あたりの支払い（オール）</label>
                 <input type="number" class="form-control mb-2" name="points_each" inputmode="numeric" placeholder="例: 4000">
               </div>
               <div class="direct-child-win">
                 <label class="form-label small mb-1">子の支払い / 親の支払い</label>
                 <div class="d-flex gap-1 mb-2">
                   <input type="number" class="form-control" name="points_child" inputmode="numeric" placeholder="子 例: 2000">
                   <input type="number" class="form-control" name="points_dealer" inputmode="numeric" placeholder="親 例: 4000">
                 </div>
               </div>`
            : `<label class="form-label small mb-1">点数</label>
               <input type="number" class="form-control mb-2" name="points" inputmode="numeric" placeholder="例: 7700">`;
        return `
            <div class="btn-group w-100 mb-2" role="group">
              <input type="radio" class="btn-check" name="mode" id="modeHan" value="han" checked>
              <label class="btn btn-outline-primary btn-sm" for="modeHan">翻・符で計算</label>
              <input type="radio" class="btn-check" name="mode" id="modeDirect" value="direct">
              <label class="btn btn-outline-primary btn-sm" for="modeDirect">点数を直接入力</label>
            </div>
            <div class="mode-han d-flex gap-1 mb-2">
              <select class="form-select" name="han" style="flex: 0 0 6.5em;" aria-label="翻数">${hanOptions}</select>
              <select class="form-select" name="fu" style="flex: 1 1 auto;" aria-label="符と点数"></select>
            </div>
            <div class="mode-direct d-none">${direct}</div>`;
    }

    const FORMS = {
        ron: { title: 'ロン', html: () => seatSelect('winner', '和了した人') + seatSelect('loser', '放銃した人') + handInputs(false) },
        tsumo: { title: 'ツモ', html: () => seatSelect('winner', '和了した人') + handInputs(true) },
        draw: {
            title: '流局',
            html: () => '<div class="small mb-1">テンパイの人を選んでください</div>' + names()
                .map((n, seat) => `<div class="form-check"><input class="form-check-input" type="checkbox" name="tenpai" value="${seat}" id="tenpai${seat}">
                    <label class="form-check-label" for="tenpai${seat}">${escapeHtml(n)}</label></div>`)
                .join(''),
        },
        transfer: {
            title: '点棒移動',
            html: () => seatSelect('from', '支払う人') + seatSelect('to', '受け取る人')
                + '<label class="form-label small mb-1">点数</label><input type="number" class="form-control" name="amount" inputmode="numeric" placeholder="例: 1000">',
        },
    };

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function readForm(kind) {
        const value = name => {
            const el = modalBody.querySelector(`[name="${name}"]`);
            return el ? el.value : '';
        };
        const action = { type: kind };
        if (kind === 'draw') {
            action.tenpai = Array.from(modalBody.querySelectorAll('[name="tenpai"]:checked')).map(el => Number(el.value));
            return action;
        }
        if (kind === 'transfer') {
            return Object.assign(action, { from: value('from'), to: value('to'), amount: value('amount') });
        }
        action.winner = value('winner');
        if (kind === 'ron') action.loser = value('loser');
        const direct = modalBody.querySelector('[name="mode"]:checked').value === 'direct';
        if (!direct) {
            action.han = value('han');
            action.fu = value('fu');
        } else if (kind === 'ron') {
            action.points = value('points');
        } else if (Number(action.winner) === current.dealer) {
            action.points_each = value('points_each');
        } else {
            action.points_child = value('points_child');
            action.points_dealer = value('points_dealer');
        }
        return action;
    }

    function syncFormVisibility() {
        const modeInput = modalBody.querySelector('[name="mode"]:checked');
        if (modeInput) {
            const direct = modeInput.value === 'direct';
            modalBody.querySelector('.mode-han').classList.toggle('d-none', direct);
            modalBody.querySelector('.mode-direct').classList.toggle('d-none', !direct);
            refreshPointOptions();
        }
        const dealerWin = modalBody.querySelector('.direct-dealer-win');
        if (dealerWin) {
            const winner = modalBody.querySelector('[name="winner"]').value;
            const isDealer = winner !== '' && Number(winner) === current.dealer;
            dealerWin.classList.toggle('d-none', !isDealer);
            modalBody.querySelector('.direct-child-win').classList.toggle('d-none', isDealer);
        }
    }

    // 翻数・和了者（親か子か）・ロン/ツモに合わせて、「符と点数」の候補を作り直す
    function refreshPointOptions() {
        const hanSelect = modalBody.querySelector('[name="han"]');
        const fuSelect = modalBody.querySelector('[name="fu"]');
        if (!hanSelect || !fuSelect) return;
        const winner = modalBody.querySelector('[name="winner"]').value;
        const who = winner !== '' && Number(winner) === current.dealer ? 'dealer' : 'child';
        const kind = pendingAction === 'tsumo' ? 'tsumo' : 'ron';
        const key = `${kind}:${who}:${hanSelect.value}`;
        if (fuSelect.dataset.key === key) return;

        const candidates = ((POINTS_TABLE[kind] || {})[who] || {})[hanSelect.value] || [];
        const previous = fuSelect.value;
        fuSelect.replaceChildren(...candidates.map(c => new Option(c.label, c.fu)));
        // 前に選んでいた符があればそのまま、なければ30符（なければ先頭）
        const values = candidates.map(c => String(c.fu));
        fuSelect.value = values.includes(previous) ? previous : (values.includes('30') ? '30' : values[0] || '');
        fuSelect.dataset.key = key;
    }

    // 入力のたびに、確定した場合の点数の動きをサーバーで計算して表示する
    let previewTimer = null;
    function schedulePreview() {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(updatePreview, 150);
    }
    async function updatePreview() {
        const previewEl = document.getElementById('actionPreview');
        const errorEl = document.getElementById('actionModalError');
        const action = readForm(pendingAction);
        action.dry_run = true;
        const data = await postJson(urls.actionUrl, action);
        if (data.error) {
            previewEl.textContent = '';
            errorEl.textContent = data.error;
            document.getElementById('actionConfirm').disabled = true;
            return;
        }
        errorEl.textContent = '';
        const playerNames = names();
        previewEl.textContent = data.deltas
            .map((delta, seat) => (delta ? `${playerNames[seat]} ${formatDelta(delta)}` : null))
            .filter(Boolean)
            .join(' / ') || '点数の移動はありません';
        document.getElementById('actionConfirm').disabled = false;
    }

    document.querySelectorAll('[data-open]').forEach(button => {
        button.addEventListener('click', () => {
            pendingAction = button.dataset.open;
            const form = FORMS[pendingAction];
            document.getElementById('actionModalTitle').textContent = form.title;
            modalBody.innerHTML = form.html();
            document.getElementById('actionPreview').textContent = '';
            document.getElementById('actionModalError').textContent = '';
            document.getElementById('actionConfirm').disabled = true;
            syncFormVisibility();
            if (pendingAction === 'draw') updatePreview();
            modal.show();
        });
    });

    modalBody.addEventListener('input', () => { syncFormVisibility(); schedulePreview(); });
    modalBody.addEventListener('change', () => { syncFormVisibility(); schedulePreview(); });

    document.getElementById('actionConfirm').addEventListener('click', async () => {
        const action = readForm(pendingAction);
        const data = await postJson(urls.actionUrl, action);
        if (data.error) {
            document.getElementById('actionModalError').textContent = data.error;
            return;
        }
        current = data;
        render();
        modal.hide();
    });

    // ---------------- 全画面（集中）表示 ----------------
    // 卓と操作ボタンだけを固定表示し、スクロール・引っ張って更新・画面スリープを止める。
    // 端末の「戻る」操作はページ移動ではなく全画面の解除にする。
    let focusActive = false;
    let wakeLock = null;

    async function requestWakeLock() {
        if (!('wakeLock' in navigator)) return;
        try {
            wakeLock = await navigator.wakeLock.request('screen');
        } catch (e) {
            wakeLock = null; // 省電力モードなどで拒否されても表示は続ける
        }
    }

    function releaseWakeLock() {
        if (wakeLock) {
            wakeLock.release().catch(() => {});
            wakeLock = null;
        }
    }

    // 全画面のとき、卓全体が画面に収まる倍率で拡大縮小する（横画面はボタンを左右に逃がした残りの幅）
    function fitBoard() {
        const board = document.getElementById('board');
        board.style.transform = '';
        board.style.margin = '';
        if (!focusActive) return;

        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;
        const boardWidth = board.offsetWidth;
        const boardHeight = board.offsetHeight;
        if (!boardWidth || !boardHeight) return;

        let availableWidth;
        let availableHeight;
        if (viewportWidth > viewportHeight) {
            const toolGroup = document.querySelector('#operatorTools:not(.d-none) .tool-group');
            const sideWidth = toolGroup ? toolGroup.offsetWidth + 16 : 16;
            availableWidth = viewportWidth - sideWidth * 2 - 16;
            availableHeight = viewportHeight - 16;
        } else {
            // 縦画面: 卓以外（ボタン・下に表示・終了の案内など）の高さと「✕ 戻る」の分を差し引く
            let reserved = 44 + 16;
            ['finishedBanner', 'operatorTools', 'viewerTools', 'actionError', 'viewOptions'].forEach(id => {
                const el = document.getElementById(id);
                if (el && el.offsetHeight) reserved += el.offsetHeight + 8;
            });
            availableWidth = viewportWidth - 16;
            availableHeight = viewportHeight - reserved;
        }

        const scale = Math.max(0.4, Math.min(availableWidth / boardWidth, availableHeight / boardHeight, 1.5));
        board.style.transform = `scale(${scale})`;
        // transform は場所を取る大きさを変えないので、余白で見た目の大きさに合わせる
        board.style.margin = `${(scale - 1) * boardHeight / 2}px ${(scale - 1) * boardWidth / 2}px`;
    }

    function enterFocus() {
        if (focusActive) return;
        focusActive = true;
        document.documentElement.classList.add('live-focus');
        document.body.classList.add('live-focus');
        const root = document.documentElement;
        if (root.requestFullscreen) {
            root.requestFullscreen({ navigationUI: 'hide' }).catch(() => {}); // iPhone の Safari は非対応
        }
        requestWakeLock();
        history.pushState({ liveFocus: true }, '');
        requestAnimationFrame(fitBoard);
    }

    function exitFocus(fromHistory) {
        if (!focusActive) return;
        focusActive = false;
        document.documentElement.classList.remove('live-focus');
        document.body.classList.remove('live-focus');
        if (document.fullscreenElement && document.exitFullscreen) {
            document.exitFullscreen().catch(() => {});
        }
        releaseWakeLock();
        fitBoard();
        // 「✕ 戻る」で抜けたときは、入るときに積んだ履歴を戻しておく
        if (!fromHistory && history.state && history.state.liveFocus) {
            history.back();
        }
    }

    document.getElementById('focusButton').addEventListener('click', enterFocus);
    document.getElementById('focusExitButton').addEventListener('click', () => exitFocus(false));
    window.addEventListener('popstate', () => exitFocus(true));
    document.addEventListener('fullscreenchange', () => {
        // Android の戻るジェスチャーなどで全画面が解除されたら、表示も元に戻す
        if (!document.fullscreenElement && focusActive) exitFocus(false);
    });
    // 回転・全画面への切り替え・アドレスバーの出入りで画面の大きさが変わったら合わせ直す
    window.addEventListener('resize', () => requestAnimationFrame(fitBoard));
    window.addEventListener('orientationchange', () => setTimeout(fitBoard, 300));

    // 画面を切り替えるとスリープ防止が外れるので、戻ってきたら取り直す
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && focusActive) requestWakeLock();
    });

    // ---------------- 開始 ----------------
    poll();
    setInterval(poll, 2000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
});
