from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import date, datetime, timedelta
from flask_migrate import Migrate
from sqlalchemy import func
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask import flash
from collections import defaultdict
from flask import request
import os
from functools import wraps
from flask import redirect, url_for
from models import (
    db, User, Player, Game, GamePhoto, Title, PlayerTitleAchievement, AppSetting,
    compute_all_player_stats, get_achieved_title_ids,
)
from sqlalchemy import inspect as sa_inspect, text
from werkzeug.utils import secure_filename
from uuid import uuid4

try:
    import cloudinary
    import cloudinary.uploader
except ImportError:
    cloudinary = None
    cloudinary_uploader = None

import logging

app = Flask(__name__)
# Secret key should come from environment in production
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

# Session cookie hardening for production
app.config.update(
    SESSION_COOKIE_SECURE=bool(os.environ.get("SESSION_COOKIE_SECURE", "True") == "True"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=os.environ.get("SESSION_COOKIE_SAMESITE", "Lax"),
    # ログイン状態の保持期間（アクセスするたびに延長される）
    PERMANENT_SESSION_LIFETIME=timedelta(days=int(os.environ.get("SESSION_LIFETIME_DAYS", "30"))),
)

# Configure basic logging
debug_mode = os.environ.get("FLASK_DEBUG", "False") == "True"
log_level = logging.DEBUG if debug_mode else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)

# Cloudinary設定（Renderの環境変数 CLOUDINARY_URL がある場合のみ有効化）
if cloudinary is not None and os.environ.get("CLOUDINARY_URL"):
    cloudinary.config(cloudinary_url=os.environ.get("CLOUDINARY_URL"))

# SQLiteデータベースの設定
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "sqlite:///mahjong.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# create_all は既存テーブルに列を足さないため、後から追加した列はここで補う
_ADDED_COLUMNS = {
    "user": [
        ("passkey_enrollment_token", "VARCHAR(128)"),
        ("passkey_enrollment_expires_at", "TIMESTAMP"),
        ("player_id", "INTEGER"),
        ("can_edit_games", "BOOLEAN DEFAULT FALSE"),
    ],
    "credential": [
        ("created_at", "TIMESTAMP"),
        ("last_used_at", "TIMESTAMP"),
    ],
}


def _ensure_added_columns():
    inspector = sa_inspect(db.engine)
    for table_name, columns in _ADDED_COLUMNS.items():
        if not inspector.has_table(table_name):
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, column_type in columns:
            if column_name in existing:
                continue
            try:
                db.session.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN {column_name} {column_type}'))
                db.session.commit()
                logger.info("Added column %s.%s", table_name, column_name)
            except Exception:
                # 複数ワーカーが同時に起動した場合など、他方が先に追加済みなら失敗してよい
                db.session.rollback()
                logger.exception("Failed to add column %s.%s", table_name, column_name)


with app.app_context():
    db.create_all()
    _ensure_added_columns()

migrate = Migrate(app, db)

from auth import bp as auth_bp
app.register_blueprint(auth_bp)

from live import bp as live_bp, finished_table_form_data
app.register_blueprint(live_bp)


@app.context_processor
def inject_current_player():
    """ナビバーの「マイページ」用のプレイヤーIDと、戦績の修正・削除ボタンを出すかを渡す"""
    user = _current_user()
    return {
        "my_player_id": user.player_id if user else None,
        "can_manage_games": can_manage_games(user),
    }


def _is_admin_user(user):
    return bool(user and session.get("login_is_admin")
                and user.username == os.environ.get("ADMIN_USERNAME", "admin"))


def can_manage_games(user=None):
    """戦績の修正・削除ができるか（管理者、または管理者から権限を付与されたユーザー）"""
    user = user or _current_user()
    if not user:
        return False
    return _is_admin_user(user) or bool(user.can_edit_games)


def game_manager_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _current_user():
            return redirect(url_for("auth.login_page", next=request.path))
        if not can_manage_games():
            return jsonify({"error": "戦績の修正・削除の権限がありません。管理者に依頼してください。"}), 403
        return f(*args, **kwargs)
    return wrapper


@app.template_filter("jst")
def format_jst(value):
    """DBのUTC日時を日本時間で表示する（記録がない場合は「不明」）"""
    if not value:
        return "不明"
    return (value + timedelta(hours=9)).strftime("%Y/%m/%d %H:%M")


def _current_user():
    user_id = session.get("login_user_id")
    return db.session.get(User, user_id) if user_id else None

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        logger.debug("login_user_id in session: %s", session.get("login_user_id"))
        if "login_user_id" not in session:
            logger.debug("Redirecting to login")
            return redirect(url_for("auth.login_page", next=request.path))
        return f(*args, **kwargs)
    return wrapper

