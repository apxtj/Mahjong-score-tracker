import os
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

# app import 時にエンジンが作られるため、import 前にテスト用DBを指定する（実DBを drop_all しないように）
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from sqlalchemy import inspect as sa_inspect, text

from app import app, db, _ensure_added_columns, recalc_total_scores
from models import AppSetting, Credential, Game, Player, Title, User, compute_all_player_stats


def _add_game(players, scores, game_date=date(2026, 10, 1)):
    game = Game(
        date=game_date,
        player1_id=players[0].id, score1=scores[0],
        player2_id=players[1].id, score2=scores[1],
        player3_id=players[2].id, score3=scores[2],
        player4_id=players[3].id, score4=scores[3],
        oka=0, uma1=0, uma2=0, uma3=0, uma4=0,
    )
    db.session.add(game)
    db.session.commit()
    return game


class UsabilityFeatureTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _players(self, count=4):
        players = [Player(name=f'P{i}') for i in range(1, count + 1)]
        db.session.add_all(players)
        db.session.commit()
        return players

    def _login(self, client, user, is_admin=False):
        with client.session_transaction() as sess:
            sess['login_user_id'] = user.id
            sess['login_is_admin'] = is_admin

    # --- ログイン（ユーザー名なし・ログイン保持） ---

    def _add_credential(self, user, credential_id):
        db.session.add(Credential(user_id=user.id, credential_id=credential_id, public_key=b'pk'))
        db.session.commit()

    def test_one_tap_login_offers_member_passkeys_but_not_admin(self):
        admin = User(username='admin')
        alice = User(username='alice')
        bob = User(username='bob')
        db.session.add_all([admin, alice, bob])
        db.session.commit()
        self._add_credential(admin, b'cred-admin')
        self._add_credential(alice, b'cred-alice')
        self._add_credential(bob, b'cred-bob')

        response = app.test_client().post('/login_start', json={})

        self.assertEqual(response.status_code, 200)
        from fido2.utils import websafe_encode
        offered = {c['id'] for c in response.get_json()['allowCredentials']}
        self.assertEqual(offered, {websafe_encode(b'cred-alice'), websafe_encode(b'cred-bob')})

    def test_one_tap_login_offers_admin_when_no_member_passkeys_exist(self):
        admin = User(username='admin')
        db.session.add(admin)
        db.session.commit()
        self._add_credential(admin, b'cred-admin')

        response = app.test_client().post('/login_start', json={})

        from fido2.utils import websafe_encode
        self.assertEqual(
            [c['id'] for c in response.get_json()['allowCredentials']],
            [websafe_encode(b'cred-admin')],
        )

    def test_one_tap_login_rejects_admin_passkey_when_members_exist(self):
        admin = User(username='admin')
        alice = User(username='alice')
        db.session.add_all([admin, alice])
        db.session.commit()
        self._add_credential(admin, b'cred-admin')
        self._add_credential(alice, b'cred-alice')

        client = app.test_client()
        client.post('/login_start', json={})
        response = self._finish_login(client, raw_id=b'cred-admin')

        self.assertEqual(response.status_code, 400)

    def test_admin_can_still_login_by_choosing_admin_username(self):
        admin = User(username='admin')
        alice = User(username='alice')
        db.session.add_all([admin, alice])
        db.session.commit()
        self._add_credential(admin, b'cred-admin')
        self._add_credential(alice, b'cred-alice')

        client = app.test_client()
        with client.session_transaction() as sess:
            # login_start(username='admin') 後と同じセッション状態
            sess['auth_state'] = {'challenge': 'x'}
            sess['auth_user_id'] = admin.id
        response = self._finish_login(client, raw_id=b'cred-admin')

        self.assertEqual(response.get_json(), {'status': 'ok'})
        with client.session_transaction() as sess:
            self.assertTrue(sess['login_is_admin'])

    def _finish_login(self, client, raw_id=b'cred-alice'):
        fake_server = MagicMock()
        with patch('auth._make_webauthn_server', return_value=(fake_server, 'localhost')), \
                patch('auth.AttestedCredentialData'), patch('auth.CollectedClientData'), \
                patch('auth.AuthenticatorData'), patch('auth.AuthenticatorAssertionResponse'), \
                patch('auth.AuthenticationResponseWA'):
            from base64 import urlsafe_b64encode
            encoded = urlsafe_b64encode(raw_id).decode().rstrip('=')
            return client.post('/login_finish', json={
                'rawId': encoded, 'clientDataJSON': 'e30',
                'authenticatorData': 'e30', 'signature': 'e30',
            })

    def test_usernameless_login_finds_user_by_credential_and_keeps_session(self):
        alice = User(username='alice')
        db.session.add(alice)
        db.session.flush()
        db.session.add(Credential(user_id=alice.id, credential_id=b'cred-alice', public_key=b'pk'))
        db.session.commit()

        client = app.test_client()
        client.post('/login_start', json={})
        response = self._finish_login(client)

        self.assertEqual(response.get_json(), {'status': 'ok'})
        with client.session_transaction() as sess:
            self.assertEqual(sess['login_user_id'], alice.id)
            self.assertTrue(sess.permanent)

    def test_login_with_username_rejects_other_users_passkey(self):
        alice = User(username='alice')
        bob = User(username='bob')
        db.session.add_all([alice, bob])
        db.session.flush()
        db.session.add_all([
            Credential(user_id=alice.id, credential_id=b'cred-alice', public_key=b'pk'),
            Credential(user_id=bob.id, credential_id=b'cred-bob', public_key=b'pk'),
        ])
        db.session.commit()

        client = app.test_client()
        # login_start(username='alice') 後と同じセッション状態
        with client.session_transaction() as sess:
            sess['auth_state'] = {'challenge': 'x'}
            sess['auth_user_id'] = alice.id
        response = self._finish_login(client, raw_id=b'cred-bob')

        self.assertEqual(response.status_code, 400)
        with client.session_transaction() as sess:
            self.assertNotIn('login_user_id', sess)

    def test_register_start_requests_discoverable_passkey_in_webauthn_format(self):
        client = app.test_client()

        payload = client.post('/register_start', json={'username': 'admin'}).get_json()

        self.assertEqual(payload['authenticatorSelection']['residentKey'], 'required')
        self.assertTrue(payload['authenticatorSelection']['requireResidentKey'])
        self.assertEqual(payload['attestation'], 'none')

    def test_enrollment_url_allows_reregistering_user_with_existing_passkey(self):
        admin = User(username='admin')
        alice = User(username='alice')
        db.session.add_all([admin, alice])
        db.session.flush()
        db.session.add(Credential(user_id=alice.id, credential_id=b'old', public_key=b'pk'))
        db.session.commit()

        admin_client = app.test_client()
        self._login(admin_client, admin, is_admin=True)
        admin_client.post(f'/admin/users/{alice.id}/passkey')
        token = db.session.get(User, alice.id).passkey_enrollment_token
        self.assertIsNotNone(token)

        client = app.test_client()
        client.get(f'/enroll/{token}')
        self.assertEqual(client.post('/register_start', json={}).status_code, 200)

        # 登録URLなしでは、登録済みユーザーの Passkey は追加できない
        self.assertEqual(app.test_client().post('/register_start', json={'username': 'alice'}).status_code, 403)

    def test_admin_can_issue_enrollment_url_for_self(self):
        admin = User(username='admin')
        db.session.add(admin)
        db.session.commit()

        client = app.test_client()
        self._login(client, admin, is_admin=True)
        client.post(f'/admin/users/{admin.id}/passkey')

        self.assertIsNotNone(db.session.get(User, admin.id).passkey_enrollment_token)

    def test_login_page_shows_admin_setup_only_until_admin_registers(self):
        client = app.test_client()
        self.assertIn('管理者の Passkey を登録', client.get('/login').data.decode('utf-8'))

        admin = User.query.filter_by(username='admin').first()
        db.session.add(Credential(user_id=admin.id, credential_id=b'cred-admin', public_key=b'pk'))
        db.session.commit()

        self.assertNotIn('管理者の Passkey を登録', client.get('/login').data.decode('utf-8'))

    # --- 点数入力 ---

    def test_score_input_in_hundreds_is_saved_as_points_and_rules_are_shared(self):
        players = self._players()
        user = User(username='alice')
        db.session.add(user)
        db.session.commit()

        client = app.test_client()
        self._login(client, user)
        response = client.post('/', data={
            'score_unit': '100',
            'player1_id': players[0].id, 'score1': '400',
            'player2_id': players[1].id, 'score2': '300',
            'player3_id': players[2].id, 'score3': '200',
            'player4_id': players[3].id, 'score4': '100',
            'oka': '0', 'uma': '10000,5000,-5000,-10000',
        })

        self.assertEqual(response.status_code, 302)
        game = Game.query.one()
        self.assertEqual(
            [game.score1, game.score2, game.score3, game.score4],
            [50000, 35000, 15000, 0],
        )
        self.assertEqual(AppSetting.get_value('rule_uma'), '10000,5000,-5000,-10000')

        # 別の端末（別セッション）でも同じルールが初期表示される
        other_client = app.test_client()
        self._login(other_client, user)
        html = other_client.get('/').data.decode('utf-8')
        self.assertIn('value="10000,5000,-5000,-10000"', html)

    def test_score_total_check_uses_converted_points(self):
        players = self._players()
        user = User(username='alice')
        db.session.add(user)
        db.session.commit()

        client = app.test_client()
        self._login(client, user)
        response = client.post('/', data={
            'score_unit': '100',
            'player1_id': players[0].id, 'score1': '400',
            'player2_id': players[1].id, 'score2': '300',
            'player3_id': players[2].id, 'score3': '200',
            'player4_id': players[3].id, 'score4': '50',
            'oka': '0', 'uma': '0,0,0,0',
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn('95,000', response.data.decode('utf-8'))
        self.assertEqual(Game.query.count(), 0)

    # --- ユーザーとプレイヤーの紐付け ---

    def test_admin_links_user_to_player_and_user_sees_my_page(self):
        players = self._players()
        admin = User(username='admin')
        alice = User(username='alice')
        db.session.add_all([admin, alice])
        db.session.commit()

        admin_client = app.test_client()
        self._login(admin_client, admin, is_admin=True)
        response = admin_client.post(f'/admin/users/{alice.id}/player', data={'player_id': players[1].id})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.get(User, alice.id).player_id, players[1].id)

        alice_client = app.test_client()
        self._login(alice_client, alice)
        html = alice_client.get('/').data.decode('utf-8')
        self.assertIn(f'/profile/{players[1].id}', html)
        self.assertIn(f'<option value="{players[1].id}" selected', html)

    def test_player_cannot_be_linked_to_two_users(self):
        players = self._players()
        admin = User(username='admin')
        alice = User(username='alice', player_id=players[0].id)
        bob = User(username='bob')
        db.session.add_all([admin, alice, bob])
        db.session.commit()

        client = app.test_client()
        self._login(client, admin, is_admin=True)
        client.post(f'/admin/users/{bob.id}/player', data={'player_id': players[0].id})

        self.assertIsNone(db.session.get(User, bob.id).player_id)

    def test_title_update_is_limited_to_own_player(self):
        players = self._players()
        alice = User(username='alice', player_id=players[0].id)
        bob = User(username='bob', player_id=players[1].id)
        db.session.add_all([alice, bob])
        db.session.commit()

        anonymous = app.test_client()
        response = anonymous.post(f'/api/player/{players[0].id}/title/update', json={'slot': 1, 'title_id': None})
        self.assertEqual(response.status_code, 302)

        client = app.test_client()
        self._login(client, bob)
        response = client.post(f'/api/player/{players[0].id}/title/update', json={'slot': 1, 'title_id': None})
        self.assertEqual(response.status_code, 403)

        response = client.post(f'/api/player/{players[1].id}/title/update', json={'slot': 1, 'title_id': None})
        self.assertEqual(response.status_code, 200)

    def test_deleting_player_unlinks_user(self):
        players = self._players(1)
        alice = User(username='alice', player_id=players[0].id)
        db.session.add(alice)
        db.session.commit()

        client = app.test_client()
        self._login(client, alice)
        client.post(f'/delete_player/{players[0].id}')

        self.assertIsNone(db.session.get(User, alice.id).player_id)

    # --- 速度改善（結果が変わらないこと） ---

    def test_bulk_stats_match_per_player_stats(self):
        players = self._players(5)
        _add_game(players[:4], [40000, 30000, 20000, 10000], date(2026, 10, 1))
        _add_game([players[4], players[0], players[1], players[2]], [25000, 25000, 30000, 20000], date(2026, 10, 1))
        _add_game([players[3], players[2], players[1], players[0]], [50000, 30000, 20000, 0], date(2026, 10, 2))

        bulk = compute_all_player_stats()

        for player in players:
            self.assertEqual(bulk[player.id], player.calculate_stats())

    def test_recalc_total_scores_after_game_deleted(self):
        players = self._players()
        _add_game(players, [40000, 30000, 20000, 10000])
        second = _add_game(players, [10000, 20000, 30000, 40000])
        recalc_total_scores()
        self.assertEqual([p.total_score for p in players], [50000, 50000, 50000, 50000])

        db.session.delete(second)
        db.session.commit()
        recalc_total_scores()

        self.assertEqual([p.total_score for p in players], [40000, 30000, 20000, 10000])

    def test_profile_computes_game_stats_once(self):
        players = self._players()
        _add_game(players, [40000, 30000, 20000, 10000])
        for i in range(3):
            db.session.add(Title(name=f'永続{i}', badge_filename='b.png', is_permanent=True))
        db.session.commit()
        user = User(username='alice')
        db.session.add(user)
        db.session.commit()

        client = app.test_client()
        self._login(client, user)
        with patch('models.compute_all_player_stats', wraps=compute_all_player_stats) as models_spy, \
                patch('app.compute_all_player_stats', wraps=compute_all_player_stats) as app_spy:
            response = client.get(f'/profile/{players[0].id}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(app_spy.call_count + models_spy.call_count, 1)

    # --- 既存DBへの列追加 ---

    def test_missing_user_columns_are_added_on_startup(self):
        db.session.execute(text('DROP TABLE "user"'))
        db.session.execute(text('CREATE TABLE "user" (id INTEGER PRIMARY KEY, username VARCHAR(50) NOT NULL UNIQUE)'))
        db.session.commit()

        _ensure_added_columns()

        columns = {c['name'] for c in sa_inspect(db.engine).get_columns('user')}
        self.assertTrue({'player_id', 'passkey_enrollment_token', 'passkey_enrollment_expires_at'} <= columns)


if __name__ == '__main__':
    unittest.main()
