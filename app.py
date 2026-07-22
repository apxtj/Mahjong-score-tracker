from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from flask_migrate import Migrate
from sqlalchemy import func
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask import flash
from collections import defaultdict
from flask import request
import os
from functools import wraps
from flask import redirect, url_for
from models import db, User, Player, Game, Title, PlayerTitleAchievement
from werkzeug.utils import secure_filename
from uuid import uuid4

import logging

app = Flask(__name__)
# Secret key should come from environment in production
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

# Session cookie hardening for production
app.config.update(
    SESSION_COOKIE_SECURE=bool(os.environ.get("SESSION_COOKIE_SECURE", "True") == "True"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=os.environ.get("SESSION_COOKIE_SAMESITE", "Lax"),
)

# Configure basic logging
debug_mode = os.environ.get("FLASK_DEBUG", "False") == "True"
log_level = logging.DEBUG if debug_mode else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)

# SQLiteデータベースの設定
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "sqlite:///mahjong.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)
migrate = Migrate(app, db)

from auth import bp as auth_bp
app.register_blueprint(auth_bp)

def login_required(f):
    # @wraps(f)
    # def wrapper(*args, **kwargs):
    #     logger.debug("login_user_id in session: %s", session.get("login_user_id"))
    #     if "login_user_id" not in session:
    #         logger.debug("Redirecting to login")
    #         return redirect(url_for("auth.login_page", next=request.path))
    #     return f(*args, **kwargs)
    # return wrapper
    # 一時的に認証を無効化するため、デコレータを no-op にしています。
    # 必要に応じて元の実装に戻してください。
    logger.info("Authentication disabled: login_required is a no-op.")
    return f

@app.route("/logout")
def logout():
    session.pop("login_user_id", None)  # ログイン情報削除
    return redirect(url_for("auth.login_page"))

def recalc_total_scores():
    players = Player.query.all()
    for player in players:
        total = 0
        games = Game.query.filter(
            (Game.player1_id == player.id) |
            (Game.player2_id == player.id) |
            (Game.player3_id == player.id) |
            (Game.player4_id == player.id)
        ).all()

        for g in games:
            if g.player1_id == player.id:
                total += g.score1
            elif g.player2_id == player.id:
                total += g.score2
            elif g.player3_id == player.id:
                total += g.score3
            elif g.player4_id == player.id:
                total += g.score4
        player.total_score = total
    db.session.commit()


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    players = Player.query.all()
    error = None
    form_data = {}

    if request.method == "POST":
        try:
            # フォームデータの取得
            form_data = {
                "player1_id": request.form.get("player1_id", ""),
                "score1": int(request.form.get("score1", 0)),
                "player2_id": request.form.get("player2_id", ""),
                "score2": int(request.form.get("score2", 0)),
                "player3_id": request.form.get("player3_id", ""),
                "score3": int(request.form.get("score3", 0)),
                "player4_id": request.form.get("player4_id", ""),
                "score4": int(request.form.get("score4", 0)),
                "oka": int(request.form.get("oka", 0)),
                "uma": request.form.get("uma", "20000,-20000,-40000,-60000"),
            }
            
            # セッションに保存
            session["last_oka"] = form_data["oka"]
            session["last_uma"] = form_data["uma"]

            # プレイヤーIDの重複チェック
            player_ids = [
                form_data["player1_id"],
                form_data["player2_id"],
                form_data["player3_id"],
                form_data["player4_id"],
            ]
            if len(set(player_ids)) < 4:
                error = "同じプレイヤーを複数選択しています。すべて異なるプレイヤーを選んでください。"
                return render_template("index.html", players=players, error=error, form_data=form_data)

            # スコアの合計チェック
            total_score = form_data["score1"] + form_data["score2"] + form_data["score3"] + form_data["score4"]
            if total_score != 100000:
                error = f"スコアの合計が100,000ではありません（現在: {total_score}）。"
                return render_template("index.html", players=players, error=error, form_data=form_data)

            # スコア順に並び替え（降順）
            sorted_players = sorted(
                [
                    {"id": int(form_data["player1_id"]), "score": form_data["score1"]},
                    {"id": int(form_data["player2_id"]), "score": form_data["score2"]},
                    {"id": int(form_data["player3_id"]), "score": form_data["score3"]},
                    {"id": int(form_data["player4_id"]), "score": form_data["score4"]},
                ],
                key=lambda x: x['score'],
                reverse=True
            )

            # うまを分解（文字列→整数リスト）
            try:
                uma_values = [int(x.strip()) for x in form_data["uma"].split(",")]
                if len(uma_values) != 4:
                    raise ValueError("うまの値は4つ必要です。")
            except Exception as e:
                error = f"うまの形式エラー: {str(e)}"
                return render_template("index.html", players=players, error=error, form_data=form_data)

            oka_value = form_data["oka"]

            # うま・おかを加算した最終スコアを計算
            final_scores = []
            for idx, player_data in enumerate(sorted_players):
                oka = oka_value if idx == 0 else 0
                final_score = player_data['score'] + uma_values[idx] + oka
                final_scores.append(final_score)

            # Gameレコードの作成
            new_game = Game(
                player1_id=sorted_players[0]['id'], score1=final_scores[0],
                player2_id=sorted_players[1]['id'], score2=final_scores[1],
                player3_id=sorted_players[2]['id'], score3=final_scores[2],
                player4_id=sorted_players[3]['id'], score4=final_scores[3],
                oka=oka_value,
                uma1=uma_values[0],
                uma2=uma_values[1],
                uma3=uma_values[2],
                uma4=uma_values[3]
            )
            db.session.add(new_game)
            db.session.commit()

            # 各プレイヤーのトータルスコアを加算
            for idx, player_data in enumerate(sorted_players):
                player = Player.query.get(player_data['id'])
                player.total_score += final_scores[idx]
            db.session.commit()

            _record_permanent_achievements(
                Player.query.filter(Player.id.in_([p['id'] for p in sorted_players])).all()
            )
            db.session.commit()

            return redirect(url_for('results'))

        except Exception as e:
            error = f"入力エラーが発生しました: {str(e)}"
            return render_template("index.html", players=players, error=error, form_data=form_data)

    # 初期表示用フォームデータ
    form_data = {
        f"player{i}_id": "" for i in range(1, 5)
    }
    form_data.update({
        f"score{i}": 0 for i in range(1, 5)
    })
    form_data.update({
    "oka": session.get("last_oka", 0),
    "uma": session.get("last_uma", "20000,-20000,-40000,-60000"),
    })

    return render_template("index.html", players=players, error=error, form_data=form_data)

