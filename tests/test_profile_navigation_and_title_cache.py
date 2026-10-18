import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# app import 時にエンジンが作られるため、import 前にテスト用DBを指定する（実DBを drop_all しないように）
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

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

    def test_login_page_has_no_public_registration_link(self):
        client = app.test_client()
        response = client.get('/login')
        html = response.data.decode('utf-8')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('新規ユーザー登録', html)
        self.assertNotIn('/register', html)

    def test_admin_titles_page_is_available_to_logged_in_admin(self):
        admin = User(username='admin')
        db.session.add(admin)
        db.session.commit()

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['login_user_id'] = admin.id
            sess['login_is_admin'] = True

        response = client.get('/admin/titles')
        html = response.data.decode('utf-8')

        self.assertEqual(response.status_code, 200)
        self.assertIn('称号', html)

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

    def test_register_start_uses_request_host_for_rp_id_when_env_unset(self):
        user = User(username='alice')
        db.session.add(user)
        db.session.commit()

        client = app.test_client()
        with patch.dict(os.environ, {'ALLOW_PUBLIC_REGISTRATION': 'True'}, clear=False):
            os.environ.pop('RP_ID', None)
            response = client.post(
                '/register_start',
                json={'username': 'alice'},
                base_url='https://example.com'
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['rp']['id'], 'example.com')

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

    def test_enrollment_flow_uses_session_username_without_form_input(self):
        user = User(username='alice')
        db.session.add(user)
        db.session.commit()

        token = 'demo-token'
        user.passkey_enrollment_token = token
        user.passkey_enrollment_expires_at = datetime.utcnow() + timedelta(hours=1)
        db.session.commit()

        client = app.test_client()
        self.assertEqual(client.get(f'/enroll/{token}').status_code, 200)

        response = client.post('/register_start', json={})

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'challenge', response.data)

    def _create_enrollment_user(self, username='alice', expires_in=timedelta(hours=1)):
        user = User(
            username=username,
            passkey_enrollment_token=f'{username}-token',
            passkey_enrollment_expires_at=datetime.utcnow() + expires_in,
        )
        db.session.add(user)
        db.session.commit()
        return user

    def test_enrollment_ignores_requested_username(self):
        user = self._create_enrollment_user()
        client = app.test_client()
        client.get(f'/enroll/{user.passkey_enrollment_token}')

        response = client.post('/register_start', json={'username': 'someone_else'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['user']['name'], 'alice')

    def test_enrollment_rejects_token_expired_after_opening_url(self):
        user = self._create_enrollment_user()
        client = app.test_client()
        client.get(f'/enroll/{user.passkey_enrollment_token}')
        user.passkey_enrollment_expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()

        response = client.post('/register_start', json={})

        self.assertEqual(response.status_code, 400)

    def test_register_finish_clears_enrollment_token(self):
        user = self._create_enrollment_user()
        client = app.test_client()
        client.get(f'/enroll/{user.passkey_enrollment_token}')
        client.post('/register_start', json={})

        class FakeCredentialData:
            credential_id = b'cred-id'

            def __bytes__(self):
                return b'attested-data'

        class FakeServer:
            def register_complete(self, state, response):
                return type('Auth', (), {'credential_data': FakeCredentialData(), 'counter': 0})()

        with patch('auth._make_webauthn_server', return_value=(FakeServer(), 'localhost')):
            response = client.post('/register_finish', json={
                'id': 'Y3JlZC1pZA', 'rawId': 'Y3JlZC1pZA',
                'clientDataJSON': 'e30', 'attestationObject': 'e30',
            })

        self.assertEqual(response.get_json(), {'status': 'ok'})
        db.session.expire_all()
        saved = User.query.filter_by(username='alice').first()
        self.assertIsNone(saved.passkey_enrollment_token)
        self.assertIsNone(saved.passkey_enrollment_expires_at)
        self.assertEqual(len(saved.credentials), 1)

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
