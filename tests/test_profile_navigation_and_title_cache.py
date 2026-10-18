import unittest
from pathlib import Path

from app import app, db, Player, Title, User, build_player_navigation, get_unlocked_title_ids


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

    def test_results_page_redirects_to_login_when_not_authenticated(self):
        client = app.test_client()

        response = client.get('/results')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers.get('Location', ''))

    def test_login_page_lists_registered_users(self):
        db.session.add_all([
            User(username='alice'),
            User(username='bob'),
        ])
        db.session.commit()

        client = app.test_client()
        response = client.get('/login')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'alice', response.data)
        self.assertIn(b'bob', response.data)

    def test_public_registration_is_disabled_by_default(self):
        client = app.test_client()

        response = client.get('/register')

        self.assertEqual(response.status_code, 403)

    def test_admin_user_is_available_as_default_admin_account(self):
        client = app.test_client()

        response = client.get('/login')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'admin', response.data)

    def test_admin_can_register_passkey_without_public_registration(self):
        client = app.test_client()

        response = client.post('/register_start', json={'username': 'admin'})

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'challenge', response.data)
        self.assertIn(b'rp', response.data)

    def test_admin_sees_user_management_link_after_login(self):
        admin = User(username='admin')
        db.session.add(admin)
        db.session.commit()

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['login_user_id'] = admin.id
            sess['login_is_admin'] = True

        response = client.get('/results')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'/admin/users', response.data)

    def test_schema_sql_includes_passkey_enrollment_columns(self):
        schema_sql = Path('db/schema.sql').read_text(encoding='utf-8')

        self.assertIn('passkey_enrollment_token', schema_sql)
        self.assertIn('passkey_enrollment_expires_at', schema_sql)

    def test_admin_can_create_member_user_account(self):
        admin = User(username='admin')
        db.session.add(admin)
        db.session.commit()

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['login_user_id'] = admin.id
            sess['login_is_admin'] = True

        response = client.post('/admin/users', data={'username': 'alice'}, follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(User.query.filter_by(username='alice').first())

    def test_admin_can_issue_passkey_enrollment_url(self):
        admin = User(username='admin')
        member = User(username='alice')
        db.session.add_all([admin, member])
        db.session.commit()

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['login_user_id'] = admin.id
            sess['login_is_admin'] = True

        response = client.post(f'/admin/users/{member.id}/passkey', follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(User.query.get(member.id).passkey_enrollment_token)

    def test_admin_can_delete_member_user_account(self):
        admin = User(username='admin')
        member = User(username='alice')
        db.session.add_all([admin, member])
        db.session.commit()

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['login_user_id'] = admin.id
            sess['login_is_admin'] = True

        response = client.post(f'/admin/users/{member.id}/delete', follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(User.query.filter_by(username='alice').first())


if __name__ == '__main__':
    unittest.main()
