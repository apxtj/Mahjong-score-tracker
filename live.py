"""ライブ卓: 場決めと、点棒の代わりに点数をやり取りする対局中の点数表示

記録係の1人が操作し、他の人は閲覧画面で現在の点数をリアルタイム（ポーリング）で見る。
既存の戦績入力とは独立していて、終了後に戦績入力へ点数を引き継げる。
"""
import copy
import json
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from models import LiveTable, Player, User, db

bp = Blueprint("live", __name__, url_prefix="/live")

START_POINTS = 25000
HONBA_POINTS = 300          # 1本場あたり（ロンは放銃者が300、ツモは各自100ずつ）
RIICHI_STICK = 1000
NOTEN_PENALTY_TOTAL = 3000
HISTORY_LIMIT = 100
LOG_LIMIT = 30
VALID_FU = {20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 110}
SEAT_WINDS = "東南西北"


class LiveError(ValueError):
    """操作できない入力（画面にそのまま表示する）"""


# ---------------------------------------------------------------------------
# 点数計算（純粋関数）
# ---------------------------------------------------------------------------

def _ceil100(value):
    return -(-value // 100) * 100


def hand_base(han, fu):
    """基本点。5翻以上（または4翻40符・3翻70符など基本点2000超）は満貫以上として扱う（切り上げ満貫なし）"""
    if han >= 13:
        return 8000
    if han >= 11:
        return 6000
    if han >= 8:
        return 4000
    if han >= 6:
        return 3000
    if han >= 5:
        return 2000
    return min(fu * 2 ** (han + 2), 2000)


def hand_points(han, fu, dealer):
    """和了点（本場・供託を含まない）。
    dealer=True: {"ron": 親のロン, "tsumo_each": 子それぞれの支払い}
    dealer=False: {"ron": 子のロン, "tsumo_dealer": 親の支払い, "tsumo_child": 子の支払い}
    """
    if not 1 <= han:
        raise LiveError("翻数は1以上で指定してください。")
    if han < 5 and fu not in VALID_FU:
        raise LiveError("符は20〜110（25符を含む）から選んでください。")
    base = hand_base(han, fu)
    if dealer:
        return {"ron": _ceil100(base * 6), "tsumo_each": _ceil100(base * 2)}
    return {"ron": _ceil100(base * 4), "tsumo_dealer": _ceil100(base * 2), "tsumo_child": _ceil100(base)}


def round_label(round_index):
    """0 → 東1局, 4 → 南1局 ..."""
    return f"{SEAT_WINDS[(round_index // 4) % 4]}{round_index % 4 + 1}局"


# ---------------------------------------------------------------------------
# 卓の状態と操作（純粋関数）
# ---------------------------------------------------------------------------

def new_state(players):
    """players: 席順（起家から反時計回り = 東南西北）の [{"id", "name"}]"""
    return {
        "players": players,
        "scores": [START_POINTS] * 4,
        "round": 0,
        "honba": 0,
        "kyotaku": 0,
        "riichi": [False] * 4,
        "finished": False,
        "log": [],
    }


def dealer_seat(state):
    return state["round"] % 4


def seat_winds(state):
    """各席の現在の自風（親が東）"""
    dealer = dealer_seat(state)
    return [SEAT_WINDS[(seat - dealer) % 4] for seat in range(4)]


def _seat(value, label):
    try:
        seat = int(value)
    except (TypeError, ValueError):
        raise LiveError(f"{label}を選んでください。")
    if not 0 <= seat <= 3:
        raise LiveError(f"{label}を選んでください。")
    return seat


def _points(value, label):
    try:
        points = int(value)
    except (TypeError, ValueError):
        raise LiveError(f"{label}を数字で入力してください。")
    if points <= 0 or points % 100:
        raise LiveError(f"{label}は100点単位の正の数で入力してください。")
    return points


def _han_fu(action):
    try:
        han = int(action.get("han"))
        fu = int(action.get("fu") or 30)
    except (TypeError, ValueError):
        raise LiveError("翻数と符を選んでください。")
    return han, fu


def _end_hand(state, dealer_continues):
    """和了での局の終了: リーチをリセットし、親の和了なら連荘（1本場積む）、子の和了なら次局（0本場）へ"""
    state["riichi"] = [False] * 4
    if dealer_continues:
        state["honba"] += 1
    else:
        state["round"] += 1
        state["honba"] = 0


def apply_action(state, action):
    """操作を適用した新しい state と、各席の点数の増減・ログ文を返す（state は書き換えない）"""
    if state.get("finished"):
        raise LiveError("この卓は終了しています。取り消しで終了前に戻せます。")

    state = copy.deepcopy(state)
    names = [p["name"] for p in state["players"]]
    deltas = [0, 0, 0, 0]
    kind = action.get("type")
    label = f"{round_label(state['round'])} {state['honba']}本場"
    dealer = dealer_seat(state)

    if kind == "riichi":
        seat = _seat(action.get("seat"), "リーチした人")
        if state["riichi"][seat]:
            raise LiveError(f"{names[seat]} はすでにリーチしています。")
        deltas[seat] = -RIICHI_STICK
        state["riichi"][seat] = True
        state["kyotaku"] += RIICHI_STICK
        message = f"{label} {names[seat]} リーチ"

    elif kind == "ron":
        winner = _seat(action.get("winner"), "和了した人")
        loser = _seat(action.get("loser"), "放銃した人")
        if winner == loser:
            raise LiveError("和了した人と放銃した人が同じです。")
        if action.get("points"):
            points = _points(action.get("points"), "点数")
            detail = f"{points:,}点"
        else:
            han, fu = _han_fu(action)
            points = hand_points(han, fu, winner == dealer)["ron"]
            detail = f"{_hand_name(han, fu)} {points:,}点"
        pay = points + HONBA_POINTS * state["honba"]
        deltas[loser] -= pay
        deltas[winner] += pay + state["kyotaku"]
        message = f"{label} ロン {names[loser]} → {names[winner]} {detail}"
        state["kyotaku"] = 0
        _end_hand(state, dealer_continues=(winner == dealer))

    elif kind == "tsumo":
        winner = _seat(action.get("winner"), "和了した人")
        honba_each = HONBA_POINTS // 3 * state["honba"]
        if winner == dealer:
            if action.get("points_each"):
                each = _points(action.get("points_each"), "支払い")
                detail = f"{each:,}オール"
            else:
                han, fu = _han_fu(action)
                each = hand_points(han, fu, True)["tsumo_each"]
                detail = f"{_hand_name(han, fu)} {each:,}オール"
            payments = {seat: each for seat in range(4) if seat != winner}
        else:
            if action.get("points_child") or action.get("points_dealer"):
                child = _points(action.get("points_child"), "子の支払い")
                dealer_pay = _points(action.get("points_dealer"), "親の支払い")
            else:
                han, fu = _han_fu(action)
                result = hand_points(han, fu, False)
                child, dealer_pay = result["tsumo_child"], result["tsumo_dealer"]
            detail = f"{child:,}・{dealer_pay:,}"
            if not (action.get("points_child") or action.get("points_dealer")):
                detail = f"{_hand_name(han, fu)} {detail}"
            payments = {seat: (dealer_pay if seat == dealer else child) for seat in range(4) if seat != winner}
        for seat, amount in payments.items():
            deltas[seat] -= amount + honba_each
            deltas[winner] += amount + honba_each
        deltas[winner] += state["kyotaku"]
        message = f"{label} ツモ {names[winner]} {detail}"
        state["kyotaku"] = 0
        _end_hand(state, dealer_continues=(winner == dealer))

    elif kind == "draw":
        tenpai = sorted({_seat(seat, "テンパイの人") for seat in action.get("tenpai", [])})
        noten = [seat for seat in range(4) if seat not in tenpai]
        if tenpai and noten:
            for seat in tenpai:
                deltas[seat] += NOTEN_PENALTY_TOTAL // len(tenpai)
            for seat in noten:
                deltas[seat] -= NOTEN_PENALTY_TOTAL // len(noten)
        tenpai_names = "・".join(names[seat] for seat in tenpai) or "なし"
        message = f"{label} 流局（テンパイ: {tenpai_names}）"
        state["riichi"] = [False] * 4
        state["honba"] += 1
        if dealer not in tenpai:
            state["round"] += 1

    elif kind == "transfer":
        source = _seat(action.get("from"), "支払う人")
        target = _seat(action.get("to"), "受け取る人")
        if source == target:
            raise LiveError("支払う人と受け取る人が同じです。")
        amount = _points(action.get("amount"), "点数")
        deltas[source] -= amount
        deltas[target] += amount
        message = f"点棒移動 {names[source]} → {names[target]} {amount:,}点"

    elif kind == "finish":
        # 残った供託はトップへ（同点は起家に近い人 = 席順の早い人）
        top = max(range(4), key=lambda seat: (state["scores"][seat], -seat))
        deltas[top] += state["kyotaku"]
        state["kyotaku"] = 0
        state["finished"] = True
        message = "対局終了"

    else:
        raise LiveError("不明な操作です。")

    state["scores"] = [score + delta for score, delta in zip(state["scores"], deltas)]
    state["log"] = ([{"message": message, "deltas": deltas}] + state["log"])[:LOG_LIMIT]
    return state, deltas, message


def _hand_name(han, fu):
    if han >= 13:
        return "役満"
    if han >= 11:
        return "三倍満"
    if han >= 8:
        return "倍満"
    if han >= 6:
        return "跳満"
    if han >= 5 or hand_base(han, fu) >= 2000:
        return "満貫"
    return f"{fu}符{han}翻"


def public_state(table, user):
    """画面に渡す形（表示用のラベルと、この人が操作できるか）"""
    state = json.loads(table.state)
    return {
        "id": table.id,
        "version": table.version,
        "status": table.status,
        "state": state,
        "round_label": round_label(state["round"]),
        "dealer": dealer_seat(state),
        "winds": seat_winds(state),
        "can_operate": bool(user and user.id == table.operator_id and table.status != "closed"),
        "operator_name": table.operator.username if table.operator else "",
        "can_undo": len(json.loads(table.history or "[]")) > 0,
    }


# ---------------------------------------------------------------------------
# 画面・API
# ---------------------------------------------------------------------------

def _current_user():
    user_id = session.get("login_user_id")
    return db.session.get(User, user_id) if user_id else None


def _login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _current_user():
            if request.method == "GET" and not request.path.endswith("/state"):
                return redirect(url_for("auth.login_page", next=request.path))
            return jsonify({"error": "ログインしてください。"}), 401
        return view(*args, **kwargs)
    return wrapper


def _get_table(table_id):
    table = db.session.get(LiveTable, table_id)
    if not table or table.status == "closed":
        return None
    return table


def _operator_table(table_id):
    """操作できる卓を返す。できなければ (None, エラーレスポンス)"""
    table = _get_table(table_id)
    if not table:
        return None, (jsonify({"error": "この卓は見つかりません。"}), 404)
    user = _current_user()
    if user.id != table.operator_id:
        return None, (jsonify({"error": "点数を操作できるのは記録係だけです。「操作を引き継ぐ」で記録係になれます。"}), 403)
    return table, None


def _save(table, state, push_history_from=None):
    if push_history_from is not None:
        history = json.loads(table.history or "[]")
        history.append(push_history_from)
        table.history = json.dumps(history[-HISTORY_LIMIT:], ensure_ascii=False)
    table.state = json.dumps(state, ensure_ascii=False)
    table.status = "finished" if state.get("finished") else "playing"
    table.version += 1
    table.updated_at = datetime.utcnow()
    db.session.commit()


@bp.route("/", methods=["GET"])
@_login_required
def live_index():
    """卓の一覧と、新しい卓を立てる（場決め）画面"""
    from app import _default_player_ids  # 循環importを避けるため遅延import

    tables = (LiveTable.query.filter(LiveTable.status != "closed")
              .order_by(LiveTable.updated_at.desc()).limit(10).all())
    table_rows = []
    for table in tables:
        state = json.loads(table.state)
        table_rows.append({
            "id": table.id,
            "status": table.status,
            "names": [p["name"] for p in state["players"]],
            "round_label": round_label(state["round"]),
            "updated_at": table.updated_at,
        })
    return render_template(
        "live_index.html",
        players=Player.query.order_by(Player.id).all(),
        default_player_ids=_default_player_ids(),
        tables=table_rows,
    )


@bp.route("/", methods=["POST"])
@_login_required
def live_create():
    """席順（東南西北）で4人を受け取り、卓を立てる"""
    try:
        player_ids = [int(request.form.get(f"seat{i}")) for i in range(4)]
    except (TypeError, ValueError):
        return redirect(url_for("live.live_index", error="4人を選んでください。"))
    if len(set(player_ids)) != 4:
        return redirect(url_for("live.live_index", error="同じプレイヤーが複数選ばれています。"))
    players = {p.id: p for p in Player.query.filter(Player.id.in_(player_ids)).all()}
    if len(players) != 4:
        return redirect(url_for("live.live_index", error="選ばれたプレイヤーが見つかりません。"))

    user = _current_user()
    state = new_state([{"id": pid, "name": players[pid].name} for pid in player_ids])
    table = LiveTable(
        created_by_id=user.id,
        operator_id=user.id,
        state=json.dumps(state, ensure_ascii=False),
    )
    db.session.add(table)
    db.session.commit()
    return redirect(url_for("live.live_table", table_id=table.id))


@bp.route("/<int:table_id>")
@_login_required
def live_table(table_id):
    table = _get_table(table_id)
    if not table:
        return redirect(url_for("live.live_index", error="この卓は見つかりません。"))
    return render_template("live_table.html", table_id=table.id)


@bp.route("/<int:table_id>/state")
@_login_required
def live_state(table_id):
    table = _get_table(table_id)
    if not table:
        return jsonify({"error": "この卓は見つかりません。"}), 404
    # 変更がなければ中身を返さない（2秒ごとの確認を軽くする）
    if request.args.get("version", type=int) == table.version:
        return jsonify({"unchanged": True, "version": table.version})
    return jsonify(public_state(table, _current_user()))


@bp.route("/<int:table_id>/action", methods=["POST"])
@_login_required
def live_action(table_id):
    table, error = _operator_table(table_id)
    if error:
        return error
    action = request.get_json(silent=True) or {}
    current = json.loads(table.state)
    try:
        new, deltas, message = apply_action(current, action)
    except LiveError as live_error:
        return jsonify({"error": str(live_error)}), 400

    if action.get("dry_run"):
        # 確定前の確認表示用（保存しない）
        return jsonify({"deltas": deltas, "message": message})

    _save(table, new, push_history_from=current)
    return jsonify(public_state(table, _current_user()))


@bp.route("/<int:table_id>/undo", methods=["POST"])
@_login_required
def live_undo(table_id):
    table, error = _operator_table(table_id)
    if error:
        return error
    history = json.loads(table.history or "[]")
    if not history:
        return jsonify({"error": "取り消せる操作がありません。"}), 400
    previous = history.pop()
    table.history = json.dumps(history, ensure_ascii=False)
    _save(table, previous)
    return jsonify(public_state(table, _current_user()))


@bp.route("/<int:table_id>/takeover", methods=["POST"])
@_login_required
def live_takeover(table_id):
    """記録係を自分に替える（記録係のスマホの電池切れなどに備える）"""
    table = _get_table(table_id)
    if not table:
        return jsonify({"error": "この卓は見つかりません。"}), 404
    table.operator_id = _current_user().id
    table.version += 1
    db.session.commit()
    return jsonify(public_state(table, _current_user()))


@bp.route("/<int:table_id>/close", methods=["POST"])
@_login_required
def live_close(table_id):
    """卓を片付ける（一覧から消す）。戦績には影響しない"""
    table, error = _operator_table(table_id)
    if error:
        return error
    table.status = "closed"
    table.version += 1
    db.session.commit()
    return jsonify({"status": "closed"})


def finished_table_form_data(table_id):
    """終了した卓の点数を、戦績入力画面の初期値（席順・同点時は起家に近い順）にする"""
    table = db.session.get(LiveTable, table_id)
    if not table or table.status != "finished":
        return None
    state = json.loads(table.state)
    ids = [p["id"] for p in state["players"]]
    form_data = {"tie_order": ",".join(str(pid) for pid in ids)}
    for i, (pid, score) in enumerate(zip(ids, state["scores"]), start=1):
        form_data[f"player{i}_id"] = str(pid)
        form_data[f"score{i}"] = score
    return form_data
