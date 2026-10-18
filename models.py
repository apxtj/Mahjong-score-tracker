from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    passkey_enrollment_token = db.Column(db.String(128), unique=True, nullable=True)
    passkey_enrollment_expires_at = db.Column(db.DateTime, nullable=True)
    # このユーザー本人のプレイヤー（任意）
    player_id = db.Column(db.Integer, db.ForeignKey("player.id"), nullable=True)

    credentials = db.relationship("Credential", backref="user", lazy=True)
    player = db.relationship("Player", foreign_keys=[player_id])


class AppSetting(db.Model):
    """オカ・ウマなど、全員で共有する設定値"""
    __tablename__ = "app_setting"

    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.Text, nullable=False)

    @classmethod
    def get_value(cls, key, default=None):
        setting = db.session.get(cls, key)
        return setting.value if setting else default

    @classmethod
    def set_value(cls, key, value):
        setting = db.session.get(cls, key)
        if setting:
            setting.value = str(value)
        else:
            db.session.add(cls(key=key, value=str(value)))

class Credential(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    credential_id = db.Column(db.LargeBinary, unique=True, nullable=False)
    public_key = db.Column(db.LargeBinary, nullable=False)
    sign_count = db.Column(db.Integer, default=0)
    # 既存の Passkey は記録がないため NULL（＝不明）
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=True)
    last_used_at = db.Column(db.DateTime, nullable=True)

# プレイヤー（船籍）モデル
class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    total_score = db.Column(db.Integer, default=0)  # 累計スコア
    
    # 使用中の称号（最大3つ）
    active_title_1_id = db.Column(db.Integer, db.ForeignKey('title.id'), nullable=True)
    active_title_2_id = db.Column(db.Integer, db.ForeignKey('title.id'), nullable=True)
    active_title_3_id = db.Column(db.Integer, db.ForeignKey('title.id'), nullable=True)
    
    active_title_1 = db.relationship('Title', foreign_keys=[active_title_1_id])
    active_title_2 = db.relationship('Title', foreign_keys=[active_title_2_id])
    active_title_3 = db.relationship('Title', foreign_keys=[active_title_3_id])

    def get_all_games(self):
        """このプレイヤーが参加した全ゲームを取得"""
        from sqlalchemy import or_
        games = Game.query.filter(
            or_(
                Game.player1_id == self.id,
                Game.player2_id == self.id,
                Game.player3_id == self.id,
                Game.player4_id == self.id
            )
        ).order_by(Game.date, Game.id).all()
        return games

    def get_player_data_for_game(self, game):
        """ゲーム内でのこのプレイヤーのデータ（スコア、ウマ、オカ）を取得"""
        if game.player1_id == self.id:
            return {
                'score': game.score1,
                'uma': game.uma1,
                'raw': game.score1 - game.uma1 - (game.oka if self._get_rank_in_game(game) == 1 else 0)
            }
        elif game.player2_id == self.id:
            return {
                'score': game.score2,
                'uma': game.uma2,
                'raw': game.score2 - game.uma2 - (game.oka if self._get_rank_in_game(game) == 1 else 0)
            }
        elif game.player3_id == self.id:
            return {
                'score': game.score3,
                'uma': game.uma3,
                'raw': game.score3 - game.uma3 - (game.oka if self._get_rank_in_game(game) == 1 else 0)
            }
        elif game.player4_id == self.id:
            return {
                'score': game.score4,
                'uma': game.uma4,
                'raw': game.score4 - game.uma4 - (game.oka if self._get_rank_in_game(game) == 1 else 0)
            }
        return None

    def _get_rank_in_game(self, game):
        """ゲーム内での順位を取得"""
        scores = [
            (1, game.score1),
            (2, game.score2),
            (3, game.score3),
            (4, game.score4)
        ]
        ranked = sorted(scores, key=lambda x: x[1], reverse=True)
        for rank, (player_num, score) in enumerate(ranked, start=1):
            player_id = getattr(game, f'player{player_num}_id')
            if player_id == self.id:
                return rank
        return None

    def calculate_stats(self, games=None):
        """全スタッツを計算して辞書で返す（games を渡すとDBを引かずにそれを使う。日付・ID順であること）"""
        if games is None:
            games = self.get_all_games()

        if not games:
            return {
                'total_games': 0,
                'avg_rank': 0.0,
                'rank_1_rate': 0.0,
                'rank_2_rate': 0.0,
                'rank_3_rate': 0.0,
                'rank_4_rate': 0.0,
                'last_avoidance_rate': 0.0,
                'avg_raw_score': 0.0,
                'raw_score_std_dev': 0.0,
                'max_raw_score': 0,
                'min_raw_score': 0,
                'max_consecutive_top2': 0,
                'max_consecutive_top1': 0,
                'max_consecutive_last': 0,
                'top1_count': 0
            }

        import statistics
        
        # ゲーム数
        total_games = len(games)
        
        # 各ゲームでの順位とスコアを取得
        ranks = []
        raw_scores = []
        
        for game in games:
            rank = self._get_rank_in_game(game)
            ranks.append(rank)
            
            player_data = self.get_player_data_for_game(game)
            if player_data:
                raw_scores.append(player_data['raw'])
        
        # 平均着順
        avg_rank = sum(ranks) / total_games if total_games > 0 else 0.0
        
        # 位率
        rank_1_count = ranks.count(1)
        rank_2_count = ranks.count(2)
        rank_3_count = ranks.count(3)
        rank_4_count = ranks.count(4)
        
        rank_1_rate = (rank_1_count / total_games * 100) if total_games > 0 else 0.0
        rank_2_rate = (rank_2_count / total_games * 100) if total_games > 0 else 0.0
        rank_3_rate = (rank_3_count / total_games * 100) if total_games > 0 else 0.0
        rank_4_rate = (rank_4_count / total_games * 100) if total_games > 0 else 0.0
        
        # ラス回避率（1,2,3位の率）
        last_avoidance_rate = ((total_games - rank_4_count) / total_games * 100) if total_games > 0 else 0.0
        
        # 平均素点
        avg_raw_score = sum(raw_scores) / total_games if total_games > 0 else 0.0
        
        # 合計素点
        total_raw_score = sum(raw_scores)
        
        # 標準偏差
        raw_score_std_dev = statistics.stdev(raw_scores) if len(raw_scores) > 1 else 0.0
        
        # 最高・最低素点
        max_raw_score = max(raw_scores) if raw_scores else 0
        min_raw_score = min(raw_scores) if raw_scores else 0
        
        # 連続記録（トップ、トップ2、ラス）
        max_consecutive_top1 = self._get_max_consecutive(ranks, [1])
        max_consecutive_top2 = self._get_max_consecutive(ranks, [1, 2])
        max_consecutive_last = self._get_max_consecutive(ranks, [4])
        
        return {
            'total_games': total_games,
            'avg_rank': round(avg_rank, 2),
            'rank_1_rate': round(rank_1_rate, 2),
            'rank_2_rate': round(rank_2_rate, 2),
            'rank_3_rate': round(rank_3_rate, 2),
            'rank_4_rate': round(rank_4_rate, 2),
            'last_avoidance_rate': round(last_avoidance_rate, 2),
            'avg_raw_score': round(avg_raw_score, 2),
            'total_raw_score': total_raw_score,
            'raw_score_std_dev': round(raw_score_std_dev, 2),
            'max_raw_score': max_raw_score,
            'min_raw_score': min_raw_score,
            'max_consecutive_top2': max_consecutive_top2,
            'max_consecutive_top1': max_consecutive_top1,
            'max_consecutive_last': max_consecutive_last,
            'top1_count': rank_1_count
        }

    def _get_max_consecutive(self, ranks, target_ranks):
        """指定した順位の最大連続数を計算"""
        if not ranks:
            return 0
        
        max_consecutive = 0
        current_consecutive = 0
        
        for rank in ranks:
            if rank in target_ranks:
                current_consecutive += 1
                max_consecutive = max(max_consecutive, current_consecutive)
            else:
                current_consecutive = 0
        
        return max_consecutive

