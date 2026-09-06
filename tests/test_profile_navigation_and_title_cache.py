import unittest

from app import app, db, Player, Title, Game, build_player_navigation, get_unlocked_title_ids, save_uploaded_game_photo


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

    def test_save_uploaded_game_photo_returns_relative_path(self):
        player = Player(name='A')
        db.session.add(player)
        db.session.commit()

        game = Game(player1_id=player.id, player2_id=player.id, player3_id=player.id, player4_id=player.id,
                    score1=100000, score2=0, score3=0, score4=0)
        db.session.add(game)
        db.session.commit()

        file_storage = type('UploadedFile', (), {
            'filename': 'sample.png',
            'stream': __import__('io').BytesIO(b'fake-image-bytes'),
            'save': lambda self, path: None,
        })()

        saved_path = save_uploaded_game_photo(file_storage)

        self.assertTrue(saved_path.startswith('/static/game_photos/'))
        self.assertTrue(saved_path.endswith('.png'))


if __name__ == '__main__':
    unittest.main()
