from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from flask_migrate import Migrate
from flask import Flask, render_template, request, redirect, url_for, session
from flask import flash
from collections import defaultdict
from flask import request
import os

app = Flask(__name__)
app.secret_key = "your-secret-key"

# SQLiteデータベースの設定
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "sqlite:///mahjong.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# プレイヤー（船籍）モデル
class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    total_score = db.Column(db.Integer, default=0)  # 累計スコア

# ゲーム戦績モデル
class Game(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, default=lambda: (datetime.utcnow() + timedelta(hours=9)).date())
    player1_id = db.Column(db.Integer, db.ForeignKey('player.id'))
    player2_id = db.Column(db.Integer, db.ForeignKey('player.id'))
    player3_id = db.Column(db.Integer, db.ForeignKey('player.id'))
    player4_id = db.Column(db.Integer, db.ForeignKey('player.id'))
    score1 = db.Column(db.Integer)
    score2 = db.Column(db.Integer)
    score3 = db.Column(db.Integer)
    score4 = db.Column(db.Integer)
    player1 = db.relationship('Player', foreign_keys=[player1_id])
    player2 = db.relationship('Player', foreign_keys=[player2_id])
    player3 = db.relationship('Player', foreign_keys=[player3_id])
    player4 = db.relationship('Player', foreign_keys=[player4_id])
    oka = db.Column(db.Integer, default=0)      # おかを追加
    uma1 = db.Column(db.Integer, default=0)     # うま1位
    uma2 = db.Column(db.Integer, default=0)
    uma3 = db.Column(db.Integer, default=0)
    uma4 = db.Column(db.Integer, default=0)

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
                "uma": request.form.get("uma", "30000,10000,-10000,-30000"),
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
    "uma": session.get("last_uma", "30000,10000,-10000,-30000"),
    })

    return render_template("index.html", players=players, error=error, form_data=form_data)

@app.route("/results")
def results():
    recalc_total_scores()

    # フィルタ取得
    filter_date = request.args.get("date", "").strip()
    filter_player = request.args.get("player", "").strip()
    filter_rank = request.args.get("rank", "").strip()

    players = Player.query.order_by(Player.id).all()
    games = Game.query.order_by(Game.date, Game.id).all()

    # 日付毎のゲームグループ化
    games_by_date = defaultdict(list)
    for game in games:
        games_by_date[game.date.strftime('%Y-%m-%d')].append(game)

    table_data = []

    # --- ここまで元のまま ---

    for date, games_on_date in sorted(games_by_date.items()):
        # 日付フィルタがある場合、対象日だけに絞る
        if filter_date and filter_date != date:
            continue

        player_daily_totals = {p.name: 0 for p in players}

        for idx, game in enumerate(games_on_date, start=1):
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
    if filter_date:
        filtered_dates = [filter_date] if filter_date in filtered_dates else []

    game_counter = 1

    for date in filtered_dates:
        games_on_date = games_by_date[date]
        
        # 各プレイヤーの日付ごとのスコア
        daily_total_score = {name: 0 for name in player_names}
        for game in games_on_date:
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
        filter_date=filter_date,
        filter_player=filter_player,
        filter_rank=filter_rank,
        date_labels=date_labels,
        score_trends=score_trends,
        daily_scores=daily_scores,
        graph_type=graph_type,  # 選択されたグラフタイプを渡す
        x_labels=x_labels,
    )

@app.route("/players")
def players():
    players = Player.query.all()
    return render_template("players.html", players=players)

@app.route("/add_player", methods=["GET", "POST"])
def add_player():
    if request.method == "POST":
        player_name = request.form["name"]
        new_player = Player(name=player_name)
        db.session.add(new_player)
        db.session.commit()
        return redirect(url_for('players'))

    return render_template("add_player.html")

@app.route("/delete_player/<int:player_id>", methods=["POST"])
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


@app.route("/delete_game/<int:game_id>", methods=["POST"])
def delete_game(game_id):
    game = Game.query.get_or_404(game_id)
    db.session.delete(game)
    db.session.commit()
    return redirect(url_for('results'))


if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # アプリケーションコンテキスト内でDB作成

    app.run(debug=True)