def admin_required(f):
    """Passkey でログインした管理者だけを通す（パスワードによるログインは受け付けない）"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        current_user = _current_user()
        if not current_user:
            return redirect(url_for("auth.login_page", next=request.path))

        if not _is_admin_user(current_user):
            return jsonify({"error": "Admin access required"}), 403

        return f(*args, **kwargs)

    return wrapper

@app.route("/logout")
def logout():
    session.pop("login_user_id", None)
    session.pop("login_username", None)
    session.pop("login_is_admin", None)
    return redirect(url_for("auth.login_page"))

def recalc_total_scores():
    """全対局を1回だけ読み込んで、各プレイヤーの累計スコアを計算し直す（対局の削除時などに使う）"""
    totals = defaultdict(int)
    for g in Game.query.all():
        totals[g.player1_id] += g.score1 or 0
        totals[g.player2_id] += g.score2 or 0
        totals[g.player3_id] += g.score3 or 0
        totals[g.player4_id] += g.score4 or 0

    for player in Player.query.all():
        player.total_score = totals.get(player.id, 0)
    db.session.commit()


DEFAULT_OKA = 0
DEFAULT_UMA = "20000,-20000,-40000,-60000"
# 持ち点の入力単位（画面では百点単位で入力し、サーバー側で点数に戻す）
SCORE_INPUT_UNIT = 100


def rank_entries(entries, tie_order_ids):
    """点数の高い順に並べる。同点のときだけ tie_order_ids（起家に近い＝上位にする順のプレイヤーID）で決める。

    entries: [{"id": プレイヤーID, "score": 点数}, ...]
    同点のプレイヤーが tie_order_ids に含まれていない場合は ValueError。
    """
    scores = defaultdict(list)
    for entry in entries:
        scores[entry["score"]].append(entry["id"])
    for tied_ids in scores.values():
        if len(tied_ids) > 1 and not set(tied_ids) <= set(tie_order_ids):
            raise ValueError("同点のプレイヤーがいます。起家に近い人（上位にする人）を選んでください。")

    priority = {player_id: index for index, player_id in enumerate(tie_order_ids)}
    return sorted(entries, key=lambda e: (-e["score"], priority.get(e["id"], 0)))


def _parse_tie_order(value):
    try:
        return [int(v) for v in (value or "").split(",") if v.strip()]
    except ValueError:
        return []


def _today_jst():
    return (datetime.utcnow() + timedelta(hours=9)).date()


def _default_player_ids():
    """入力画面の初期プレイヤー: 今日の直前の対局の4人、なければログイン中の本人だけ"""
    last_game = Game.query.filter_by(date=_today_jst()).order_by(Game.id.desc()).first()
    if last_game:
        last_ids = [last_game.player1_id, last_game.player2_id, last_game.player3_id, last_game.player4_id]
        # 同じ端末で続けて入力しているなら、前回入力した並び順のまま出す（順位順に入れ替わらないように）
        remembered = session.get("last_player_order") or []
        if sorted(remembered) == sorted(last_ids):
            return remembered
        return last_ids

    current_user = _current_user()
    if current_user and current_user.player_id:
        return [current_user.player_id, None, None, None]
    return [None, None, None, None]


def _load_rule_settings():
    """全員で共有するオカ・ウマ設定をDBから読む"""
    try:
        oka = int(AppSetting.get_value("rule_oka", DEFAULT_OKA))
    except (TypeError, ValueError):
        oka = DEFAULT_OKA
    return {"oka": oka, "uma": AppSetting.get_value("rule_uma", DEFAULT_UMA)}


class GameFormError(ValueError):
    """入力画面に表示するエラー。form_data は入力内容の再表示用"""

    def __init__(self, message, form_data):
        super().__init__(message)
        self.form_data = form_data


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_game_form(form):
    """戦績入力フォーム（新規・修正共通）を検証し、(form_data, Game に設定する値, 入力順のプレイヤー) を返す。

    入力に問題があれば GameFormError（メッセージは画面表示用）。
    """
    # 画面からは百点単位で送られてくる（score_unit なしの場合は点数そのもの）
    score_unit = SCORE_INPUT_UNIT if form.get("score_unit") == str(SCORE_INPUT_UNIT) else 1

    scores = [_parse_int(form.get(f"score{i}")) for i in range(1, 5)]
    player_ids = [_parse_int(form.get(f"player{i}_id")) for i in range(1, 5)]
    oka_value = _parse_int(form.get("oka", 0))

    # エラー時の再表示用（数値にできない値は空欄として扱う）
    form_data = {
        "oka": oka_value if oka_value is not None else form.get("oka", ""),
        "uma": form.get("uma", DEFAULT_UMA),
        "tie_order": form.get("tie_order", ""),
        "game_date": form.get("game_date", ""),
    }
    for i in range(1, 5):
        form_data[f"player{i}_id"] = form.get(f"player{i}_id", "")
        form_data[f"score{i}"] = scores[i - 1] * score_unit if scores[i - 1] is not None else 0

    def fail(message):
        return GameFormError(message, form_data)

    if None in player_ids:
        raise fail("プレイヤーを4人選んでください。")
    if None in scores or oka_value is None:
        raise fail("持ち点とオカは整数で入力してください。")

    # プレイヤーIDの重複チェック
    if len(set(player_ids)) < 4:
        raise fail("同じプレイヤーを複数選択しています。すべて異なるプレイヤーを選んでください。")

    # スコアの合計チェック
    total_score = sum(form_data[f"score{i}"] for i in range(1, 5))
    if total_score != 100000:
        raise fail(f"スコアの合計が100,000ではありません（現在: {total_score:,}）。")

    # スコア順に並び替え（降順）。座順は使わず、同点のときだけ指定された順位で決める
    entries = [{"id": player_ids[i - 1], "score": form_data[f"score{i}"]} for i in range(1, 5)]
    try:
        sorted_players = rank_entries(entries, _parse_tie_order(form_data["tie_order"]))
    except ValueError as tie_error:
        raise fail(str(tie_error)) from tie_error

    # うまを分解（文字列→整数リスト）
    try:
        uma_values = [int(x.strip()) for x in form_data["uma"].split(",")]
        if len(uma_values) != 4:
            raise ValueError("うまの値は4つ必要です。")
    except Exception as e:
        raise fail(f"うまの形式エラー: {str(e)}") from e

    oka_value = form_data["oka"]

    # うま・おかを加算した最終スコアを計算（1位にだけオカ）
    values = {"oka": oka_value}
    for idx, player_data in enumerate(sorted_players):
        values[f"player{idx + 1}_id"] = player_data["id"]
        values[f"score{idx + 1}"] = player_data["score"] + uma_values[idx] + (oka_value if idx == 0 else 0)
        values[f"uma{idx + 1}"] = uma_values[idx]

    return form_data, values, entries


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    players = Player.query.all()
    error = None
    form_data = {}

    if request.method == "POST":
        try:
            form_data, values, entries = _parse_game_form(request.form)
        except GameFormError as form_error:
            return render_template("index.html", players=players, error=str(form_error), form_data=form_error.form_data)

        try:
            new_game = Game(**values)
            db.session.add(new_game)
            # 次回の入力用に、ルールを全員共通の設定として保存
            AppSetting.set_value("rule_oka", values["oka"])
            AppSetting.set_value("rule_uma", ",".join(str(values[f"uma{i}"]) for i in range(1, 5)))
            db.session.commit()

            uploaded_photo = request.files.get('photo')
            photo_payload = _save_game_photo(uploaded_photo)
            if photo_payload and photo_payload.get('image_url'):
                existing_photo = GamePhoto.query.filter_by(game_id=new_game.id).first()
                if existing_photo:
                    existing_photo.image_url = photo_payload['image_url']
                    existing_photo.public_id = photo_payload.get('public_id')
                else:
                    db.session.add(GamePhoto(
                        game_id=new_game.id,
                        image_url=photo_payload['image_url'],
                        public_id=photo_payload.get('public_id'),
                    ))
                db.session.commit()

            # 各プレイヤーのトータルスコアを加算
            for i in range(1, 5):
                player = db.session.get(Player, values[f"player{i}_id"])
                player.total_score = (player.total_score or 0) + values[f"score{i}"]
            db.session.commit()

            _record_permanent_achievements(
                Player.query.filter(Player.id.in_([entry["id"] for entry in entries])).all()
            )
            db.session.commit()

            # 次の局の入力欄を、今回と同じ並び順で出すために覚えておく
            session["last_player_order"] = [entry["id"] for entry in entries]

            return redirect(url_for('results'))

        except Exception as e:
            db.session.rollback()
            error = f"入力エラーが発生しました: {str(e)}"
            return render_template("index.html", players=players, error=error, form_data=form_data)

    # 初期表示用フォームデータ
    form_data = {
        f"player{i}_id": "" for i in range(1, 5)
    }
    form_data.update({
        f"score{i}": 0 for i in range(1, 5)
    })
    form_data.update(_load_rule_settings())

    # 今日の直前の対局の4人（なければ本人）を初期選択しておく
    for i, player_id in enumerate(_default_player_ids(), start=1):
        form_data[f"player{i}_id"] = str(player_id) if player_id else ""

    # ライブ卓から「戦績入力へ」で来た場合は、終了時の点数と席順を引き継ぐ
    live_form_data = finished_table_form_data(request.args.get("live", type=int))
    if live_form_data:
        form_data.update(live_form_data)

    return render_template("index.html", players=players, error=error, form_data=form_data)

def _save_game_photo(uploaded_file):
    """ゲーム写真を保存し、表示用URLを返す。ファイル未指定時はNoneを返す。"""
    if not uploaded_file or not uploaded_file.filename:
        return None

    original_name = secure_filename(uploaded_file.filename)
    if '.' not in original_name:
        return None

    extension = original_name.rsplit('.', 1)[1].lower()
    allowed_extensions = {'png', 'jpg', 'jpeg', 'webp'}
    if extension not in allowed_extensions:
        return None

    filename = f'{uuid4().hex}.{extension}'

    if _is_cloudinary_enabled():
        try:
            uploaded_file.stream.seek(0)
            result = cloudinary.uploader.upload(
                uploaded_file.stream,
                folder='mahjong/game_photos',
                public_id=filename.rsplit('.', 1)[0],
                resource_type='image',
            )
            return {
                'image_url': result.get('secure_url') or result.get('url'),
                'public_id': result.get('public_id'),
            }
        except Exception:
            logger.exception('Cloudinary game photo upload failed')
            return None

    photo_directory = os.path.join(app.static_folder, 'game_photos')
    os.makedirs(photo_directory, exist_ok=True)
    uploaded_file.save(os.path.join(photo_directory, filename))
    return {
        'image_url': f"/static/game_photos/{filename}",
        'public_id': None,
    }


@app.route("/results")
@login_required
def results():
    # 累計スコアは対局の追加・削除時に更新済みなので、ここでは再計算しない
    game_photos = {photo.game_id: photo for photo in GamePhoto.query.all()}

    # 複数日付取得
    raw_dates = request.args.getlist("date")  # 例 ["2025‑07‑09,2025‑11‑09"] または ["2025‑07‑09","2025‑11‑09"]
    # カンマ区切り１要素の場合も分割
    filter_dates = []
    for rd in raw_dates:
        # 空文字回避
        if not rd:
            continue
        # カンマで分割してリスト化
        for d in rd.split(','):
            d2 = d.strip()
            if d2:
                filter_dates.append(d2)
    # 重複を排除（任意）
    filter_dates = list(dict.fromkeys(filter_dates))
    filter_player = request.args.get("player", "").strip()
    filter_rank = request.args.get("rank", "").strip()

    players = Player.query.order_by(Player.id).all()
    games = Game.query.order_by(Game.date, Game.id.desc()).all()

    # 日付毎のゲームグループ化
    games_by_date = defaultdict(list)
    for game in games:
        games_by_date[game.date.strftime('%Y-%m-%d')].append(game)

    table_data = []

    # --- ここまで元のまま ---

    for date, games_on_date in sorted(games_by_date.items()):
        # 日付フィルタがある場合、対象日だけに絞る
        if filter_dates and date not in filter_dates:
            continue

        player_daily_totals = {p.name: 0 for p in players}

        for idx, game in enumerate(reversed(games_on_date), start=1):
            scores = [
                {"player": game.player1, "score": game.score1, "uma": game.uma1},
                {"player": game.player2, "score": game.score2, "uma": game.uma2},
                {"player": game.player3, "score": game.score3, "uma": game.uma3},
                {"player": game.player4, "score": game.score4, "uma": game.uma4},
            ]

            ranked = sorted(scores, key=lambda x: x["score"], reverse=True)
            for rank, s in enumerate(ranked, start=1):
                s["rank"] = rank

            score_dict = {}
            for s in scores:
                name = s["player"].name
                oka = game.oka if s["rank"] == 1 else 0
                raw = s["score"] - s["uma"] - oka

                score_dict[name] = {
                    "score": s["score"],
                    "rank": s["rank"],
                    "uma": s["uma"],
                    "oka": oka,
                    "raw": raw
                }

                player_daily_totals[name] += s["score"]

            # ランクフィルタがあるときは対象ゲームだけを追加
            if filter_player and filter_rank:
                target_score = score_dict.get(filter_player)
                if not target_score or str(target_score["rank"]) != filter_rank:
                    continue  # スキップ

            table_data.append({
                "type": "game",
                "date": date,
                "game_num": idx,
                "game_id": game.id,
                "scores": score_dict,
                "photo": game_photos.get(game.id)
            })

        # ゲームが1件以上追加されていればその日の合計も表示
        if any(d["date"] == date for d in table_data):
            total_ranked = sorted(player_daily_totals.items(), key=lambda x: x[1], reverse=True)
            total_score_dict = {
                name: {"total": total, "rank": rank + 1}
                for rank, (name, total) in enumerate(total_ranked)
            }
            table_data.append({
                "type": "summary",
                "date": date,
                "totals": total_score_dict
            })

    # ----------- ここからグラフ用データ生成 ------------

    # グラフ表示タイプ（累積スコア or ゲームスコア）をラジオボタンで切り替える
    graph_type = request.args.get("graph_type", "cumulative")  # defaultは累積スコア

    # プレイヤー名リスト
    player_names = [p.name for p in players]

    # 累積スコアと日ごとのスコアを準備
    cumulative_scores = {name: 0 for name in player_names}  # 累積スコア
    score_trends = {name: [] for name in player_names}  # グラフ用
    daily_scores = {name: [] for name in player_names}  # 各ゲームのスコア
    x_labels = []

    filtered_dates = sorted(games_by_date.keys())
    if filter_dates:
        filtered_dates = [d for d in sorted(games_by_date.keys()) if d in filter_dates]

    grand_total_by_player = {name: 0 for name in player_names}
    for row in table_data:
        if row["type"] == "game":
            for name, info in row["scores"].items():
                grand_total_by_player[name] += info["score"]
    grand_ranked = sorted(grand_total_by_player.items(), key=lambda x: x[1], reverse=True)
    grand_rank_dict = {name: {"total": total, "rank": idx+1}
                       for idx, (name, total) in enumerate(grand_ranked)}

    game_counter = 1

    for date in filtered_dates:
        games_on_date = games_by_date[date]
        
        # 各プレイヤーの日付ごとのスコア
        daily_total_score = {name: 0 for name in player_names}
        for game in reversed(games_on_date):
            scores = [
                {"player": game.player1, "score": game.score1},
                {"player": game.player2, "score": game.score2},
                {"player": game.player3, "score": game.score3},
                {"player": game.player4, "score": game.score4},
            ]
            
            for s in scores:
                player = s["player"]
                score = s["score"]

                if player and player.name:
                    name = player.name
                    daily_scores[name].append(score)  # ✅ ゲームごとのスコアを格納
                    daily_total_score[name] += score  # ✅ 日合計の更新

            x_labels.append(f"{date} G{game_counter}")
            game_counter += 1
            
        # 累積スコアの更新
        for name in player_names:
            cumulative_scores[name] += daily_total_score[name]  # 累積スコア
            score_trends[name].append(cumulative_scores[name])  # グラフ用

    date_labels = filtered_dates

    return render_template(
        "results.html",
        players=players,
        table_data=table_data,
        filter_dates=filter_dates,
        filter_player=filter_player,
        filter_rank=filter_rank,
        date_labels=date_labels,
        score_trends=score_trends,
        daily_scores=daily_scores,
        graph_type=graph_type,  # 選択されたグラフタイプを渡す
        x_labels=x_labels,
        grand_totals=grand_rank_dict
    )

@app.route("/players")
@login_required
def players():
    players = Player.query.all()
    return render_template("players.html", players=players)

@app.route("/add_player", methods=["GET", "POST"])
@login_required
def add_player():
    if request.method == "POST":
        player_name = request.form["name"]
        new_player = Player(name=player_name)
        db.session.add(new_player)
        db.session.commit()
        return redirect(url_for('players'))

    return render_template("add_player.html")

@app.route("/delete_player/<int:player_id>", methods=["POST"])
@login_required
def delete_player(player_id):
    player = Player.query.get_or_404(player_id)

    # もしプレイヤーがGameで使われていたら削除できないようにする例（任意）
    used_in_games = Game.query.filter(
        (Game.player1_id == player_id) |
        (Game.player2_id == player_id) |
        (Game.player3_id == player_id) |
        (Game.player4_id == player_id)
    ).first()

    if used_in_games:
        flash("このプレイヤーはゲームに使用されているため削除できません。")
        return redirect(url_for('players'))

    User.query.filter_by(player_id=player.id).update({"player_id": None})
    db.session.delete(player)
    db.session.commit()
    return redirect(url_for('players'))

@app.route("/edit_player/<int:player_id>", methods=["GET", "POST"])
@login_required
def edit_player(player_id):
    player = Player.query.get_or_404(player_id)
    
    if request.method == "POST":
        new_name = request.form.get("name")
        if new_name:
            player.name = new_name
            db.session.commit()
            flash("プレイヤー名を更新しました。")
            return redirect(url_for("players"))
        else:
            flash("名前を入力してください。")

    return render_template("edit_player.html", player=player)


def calculate_payment(total_cost, player_food_costs, late_players, player_rankings):
    """
    Calculate payment amounts for each player.
    
    Args:
        total_cost: Total game & drink cost (int)
        player_food_costs: dict {player_name: food_cost}
        late_players: set of player names who were late
        player_rankings: dict {player_name: rank} where rank is 1-4
    
    Returns:
        dict {player_name: total_payment}
    """
    players_list = list(player_food_costs.keys())
    total_food = sum(player_food_costs.values())
    game_drink_cost = total_cost - total_food
    
    # Step 1: Each player pays their food cost
    payments = {name: player_food_costs[name] for name in players_list}
    
    # Step 2: Late players pay 10% of (game_drink_cost - total_food)
    # Actually: 10% of (game_drink_cost) since total_food is already deducted from total_cost
    late_share = game_drink_cost * 0.1
    for late_player in late_players:
        if late_player in payments:
            payments[late_player] += late_share
    
    # Step 3: Distribute remaining amount by ranking
    remaining_cost = game_drink_cost - (len(late_players) * late_share)
    rank_percentages = {1: 0.10, 2: 0.20, 3: 0.30, 4: 0.40}
    
    for player_name, rank in player_rankings.items():
        if rank in rank_percentages:
            payments[player_name] += remaining_cost * rank_percentages[rank]
    
    return {name: round(amount, 0) for name, amount in payments.items()}


@app.route("/calculate_payment", methods=["POST"])
@login_required
def calculate_payment_route():
    """API endpoint to calculate payment amounts."""
    try:
        data = request.json
        total_cost = float(data.get("total_cost", 0))
        player_food_costs = {name: float(cost) for name, cost in data.get("player_food_costs", {}).items()}
        late_players = set(data.get("late_players", []))

        # `player_rankings` may be sent as {name: rank} or {name: {"rank": rank, ...}}
        raw_rankings = data.get("player_rankings", {}) or {}
        player_rankings = {}
        for name, val in raw_rankings.items():
            if isinstance(val, dict):
                # expected structure: {"total": ..., "rank": N}
                rank_val = val.get("rank")
            else:
                rank_val = val

            if rank_val is None:
                raise ValueError(f"順位が不明なプレイヤーがあります: {name}")

            try:
                player_rankings[name] = int(rank_val)
            except Exception:
                raise ValueError(f"無効な順位値: {name} -> {rank_val}")

        result = calculate_payment(total_cost, player_food_costs, late_players, player_rankings)
        return jsonify({"success": True, "payments": result})
    except Exception as e:
        logger.exception("Payment calculation failed")
        return jsonify({"success": False, "error": str(e)}), 400


def build_player_navigation(players, current_player_id):
    """プロフィールの前後ナビゲーション用に並び順を返す"""
    ordered_players = list(players)
    current_index = next((idx for idx, player in enumerate(ordered_players) if player.id == current_player_id), None)
    if current_index is None:
        return {'prev_player': None, 'next_player': None}

    prev_player = ordered_players[current_index - 1] if current_index > 0 else None
    next_player = ordered_players[current_index + 1] if current_index < len(ordered_players) - 1 else None
    return {'prev_player': prev_player, 'next_player': next_player}


def get_unlocked_title_ids(player, players=None, player_stats=None):
    """称号の解除済み一覧を、統計計算を再利用して高速に返す"""
    if player_stats is None:
        player_stats = compute_all_player_stats(players)

    achieved_title_ids = get_achieved_title_ids(player.id)
    unlocked_title_ids = set()
    for title in Title.query.order_by(Title.id).all():
        if title.is_permanent:
            if title.id in achieved_title_ids:
                unlocked_title_ids.add(title.id)
            continue
        if title.meets_conditions(player, all_player_stats=player_stats):
            unlocked_title_ids.add(title.id)

    return unlocked_title_ids


@app.route("/profile/<int:player_id>")
@login_required
def profile(player_id):
    player = Player.query.get_or_404(player_id)

    # 全メンバーのプロフィール指標を比較して、各指標のトップを判定
    players = Player.query.order_by(Player.id).all()
    player_stats = compute_all_player_stats(players)
    navigation = build_player_navigation(players, player.id)

    # ゲーム未参加プレイヤーは比較対象外
    valid_stats = [s for s in player_stats.values() if s['total_games'] > 0]

    best_values = {}
    if valid_stats:
        best_values = {
            'avg_rank': min(valid_stats, key=lambda s: s['avg_rank'])['avg_rank'],
            'last_avoidance_rate': max(valid_stats, key=lambda s: s['last_avoidance_rate'])['last_avoidance_rate'],
            'avg_raw_score': max(valid_stats, key=lambda s: s['avg_raw_score'])['avg_raw_score'],
            'max_raw_score': max(valid_stats, key=lambda s: s['max_raw_score'])['max_raw_score'],
            'min_raw_score': max(valid_stats, key=lambda s: s['min_raw_score'])['min_raw_score'],
            'max_consecutive_top1': max(valid_stats, key=lambda s: s['max_consecutive_top1'])['max_consecutive_top1'],
            'max_consecutive_top2': max(valid_stats, key=lambda s: s['max_consecutive_top2'])['max_consecutive_top2'],
            'max_consecutive_last': min(valid_stats, key=lambda s: s['max_consecutive_last'])['max_consecutive_last'],
        }

    stats = player_stats[player.id]
    stats['best_avg_rank'] = stats['total_games'] > 0 and stats['avg_rank'] == best_values.get('avg_rank')
    stats['best_last_avoidance_rate'] = stats['total_games'] > 0 and stats['last_avoidance_rate'] == best_values.get('last_avoidance_rate')
    stats['best_avg_raw_score'] = stats['total_games'] > 0 and stats['avg_raw_score'] == best_values.get('avg_raw_score')
    stats['best_max_raw_score'] = stats['total_games'] > 0 and stats['max_raw_score'] == best_values.get('max_raw_score')
    stats['best_min_raw_score'] = stats['total_games'] > 0 and stats['min_raw_score'] == best_values.get('min_raw_score')
    stats['best_max_consecutive_top1'] = stats['total_games'] > 0 and stats['max_consecutive_top1'] == best_values.get('max_consecutive_top1')
    stats['best_max_consecutive_top2'] = stats['total_games'] > 0 and stats['max_consecutive_top2'] == best_values.get('max_consecutive_top2')
    stats['best_max_consecutive_last'] = stats['total_games'] > 0 and stats['max_consecutive_last'] == best_values.get('max_consecutive_last')
    is_top_player = stats['best_avg_rank']

    # 永続称号は、条件を満たした時点で解除履歴を保存する
    _record_permanent_achievements([player], all_player_stats=player_stats)

    # 装備中の称号の条件確認と自動削除
    for slot_attr in ('active_title_1_id', 'active_title_2_id', 'active_title_3_id'):
        title_id = getattr(player, slot_attr)
        if title_id:
            title = db.session.get(Title, title_id)
            if title and not title.is_permanent and not title.meets_conditions(player, all_player_stats=player_stats):
                # 条件を満たさなくなったので外す
                setattr(player, slot_attr, None)

    db.session.commit()

    acquired_title_ids = get_unlocked_title_ids(player, players=players, player_stats=player_stats)
    acquired_titles = []
    for title in Title.query.order_by(Title.id).all():
        if title.id in acquired_title_ids:
            acquired_titles.append({
                'id': title.id,
                'name': title.name,
                'badge_filename': title.badge_filename,
                'badge_image_url': _badge_image_url_from_value(title.badge_filename),
            })

    return render_template(
        "profile.html",
        player=player,
        stats=stats,
        acquired_titles=acquired_titles,
        navigation=navigation,
        is_top_player=is_top_player,
        can_edit_titles=_can_edit_player_titles(player),
    )


def _can_edit_player_titles(player):
    """称号を付け替えられるのは、管理者・本人・まだ誰にも紐づいていないプレイヤーの場合"""
    if session.get("login_is_admin"):
        return True
    current_user = _current_user()
    if current_user and current_user.player_id == player.id:
        return True
    return not User.query.filter_by(player_id=player.id).first()


def _record_permanent_achievements(players, all_player_stats=None):
    """現在の成績で条件を満たした永続称号を解除履歴へ記録する"""
    permanent_titles = Title.query.filter_by(is_permanent=True).all()
    if not permanent_titles:
        return
    if all_player_stats is None:
        all_player_stats = compute_all_player_stats()
    for player in players:
        achieved_title_ids = get_achieved_title_ids(player.id)
        for title in permanent_titles:
            if title.id not in achieved_title_ids and title.meets_conditions(player, all_player_stats=all_player_stats):
                db.session.add(PlayerTitleAchievement(player_id=player.id, title_id=title.id))


@app.route("/api/player/<int:player_id>/title/update", methods=["POST"])
@login_required
def update_player_title(player_id):
    """プレイヤーの装備中の称号を更新"""
    data = request.get_json()
    player = Player.query.get_or_404(player_id)
    if not _can_edit_player_titles(player):
        return jsonify({'error': '本人のプレイヤーの称号のみ変更できます'}), 403

    slot = data.get('slot')  # 1, 2, または 3
    title_id = data.get('title_id')  # nullは許可（外す場合）

    if slot not in [1, 2, 3]:
        return jsonify({'error': 'Invalid slot'}), 400

    # タイトルが指定されている場合、取得可能か確認
    if title_id:
        title = Title.query.get_or_404(title_id)
        if not title.is_permanent and not title.check_unlocked(player):
            return jsonify({'error': 'Title not unlocked'}), 403
    
    # スロットを更新
    if slot == 1:
        player.active_title_1_id = title_id
    elif slot == 2:
        player.active_title_2_id = title_id
    elif slot == 3:
        player.active_title_3_id = title_id
    
    db.session.commit()
    return jsonify({'success': True})


TITLE_BADGE_ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}


def _save_title_badge(uploaded_file):
    """称号バッジをCloudinaryまたはstatic/title_badgesへ保存し、DB用URL/ファイル名を返す"""
    if not uploaded_file or not uploaded_file.filename:
        return None

    original_name = secure_filename(uploaded_file.filename)
    if '.' not in original_name:
        raise ValueError('画像ファイルの拡張子が必要です')

    extension = original_name.rsplit('.', 1)[1].lower()
    if extension not in TITLE_BADGE_ALLOWED_EXTENSIONS:
        raise ValueError('対応している画像形式は PNG、JPG、JPEG、WEBP です')

    filename = f'{uuid4().hex}.{extension}'

    if _is_cloudinary_enabled():
        try:
            uploaded_file.stream.seek(0)
            result = cloudinary.uploader.upload(
                uploaded_file.stream,
                folder='mahjong/title_badges',
                public_id=filename.rsplit('.', 1)[0],
                resource_type='image',
            )
            return result.get('secure_url') or result.get('url')
        except Exception:
            logger.exception('Cloudinary upload failed')
            raise ValueError('Cloudinaryへのアップロードに失敗しました')

    badge_directory = os.path.join(app.static_folder, 'title_badges')
    os.makedirs(badge_directory, exist_ok=True)
    uploaded_file.save(os.path.join(badge_directory, filename))
    return filename


def _title_form_values(form):
    """管理画面のフォーム値をTitle用の辞書に変換する"""
    name = form.get('name', '').strip()
    if not name:
        raise ValueError('称号名を入力してください')
    if len(name) > 50:
        raise ValueError('称号名は50文字以内で入力してください')

    def integer_value(field_name, label, min_value=0):
        value = (form.get(field_name, '') or '').strip() or '0'
        try:
            parsed = int(value)
        except ValueError as error:
            raise ValueError(f'{label}は整数で入力してください') from error
        if parsed < min_value:
            if min_value == 0:
                raise ValueError(f'{label}は0以上で入力してください')
            raise ValueError(f'{label}は{min_value}以上で入力してください')
        return parsed

    def float_value(field_name, label, maximum=None, default_value=0.0):
        value = (form.get(field_name, '') or '').strip() or str(default_value)
        try:
            parsed = float(value)
        except ValueError as error:
            raise ValueError(f'{label}は数値で入力してください') from error
        if parsed < 0 or (maximum is not None and parsed > maximum):
            limit = f'0以上{maximum}以下' if maximum is not None else '0以上'
            raise ValueError(f'{label}は{limit}で入力してください')
        return parsed

    return {
        'name': name,
        'required_total_games': integer_value('required_total_games', '必要対戦数'),
        'required_avg_rank': float_value('required_avg_rank', '必要平均着順', 4.1, default_value=4.1),
        'required_win_count': integer_value('required_win_count', '必要1位回数'),
        'required_last_avoidance_rate': float_value(
            'required_last_avoidance_rate', '必要ラス回避率', 100.0, default_value=0.0
        ),
        'required_max_consecutive_top1': integer_value(
            'required_max_consecutive_top1', '必要最大連続トップ数'
        ),
        'required_max_consecutive_last': integer_value(
            'required_max_consecutive_last', '必要最大連続ラス数'
        ),
        'required_highest_rank_1_rate': 'required_highest_rank_1_rate' in form,
        'required_highest_last_avoidance_rate': 'required_highest_last_avoidance_rate' in form,
        'required_highest_raw_score_std_dev': 'required_highest_raw_score_std_dev' in form,
        'required_total_raw_score': integer_value('required_total_raw_score', '必要合計素点'),
        'required_max_raw_score': integer_value('required_max_raw_score', '必要最大素点'),
        'required_min_raw_score': integer_value('required_min_raw_score', '必要最低素点', min_value=-999999),
        'is_permanent': 'is_permanent' in form,
    }


@app.route('/admin/titles', methods=['GET', 'POST'])
@admin_required
def admin_titles():
    """称号の追加・編集を行う管理画面"""
    edit_title = None
    if request.method == 'POST':
        title_id = request.form.get('title_id', '').strip()
        edit_title = Title.query.get(int(title_id)) if title_id else None
        if title_id and not edit_title:
            return render_template(
                'admin_titles.html',
                titles=Title.query.order_by(Title.id).all(),
                edit_title=None,
                error_message='編集対象の称号が見つかりません',
                message=None,
            ), 404
        try:
            title_values = _title_form_values(request.form)
            duplicate = Title.query.filter_by(name=title_values['name']).first()
            if duplicate and duplicate.id != (edit_title.id if edit_title else None):
                raise ValueError('同じ名前の称号がすでに存在します')

            badge_filename = _save_title_badge(request.files.get('badge'))
            if not edit_title and not badge_filename:
                raise ValueError('新規追加時はバッジ画像が必要です')

            if edit_title:
                for key, value in title_values.items():
                    setattr(edit_title, key, value)
                if badge_filename:
                    edit_title.badge_filename = badge_filename
                message = '称号を更新しました'
            else:
                db.session.add(Title(**title_values, badge_filename=badge_filename))
                message = '称号を追加しました'

            db.session.commit()
            return redirect(url_for('admin_titles', message=message))
        except (ValueError, TypeError) as error:
            db.session.rollback()
            error_message = str(error)
        except Exception:
            db.session.rollback()
            logger.exception('Title administration failed')
            error_message = '称号の保存に失敗しました'
    else:
        edit_id = request.args.get('edit', type=int)
        edit_title = Title.query.get(edit_id) if edit_id else None
        error_message = None

    titles = Title.query.order_by(Title.id).all()
    return render_template(
        'admin_titles.html',
        titles=titles,
        edit_title=edit_title,
        error_message=error_message,
        message=request.args.get('message'),
    )


@app.route('/admin/titles/<int:title_id>/delete', methods=['POST'])
@admin_required
def delete_admin_title(title_id):
    """管理画面から称号を削除する"""
    title = Title.query.get_or_404(title_id)
    PlayerTitleAchievement.query.filter_by(title_id=title.id).delete(
        synchronize_session=False
    )
    for player in Player.query.filter(
        (Player.active_title_1_id == title.id) |
        (Player.active_title_2_id == title.id) |
        (Player.active_title_3_id == title.id)
    ).all():
        if player.active_title_1_id == title.id:
            player.active_title_1_id = None
        if player.active_title_2_id == title.id:
            player.active_title_2_id = None
        if player.active_title_3_id == title.id:
            player.active_title_3_id = None

    db.session.delete(title)
    db.session.commit()
    return redirect(url_for('admin_titles', message='称号を削除しました'))


@app.route("/delete_game/<int:game_id>", methods=["POST"])
@game_manager_required
def delete_game(game_id):
    game = Game.query.get_or_404(game_id)
    # 写真は game_id が必須なので、対局より先に消す
    GamePhoto.query.filter_by(game_id=game.id).delete(synchronize_session=False)
    db.session.delete(game)
    db.session.commit()
    recalc_total_scores()
    return redirect(url_for('results'))


def _game_form_data(game):
    """保存済みの対局を、入力画面の形（素点・ウマ・オカ・同点時の順位）に戻す"""
    form_data = {
        "oka": game.oka or 0,
        "uma": ",".join(str(getattr(game, f"uma{i}") or 0) for i in range(1, 5)),
        "game_date": game.date.isoformat() if game.date else "",
        # 保存時に順位順で並べているので、その順を同点時の順位として引き継ぐ
        "tie_order": ",".join(str(getattr(game, f"player{i}_id")) for i in range(1, 5)),
    }
    for i in range(1, 5):
        oka = (game.oka or 0) if i == 1 else 0
        form_data[f"player{i}_id"] = str(getattr(game, f"player{i}_id"))
        form_data[f"score{i}"] = getattr(game, f"score{i}") - (getattr(game, f"uma{i}") or 0) - oka
    return form_data


@app.route("/games/<int:game_id>/edit", methods=["GET", "POST"])
@game_manager_required
def edit_game(game_id):
    """保存済みの対局を修正する（入力画面と同じ検証・計算を使う）"""
    game = Game.query.get_or_404(game_id)
    players = Player.query.all()

    def render(form_data, error=None):
        return render_template("index.html", players=players, error=error, form_data=form_data, edit_game=game)

    if request.method == "GET":
        return render(_game_form_data(game))

    try:
        form_data, values, _ = _parse_game_form(request.form)
    except GameFormError as form_error:
        return render(form_error.form_data, str(form_error))

    try:
        game_date = date.fromisoformat(request.form.get("game_date", ""))
    except ValueError:
        return render(form_data, "日付を正しく入力してください。")

    old_player_ids = {getattr(game, f"player{i}_id") for i in range(1, 5)}
    for key, value in values.items():
        setattr(game, key, value)
    game.date = game_date
    db.session.commit()

    # 累計スコアと永続称号を、修正後の内容で更新する
    recalc_total_scores()
    affected_ids = old_player_ids | {values[f"player{i}_id"] for i in range(1, 5)}
    _record_permanent_achievements(Player.query.filter(Player.id.in_(affected_ids)).all())
    db.session.commit()

    return redirect(url_for('results', date=game_date.isoformat()))


def _is_cloudinary_enabled():
    return cloudinary is not None and bool(os.environ.get("CLOUDINARY_URL"))


def _badge_image_url_from_value(badge_value):
    if not badge_value:
        return ''
    if str(badge_value).startswith(('http://', 'https://')):
        return badge_value
    return f"/static/title_badges/{badge_value}"


if __name__ == "__main__":
    with app.app_context():
        # In local development (default sqlite) it's convenient to create tables automatically.
        # In production (Postgres via DATABASE_URL / Render), prefer using migrations (Alembic/Flask-Migrate).
        uri = app.config.get('SQLALCHEMY_DATABASE_URI', '') or ''
        is_sqlite = uri.startswith('sqlite:')
        debug_mode = os.environ.get("FLASK_DEBUG", "False") == "True"

        if is_sqlite or debug_mode or os.environ.get("FORCE_CREATE_ALL", "False") == "True":
            # Only auto-create tables for local sqlite or when explicitly requested
            db.create_all()

    # Run the app. In production Render will use Procfile (gunicorn). Here we honor env vars.
    app.run(host=os.environ.get("FLASK_HOST", "127.0.0.1"), port=int(os.environ.get("FLASK_PORT", 5000)), debug=debug_mode)
