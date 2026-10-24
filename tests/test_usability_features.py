import os
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

# app import 時にエンジンが作られるため、import 前にテスト用DBを指定する（実DBを drop_all しないように）
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from sqlalchemy import inspect as sa_inspect, text

from app import app, db, _ensure_added_columns, rank_entries, recalc_total_scores
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

    def test_expired_enrollment_url_shows_expired_page(self):
        from datetime import datetime, timedelta
        alice = User(
            username='alice',
            passkey_enrollment_token='old-token',
            passkey_enrollment_expires_at=datetime.utcnow() - timedelta(minutes=1),
        )
        db.session.add(alice)
        db.session.commit()

        response = app.test_client().get('/enroll/old-token')

        self.assertEqual(response.status_code, 410)
        html = response.data.decode('utf-8')
        self.assertIn('登録URLの有効期限が切れています', html)
        self.assertIn('再発行を依頼', html)

    def test_unknown_or_used_enrollment_url_shows_invalid_page(self):
        response = app.test_client().get('/enroll/no-such-token')

        self.assertEqual(response.status_code, 404)
        self.assertIn('この登録URLは使えません', response.data.decode('utf-8'))

    def test_enrollment_expiring_after_opening_url_returns_japanese_message(self):
        from datetime import datetime, timedelta
        alice = User(
            username='alice',
            passkey_enrollment_token='tok',
            passkey_enrollment_expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        db.session.add(alice)
        db.session.commit()
        client = app.test_client()
        client.get('/enroll/tok')
        alice.passkey_enrollment_expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()

        response = client.post('/register_start', json={})

        self.assertEqual(response.status_code, 400)
        self.assertIn('有効期限が切れた', response.get_json()['error'])

    # --- 戦績の修正・削除と権限 ---

    def _game_setup(self):
        players = self._players(5)
        game = _add_game(players[:4], [40000, 30000, 20000, 10000], date(2026, 10, 1))
        # ウマ・オカ付きの保存状態にする（素点 400/300/200/100、ウマ 20/10/-10/-20、オカ 0）
        game.score1, game.score2, game.score3, game.score4 = 60000, 40000, 10000, -10000
        game.uma1, game.uma2, game.uma3, game.uma4 = 20000, 10000, -10000, -20000
        db.session.commit()
        recalc_total_scores()
        admin = User(username='admin')
        editor = User(username='editor', can_edit_games=True)
        member = User(username='member')
        db.session.add_all([admin, editor, member])
        db.session.commit()
        return players, game, admin, editor, member

    def test_edit_form_shows_raw_scores_date_and_rules_of_the_game(self):
        players, game, admin, _, _ = self._game_setup()
        client = app.test_client()
        self._login(client, admin, is_admin=True)

        html = client.get(f'/games/{game.id}/edit').data.decode('utf-8')

        self.assertIn('戦績の修正', html)
        self.assertIn('value="2026-10-01"', html)
        for i, raw in enumerate([400, 300, 200, 100], start=1):
            self.assertIn(f'name="score{i}"', html)
            self.assertIn(f'value="{raw}"', html.split(f'name="score{i}"')[1].split('>')[0])
        self.assertIn('value="20000,10000,-10000,-20000"', html)
        self.assertIn(f'value="{players[0].id},{players[1].id},{players[2].id},{players[3].id}"', html)

    def test_editing_game_updates_scores_date_and_totals(self):
        players, game, _, editor, _ = self._game_setup()
        AppSetting.set_value('rule_uma', '5000,0,0,-5000')
        db.session.commit()
        client = app.test_client()
        self._login(client, editor)

        # P5 に入れ替え、点数と日付も直す
        response = client.post(f'/games/{game.id}/edit', data={
            'score_unit': '100', 'oka': '0', 'uma': '20000,10000,-10000,-20000', 'tie_order': '',
            'game_date': '2026-09-30',
            'player1_id': players[4].id, 'score1': '100',
            'player2_id': players[1].id, 'score2': '500',
            'player3_id': players[2].id, 'score3': '250',
            'player4_id': players[3].id, 'score4': '150',
        })

        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        game = db.session.get(Game, game.id)
        self.assertEqual(game.date, date(2026, 9, 30))
        self.assertEqual(
            [game.player1_id, game.player2_id, game.player3_id, game.player4_id],
            [players[1].id, players[2].id, players[3].id, players[4].id],
        )
        self.assertEqual([game.score1, game.score2, game.score3, game.score4], [70000, 35000, 5000, -10000])
        totals = {p.id: db.session.get(Player, p.id).total_score for p in players}
        self.assertEqual(totals[players[0].id], 0)
        self.assertEqual(totals[players[4].id], -10000)
        # 修正では全員共通のルール設定は変わらない
        self.assertEqual(AppSetting.get_value('rule_uma'), '5000,0,0,-5000')

    def test_editing_into_a_tie_requires_tie_order(self):
        players, game, admin, _, _ = self._game_setup()
        client = app.test_client()
        self._login(client, admin, is_admin=True)
        data = {
            'score_unit': '100', 'oka': '0', 'uma': '20000,10000,-10000,-20000', 'tie_order': '',
            'game_date': '2026-10-01',
            'player1_id': players[0].id, 'score1': '300',
            'player2_id': players[1].id, 'score2': '300',
            'player3_id': players[2].id, 'score3': '200',
            'player4_id': players[3].id, 'score4': '200',
        }

        response = client.post(f'/games/{game.id}/edit', data=data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('起家に近い人', response.data.decode('utf-8'))

        data['tie_order'] = f'{players[1].id},{players[0].id},{players[3].id},{players[2].id}'
        self.assertEqual(client.post(f'/games/{game.id}/edit', data=data).status_code, 302)
        db.session.expire_all()
        game = db.session.get(Game, game.id)
        self.assertEqual(
            [game.player1_id, game.player2_id, game.player3_id, game.player4_id],
            [players[1].id, players[0].id, players[3].id, players[2].id],
        )

    def test_member_without_permission_cannot_edit_or_delete(self):
        _, game, _, _, member = self._game_setup()
        client = app.test_client()
        self._login(client, member)

        self.assertEqual(client.get(f'/games/{game.id}/edit').status_code, 403)
        self.assertEqual(client.post(f'/delete_game/{game.id}').status_code, 403)
        self.assertEqual(Game.query.count(), 1)
        html = client.get('/results').data.decode('utf-8')
        self.assertNotIn(f'/games/{game.id}/edit', html)
        self.assertNotIn(f'/delete_game/{game.id}', html)

    def test_permitted_user_and_admin_see_edit_and_delete_buttons(self):
        _, game, admin, editor, _ = self._game_setup()
        for user, is_admin in [(admin, True), (editor, False)]:
            client = app.test_client()
            self._login(client, user, is_admin=is_admin)
            html = client.get('/results').data.decode('utf-8')
            self.assertIn(f'/games/{game.id}/edit', html)
            self.assertIn(f'/delete_game/{game.id}', html)

    def test_permitted_user_can_delete_game_with_photo(self):
        from models import GamePhoto
        _, game, _, editor, _ = self._game_setup()
        db.session.add(GamePhoto(game_id=game.id, image_url='/static/x.png'))
        db.session.commit()
        client = app.test_client()
        self._login(client, editor)

        response = client.post(f'/delete_game/{game.id}')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Game.query.count(), 0)
        self.assertEqual(GamePhoto.query.count(), 0)

    def test_admin_grants_and_revokes_game_permission(self):
        _, _, admin, _, member = self._game_setup()
        client = app.test_client()
        self._login(client, admin, is_admin=True)

        client.post(f'/admin/users/{member.id}/game_permission', data={'can_edit_games': '1'})
        self.assertTrue(db.session.get(User, member.id).can_edit_games)

        client.post(f'/admin/users/{member.id}/game_permission', data={})
        db.session.expire_all()
        self.assertFalse(db.session.get(User, member.id).can_edit_games)

    def test_non_admin_cannot_grant_game_permission(self):
        _, _, _, editor, member = self._game_setup()
        client = app.test_client()
        self._login(client, editor)

        response = client.post(f'/admin/users/{member.id}/game_permission', data={'can_edit_games': '1'})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(db.session.get(User, member.id).can_edit_games)

    def test_invalid_score_input_is_shown_again_without_crashing(self):
        players = self._players()
        user = User(username='alice')
        db.session.add(user)
        db.session.commit()
        client = app.test_client()
        self._login(client, user)

        response = client.post('/', data={
            'score_unit': '100', 'oka': '0', 'uma': '0,0,0,0',
            'player1_id': players[0].id, 'score1': 'abc',
            'player2_id': players[1].id, 'score2': '300',
            'player3_id': players[2].id, 'score3': '200',
            'player4_id': players[3].id, 'score4': '100',
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn('整数で入力', response.data.decode('utf-8'))

    # --- 称号管理の認証（パスワードログインなし） ---

    def test_admin_titles_rejects_password_login(self):
        import base64
        credentials = base64.b64encode(b'admin:secret').decode()
        with patch.dict(os.environ, {'ADMIN_PASSWORD': 'secret'}):
            response = app.test_client().get('/admin/titles', headers={'Authorization': f'Basic {credentials}'})

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_admin_titles_forbidden_for_non_admin_user(self):
        alice = User(username='alice')
        db.session.add(alice)
        db.session.commit()
        client = app.test_client()
        self._login(client, alice)

        self.assertEqual(client.get('/admin/titles').status_code, 403)

    def test_admin_titles_requires_admin_user_not_just_session_flag(self):
        alice = User(username='alice')
        db.session.add(alice)
        db.session.commit()
        client = app.test_client()
        self._login(client, alice, is_admin=True)

        self.assertEqual(client.get('/admin/titles').status_code, 403)

    # --- LINE アプリ内ブラウザ対策 ---

    LINE_UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
               '(KHTML, like Gecko) Mobile/15E148 Safari Line/14.10.0')
    SAFARI_UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
                 '(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1')

    def test_issued_enrollment_url_opens_in_external_browser_from_line(self):
        admin = User(username='admin')
        alice = User(username='alice')
        db.session.add_all([admin, alice])
        db.session.commit()

        client = app.test_client()
        self._login(client, admin, is_admin=True)
        response = client.post(f'/admin/users/{alice.id}/passkey', follow_redirects=True)

        token = db.session.get(User, alice.id).passkey_enrollment_token
        self.assertIn(f'/enroll/{token}?openExternalBrowser=1', response.data.decode('utf-8'))

    def test_enrollment_url_with_external_browser_param_still_works(self):
        from datetime import datetime, timedelta
        db.session.add(User(
            username='alice', passkey_enrollment_token='tok',
            passkey_enrollment_expires_at=datetime.utcnow() + timedelta(hours=1),
        ))
        db.session.commit()

        response = app.test_client().get('/enroll/tok?openExternalBrowser=1')

        self.assertEqual(response.status_code, 200)

    def test_line_in_app_browser_shows_notice_on_register_and_login(self):
        from datetime import datetime, timedelta
        db.session.add(User(
            username='alice', passkey_enrollment_token='tok',
            passkey_enrollment_expires_at=datetime.utcnow() + timedelta(hours=1),
        ))
        db.session.commit()
        client = app.test_client()

        for path in ['/enroll/tok', '/login']:
            line_html = client.get(path, headers={'User-Agent': self.LINE_UA}).data.decode('utf-8')
            safari_html = client.get(path, headers={'User-Agent': self.SAFARI_UA}).data.decode('utf-8')
            self.assertIn('LINE の中では Passkey を使えません', line_html, path)
            self.assertNotIn('LINE の中では Passkey を使えません', safari_html, path)

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

    # --- 座順なしの入力・同点の扱い ---

    def test_rank_entries_ignores_input_order_without_ties(self):
        entries = [{'id': 1, 'score': 10000}, {'id': 2, 'score': 40000}, {'id': 3, 'score': 30000}, {'id': 4, 'score': 20000}]

        ranked = rank_entries(entries, [])

        self.assertEqual([e['id'] for e in ranked], [2, 3, 4, 1])

    def test_rank_entries_uses_tie_order_only_for_tied_players(self):
        entries = [{'id': 1, 'score': 25000}, {'id': 2, 'score': 40000}, {'id': 3, 'score': 25000}, {'id': 4, 'score': 10000}]

        ranked = rank_entries(entries, [3, 1])

        self.assertEqual([e['id'] for e in ranked], [2, 3, 1, 4])

    def test_rank_entries_handles_three_way_tie(self):
        entries = [{'id': 1, 'score': 30000}, {'id': 2, 'score': 30000}, {'id': 3, 'score': 30000}, {'id': 4, 'score': 10000}]

        ranked = rank_entries(entries, [2, 3, 1])

        self.assertEqual([e['id'] for e in ranked], [2, 3, 1, 4])

    def test_rank_entries_requires_tie_order_when_tied(self):
        entries = [{'id': 1, 'score': 25000}, {'id': 2, 'score': 40000}, {'id': 3, 'score': 25000}, {'id': 4, 'score': 10000}]

        with self.assertRaises(ValueError):
            rank_entries(entries, [])
        with self.assertRaises(ValueError):
            rank_entries(entries, [3])

    def _post_game(self, client, players, scores, tie_order=''):
        data = {'score_unit': '100', 'oka': '0', 'uma': '20000,10000,-10000,-20000', 'tie_order': tie_order}
        for i, (player, score) in enumerate(zip(players, scores), start=1):
            data[f'player{i}_id'] = player.id
            data[f'score{i}'] = str(score)
        return client.post('/', data=data)

    def test_tied_game_is_rejected_without_tie_order_and_saved_with_it(self):
        players = self._players()
        user = User(username='alice')
        db.session.add(user)
        db.session.commit()
        client = app.test_client()
        self._login(client, user)

        response = self._post_game(client, players, [250, 400, 250, 100])
        self.assertEqual(response.status_code, 200)
        self.assertIn('起家に近い人', response.data.decode('utf-8'))
        self.assertEqual(Game.query.count(), 0)

        response = self._post_game(client, players, [250, 400, 250, 100], tie_order=f'{players[2].id},{players[0].id}')
        self.assertEqual(response.status_code, 302)
        game = Game.query.one()
        self.assertEqual(
            [game.player1_id, game.player2_id, game.player3_id, game.player4_id],
            [players[1].id, players[2].id, players[0].id, players[3].id],
        )
        self.assertEqual([game.score2, game.score3], [25000 + 10000, 25000 - 10000])

    def test_next_game_form_carries_over_todays_players_in_same_order(self):
        players = self._players(5)
        user = User(username='alice', player_id=players[4].id)
        db.session.add(user)
        db.session.commit()
        client = app.test_client()
        self._login(client, user)

        # 入力順 P2, P1, P4, P3 で保存（順位順は P1, P2, P3, P4）
        entered = [players[1], players[0], players[3], players[2]]
        self._post_game(client, entered, [300, 400, 100, 200])

        html = client.get('/').data.decode('utf-8')
        for i, player in enumerate(entered, start=1):
            select_html = html.split(f'name="player{i}_id"')[1].split('</select>')[0]
            self.assertIn(f'<option value="{player.id}" selected', select_html)

        # 別の端末（並び順の記憶なし）では、直前の対局の順位順で4人が入る
        other = app.test_client()
        self._login(other, user)
        html = other.get('/').data.decode('utf-8')
        first_select = html.split('name="player1_id"')[1].split('</select>')[0]
        self.assertIn(f'<option value="{players[0].id}" selected', first_select)

    def test_first_game_of_day_preselects_only_own_player(self):
        players = self._players(5)
        _add_game(players[:4], [40000, 30000, 20000, 10000], date(2020, 1, 1))
        user = User(username='alice', player_id=players[4].id)
        db.session.add(user)
        db.session.commit()
        client = app.test_client()
        self._login(client, user)

        html = client.get('/').data.decode('utf-8')

        selected_players = [p for p in players if f'<option value="{p.id}" selected' in html]
        self.assertEqual(selected_players, [players[4]])

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

    # --- 本人による Passkey 追加 ---

    def _finish_registration(self, client, credential_id=b'new-cred'):
        class FakeCredentialData:
            def __init__(self):
                self.credential_id = credential_id

            def __bytes__(self):
                return b'attested-data'

        class FakeServer:
            def register_complete(self, state, response):
                return type('Auth', (), {'credential_data': FakeCredentialData(), 'counter': 0})()

        with patch('auth._make_webauthn_server', return_value=(FakeServer(), 'localhost')):
            return client.post('/register_finish', json={
                'id': 'bmV3', 'rawId': 'bmV3', 'clientDataJSON': 'e30', 'attestationObject': 'e30',
            })

    def test_account_passkeys_page_requires_login(self):
        response = app.test_client().get('/account/passkeys')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_logged_in_user_can_add_passkey_to_own_account(self):
        alice = User(username='alice', passkey_enrollment_token='keep-me')
        db.session.add(alice)
        db.session.commit()
        self._add_credential(alice, b'old-cred')

        client = app.test_client()
        self._login(client, alice)
        self.assertIn('登録済みの Passkey（1 件）', client.get('/account/passkeys').data.decode('utf-8'))

        start = client.post('/register_start', json={'add_to_current_user': True})
        self.assertEqual(start.status_code, 200)
        self.assertEqual(start.get_json()['user']['name'], 'alice')
        self.assertEqual(self._finish_registration(client).get_json(), {'status': 'ok'})

        db.session.expire_all()
        saved = db.session.get(User, alice.id)
        self.assertEqual(len(saved.credentials), 2)
        self.assertIsNotNone(Credential.query.filter_by(credential_id=b'new-cred').one().created_at)
        # 本人追加では、管理者が発行した登録URLには触れない
        self.assertEqual(saved.passkey_enrollment_token, 'keep-me')

    def test_self_add_requires_login(self):
        response = app.test_client().post('/register_start', json={'add_to_current_user': True})

        self.assertEqual(response.status_code, 401)

    def test_self_add_fails_if_logged_out_before_finish(self):
        alice = User(username='alice')
        db.session.add(alice)
        db.session.commit()

        client = app.test_client()
        self._login(client, alice)
        client.post('/register_start', json={'add_to_current_user': True})
        client.get('/logout')

        response = self._finish_registration(client)

        self.assertNotEqual(response.get_json(), {'status': 'ok'})
        self.assertEqual(Credential.query.count(), 0)

    # --- Passkey の最終使用日時・管理者による削除 ---

    def test_login_records_last_used_at(self):
        alice = User(username='alice')
        db.session.add(alice)
        db.session.commit()
        self._add_credential(alice, b'cred-alice')

        client = app.test_client()
        client.post('/login_start', json={})
        self._finish_login(client, raw_id=b'cred-alice')

        db.session.expire_all()
        self.assertIsNotNone(Credential.query.filter_by(credential_id=b'cred-alice').one().last_used_at)

    def test_admin_can_delete_single_passkey(self):
        admin = User(username='admin')
        alice = User(username='alice')
        db.session.add_all([admin, alice])
        db.session.commit()
        self._add_credential(alice, b'cred-1')
        self._add_credential(alice, b'cred-2')
        target = Credential.query.filter_by(credential_id=b'cred-1').one()

        client = app.test_client()
        self._login(client, admin, is_admin=True)
        html = client.get('/admin/users').data.decode('utf-8')
        self.assertIn(f'/admin/credentials/{target.id}/delete', html)

        client.post(f'/admin/credentials/{target.id}/delete')

        self.assertEqual([c.credential_id for c in Credential.query.all()], [b'cred-2'])

    def test_admin_last_passkey_cannot_be_deleted(self):
        admin = User(username='admin')
        db.session.add(admin)
        db.session.commit()
        self._add_credential(admin, b'cred-admin')
        credential = Credential.query.one()

        client = app.test_client()
        self._login(client, admin, is_admin=True)
        client.post(f'/admin/credentials/{credential.id}/delete')

        self.assertEqual(Credential.query.count(), 1)

    def test_non_admin_cannot_delete_passkey(self):
        alice = User(username='alice')
        db.session.add(alice)
        db.session.commit()
        self._add_credential(alice, b'cred-1')
        credential = Credential.query.one()

        client = app.test_client()
        self._login(client, alice)
        response = client.post(f'/admin/credentials/{credential.id}/delete')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Credential.query.count(), 1)

    # --- 既存DBへの列追加 ---

    def test_missing_credential_columns_are_added_on_startup(self):
        db.session.execute(text('DROP TABLE credential'))
        db.session.execute(text(
            'CREATE TABLE credential (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, '
            'credential_id BLOB NOT NULL UNIQUE, public_key BLOB NOT NULL, sign_count INTEGER)'
        ))
        db.session.commit()

        _ensure_added_columns()

        columns = {c['name'] for c in sa_inspect(db.engine).get_columns('credential')}
        self.assertTrue({'created_at', 'last_used_at'} <= columns)

    def test_missing_user_columns_are_added_on_startup(self):
        db.session.execute(text('DROP TABLE "user"'))
        db.session.execute(text('CREATE TABLE "user" (id INTEGER PRIMARY KEY, username VARCHAR(50) NOT NULL UNIQUE)'))
        db.session.commit()

        _ensure_added_columns()

        columns = {c['name'] for c in sa_inspect(db.engine).get_columns('user')}
        self.assertTrue({'player_id', 'passkey_enrollment_token', 'passkey_enrollment_expires_at'} <= columns)


if __name__ == '__main__':
    unittest.main()