def compute_all_player_stats(players=None):
    """全プレイヤーのスタッツを、対局を1回だけ読み込んでまとめて計算する"""
    if players is None:
        players = Player.query.order_by(Player.id).all()

    games_by_player = {player.id: [] for player in players}
    for game in Game.query.order_by(Game.date, Game.id).all():
        for player_id in {game.player1_id, game.player2_id, game.player3_id, game.player4_id}:
            if player_id in games_by_player:
                games_by_player[player_id].append(game)

    return {player.id: player.calculate_stats(games=games_by_player[player.id]) for player in players}


def get_achieved_title_ids(player_id):
    """永続称号の解除履歴を1クエリで取得する"""
    return {
        title_id for (title_id,) in db.session.query(PlayerTitleAchievement.title_id)
        .filter_by(player_id=player_id).all()
    }


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


class GamePhoto(db.Model):
    __tablename__ = 'game_photo'

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), unique=True, nullable=False)
    image_url = db.Column(db.Text, nullable=True)
    public_id = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    game = db.relationship('Game', backref=db.backref('photo', uselist=False))

# 称号モデル
class Title(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)  # 称号名（内部用）
    badge_filename = db.Column(db.String(255), nullable=False)    # バッチ画像URLまたはファイル名

    @property
    def badge_image_url(self):
        badge_value = self.badge_filename or ''
        if badge_value.startswith(('http://', 'https://')):
            return badge_value
        return f"/static/title_badges/{badge_value}"
    
    # 取得条件（複数条件可）
    required_total_games = db.Column(db.Integer, default=0)       # 最小対戦数
    required_avg_rank = db.Column(db.Float, default=4.1)          # 最大平均着順（小さいほど良い）
    required_win_count = db.Column(db.Integer, default=0)         # 最小1位回数
    required_last_avoidance_rate = db.Column(db.Float, default=0)  # 最小ラス回避率
    required_highest_rank_1_rate = db.Column(db.Boolean, default=False)  # 最高トップ率条件
    required_highest_last_avoidance_rate = db.Column(db.Boolean, default=False)  # 最高ラス回避率条件
    required_highest_raw_score_std_dev = db.Column(db.Boolean, default=False)  # 最高標準偏差条件
    required_max_consecutive_top1 = db.Column(db.Integer, default=0)  # 最大連続トップ数
    required_max_consecutive_last = db.Column(db.Integer, default=0)  # 最大連続ラス数
    required_total_raw_score = db.Column(db.Integer, default=0, nullable=False)
    required_max_raw_score = db.Column(db.Integer, default=0, nullable=False)
    required_min_raw_score = db.Column(db.Integer, default=0, nullable=False)
    is_permanent = db.Column(db.Boolean, default=False)  # 一度解除すれば以後使用可能
    
    def __repr__(self):
        return f'<Title {self.name}>'
    
    def meets_conditions(self, player, all_player_stats=None):
        """プレイヤーが現在の成績でこの称号の条件を満たしているか確認"""
        if all_player_stats is None:
            all_player_stats = compute_all_player_stats()

        stats = all_player_stats.get(player.id)
        if stats is None:
            stats = player.calculate_stats()
            all_player_stats[player.id] = stats

        def has_unique_highest(metric_name):
            ranked_players = []

            for other_player_id, other_stats in all_player_stats.items():
                if other_stats.get('total_games', 0) > 0:
                    ranked_players.append((other_player_id, other_stats.get(metric_name, 0)))

            if stats['total_games'] == 0 or not ranked_players:
                return False

            max_value = max(value for _, value in ranked_players)
            leaders = [player_id for player_id, value in ranked_players if value == max_value]
            return player.id in leaders and len(leaders) == 1 and stats.get(metric_name, 0) == max_value
        
        # 対戦数条件
        if stats['total_games'] < self.required_total_games:
            return False
        
        # 平均着順条件
        if stats['total_games'] > 0 and stats['avg_rank'] > self.required_avg_rank:
            return False
        
        # 1位回数条件
        if stats.get('top1_count', 0) < self.required_win_count:
            return False

        # 最大連続トップ数条件
        if stats.get('max_consecutive_top1', 0) < (self.required_max_consecutive_top1 or 0):
            return False

        # 最大連続ラス数条件
        if stats.get('max_consecutive_last', 0) < (self.required_max_consecutive_last or 0):
            return False
        
        # ラス回避率条件
        if stats['total_games'] > 0 and stats['last_avoidance_rate'] < self.required_last_avoidance_rate:
            return False
        
        # 最高トップ率条件（全プレイヤーを比較）
        if self.required_highest_rank_1_rate:
            if not has_unique_highest('rank_1_rate'):
                return False

        # 最高ラス回避率条件（全プレイヤーを比較）
        if self.required_highest_last_avoidance_rate:
            if not has_unique_highest('last_avoidance_rate'):
                return False

        # 最高標準偏差条件（全プレイヤーを比較）
        if self.required_highest_raw_score_std_dev:
            if not has_unique_highest('raw_score_std_dev'):
                return False
        
        # 合計素点条件
        if (self.required_total_raw_score or 0) > 0:
            if stats.get("total_raw_score", 0) < (self.required_total_raw_score or 0):
                return False

        # 最大素点条件
        if (self.required_max_raw_score or 0) > 0:
            if stats.get("max_raw_score", 0) < (self.required_max_raw_score or 0):
                return False

        # 最低素点条件
        if self.required_min_raw_score is not None and self.required_min_raw_score < 0:
            if stats.get('min_raw_score', 0) > self.required_min_raw_score:
                return False

        return True

    def check_unlocked(self, player, all_player_stats=None, achieved_title_ids=None):
        """現在条件または永続解除履歴により称号を使用できるか確認"""
        if self.is_permanent:
            if achieved_title_ids is None:
                achieved_title_ids = get_achieved_title_ids(player.id)
            if self.id in achieved_title_ids:
                return True

        return self.meets_conditions(player, all_player_stats=all_player_stats)


class PlayerTitleAchievement(db.Model):
    """プレイヤーが永続称号を解除した履歴"""
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=False)
    title_id = db.Column(db.Integer, db.ForeignKey('title.id'), nullable=False)
    unlocked_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        db.UniqueConstraint('player_id', 'title_id', name='uq_player_title_achievement'),
    )