@app.route("/results")
@login_required
def results():
    recalc_total_scores()

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
                "scores": score_dict
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


@app.route("/profile/<int:player_id>")
@login_required
def profile(player_id):
    player = Player.query.get_or_404(player_id)

    # 全メンバーのプロフィール指標を比較して、各指標のトップを判定
    players = Player.query.order_by(Player.id).all()
    player_stats = {p.id: p.calculate_stats() for p in players}

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

    # 永続称号は、条件を満たした時点で解除履歴を保存する
    _record_permanent_achievements([player])

    # 装備中の称号の条件確認と自動削除
    for slot_attr, title_attr in [('active_title_1_id', 'active_title_1'), 
                                    ('active_title_2_id', 'active_title_2'), 
                                    ('active_title_3_id', 'active_title_3')]:
        title_id = getattr(player, slot_attr)
        if title_id:
            title = Title.query.get(title_id)
            if title and not title.is_permanent and not title.check_unlocked(player):
                # 条件を満たさなくなったので外す
                setattr(player, slot_attr, None)
    
    db.session.commit()

    # 取得可能な称号を計算
    acquired_titles = []
    all_titles = Title.query.all()
    for title in all_titles:
        if title.check_unlocked(player):
            acquired_titles.append({
                'id': title.id,
                'name': title.name,
                'badge_filename': title.badge_filename
            })

    return render_template("profile.html", player=player, stats=stats, acquired_titles=acquired_titles)


def _record_permanent_achievements(players):
    """現在の成績で条件を満たした永続称号を解除履歴へ記録する"""
    permanent_titles = Title.query.filter_by(is_permanent=True).all()
    for player in players:
        for title in permanent_titles:
            if title.meets_conditions(player) and not PlayerTitleAchievement.query.filter_by(
                player_id=player.id,
                title_id=title.id,
            ).first():
                db.session.add(PlayerTitleAchievement(player_id=player.id, title_id=title.id))


@app.route("/api/player/<int:player_id>/title/update", methods=["POST"])
def update_player_title(player_id):
    """プレイヤーの装備中の称号を更新"""
    data = request.get_json()
    player = Player.query.get_or_404(player_id)
    
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
    """称号バッジをstatic/title_badgesへ保存し、DB用ファイル名を返す"""
    if not uploaded_file or not uploaded_file.filename:
        return None

    original_name = secure_filename(uploaded_file.filename)
    if '.' not in original_name:
        raise ValueError('画像ファイルの拡張子が必要です')

    extension = original_name.rsplit('.', 1)[1].lower()
    if extension not in TITLE_BADGE_ALLOWED_EXTENSIONS:
        raise ValueError('対応している画像形式は PNG、JPG、JPEG、WEBP です')

    filename = f'{uuid4().hex}.{extension}'
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
        value = form.get(field_name, '0').strip()
        try:
            parsed = int(value)
        except ValueError as error:
            raise ValueError(f'{label}は整数で入力してください') from error
        if parsed < min_value:
            if min_value == 0:
                raise ValueError(f'{label}は0以上で入力してください')
            raise ValueError(f'{label}は{min_value}以上で入力してください')
        return parsed

    def float_value(field_name, label, maximum=None):
        value = form.get(field_name, '0').strip()
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
        'required_avg_rank': float_value('required_avg_rank', '必要平均着順', 4.1),
        'required_win_count': integer_value('required_win_count', '必要1位回数'),
        'required_last_avoidance_rate': float_value(
            'required_last_avoidance_rate', '必要ラス回避率', 100.0
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
def admin_titles():
    """称号の追加・編集を行う管理画面（認証なし、直接URLアクセス）"""
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
def delete_admin_title(title_id):
    """管理画面から称号を削除する（認証なし、直接URLアクセス）"""
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
@login_required
def delete_game(game_id):
    game = Game.query.get_or_404(game_id)
    db.session.delete(game)
    db.session.commit()
    recalc_total_scores()
    return redirect(url_for('results'))


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
