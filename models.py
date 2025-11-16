from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)

    credentials = db.relationship("Credential", backref="user", lazy=True)

class Credential(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    credential_id = db.Column(db.LargeBinary, unique=True, nullable=False)
    public_key = db.Column(db.LargeBinary, nullable=False)
    sign_count = db.Column(db.Integer, default=0)

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
