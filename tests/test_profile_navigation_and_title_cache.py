import unittest

from app import app, db, Player, Title, build_player_navigation, get_unlocked_title_ids


class ProfileNavigationAndTitleCacheTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_build_player_navigation_returns_prev_and_next_player(self):
        players = [Player(name='A'), Player(name='B'), Player(name='C')]
        db.session.add_all(players)
        db.session.commit()

        nav = build_player_navigation(players, players[1].id)

        self.assertEqual(nav['prev_player'].id, players[0].id)
        self.assertEqual(nav['next_player'].id, players[2].id)

    def test_get_unlocked_title_ids_reuses_player_stats(self):
        player = Player(name='A')
        db.session.add(player)
        db.session.commit()

        title = Title(
            name='テスト称号',
            badge_filename='badge.png',
            required_total_games=0,
            required_avg_rank=4.1,
        )
        db.session.add(title)
        db.session.commit()

        unlocked = get_unlocked_title_ids(player)

        self.assertIn(title.id, unlocked)


if __name__ == '__main__':
    unittest.main()
