import json
import os
import unittest

# app import 時にエンジンが作られるため、import 前にテスト用DBを指定する（実DBを drop_all しないように）
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import app, db
from live import LiveError, apply_action, hand_points, new_state, round_label
from models import LiveTable, Player, User


def _state(scores=None, round_index=0, honba=0, kyotaku=0):
    state = new_state([{"id": i + 1, "name": name} for i, name in enumerate(["東", "南", "西", "北"])])
    if scores:
        state["scores"] = scores
    state["round"] = round_index
    state["honba"] = honba
    state["kyotaku"] = kyotaku
    return state


class HandPointsTests(unittest.TestCase):
    """早見表の値と一致すること"""

    def test_child_ron(self):
        cases = [((1, 30), 1000), ((2, 25), 1600), ((3, 30), 3900), ((4, 30), 7700), ((4, 40), 8000),
                 ((5, 30), 8000), ((6, 30), 12000), ((8, 30), 16000), ((11, 30), 24000), ((13, 30), 32000)]
        for (han, fu), expected in cases:
            self.assertEqual(hand_points(han, fu, False)["ron"], expected, (han, fu))

    def test_dealer_ron(self):
        cases = [((1, 30), 1500), ((3, 30), 5800), ((4, 30), 11600), ((5, 30), 12000), ((13, 30), 48000)]
        for (han, fu), expected in cases:
            self.assertEqual(hand_points(han, fu, True)["ron"], expected, (han, fu))

    def test_child_tsumo(self):
        cases = [((1, 30), (300, 500)), ((2, 20), (400, 700)), ((3, 30), (1000, 2000)), ((5, 30), (2000, 4000))]
        for (han, fu), (child, dealer) in cases:
            result = hand_points(han, fu, False)
            self.assertEqual((result["tsumo_child"], result["tsumo_dealer"]), (child, dealer), (han, fu))

    def test_dealer_tsumo(self):
        cases = [((1, 30), 500), ((3, 40), 2600), ((5, 30), 4000), ((13, 30), 16000)]
        for (han, fu), expected in cases:
            self.assertEqual(hand_points(han, fu, True)["tsumo_each"], expected, (han, fu))

    def test_invalid_fu_is_rejected(self):
        with self.assertRaises(LiveError):
            hand_points(2, 35, False)


class ApplyActionTests(unittest.TestCase):
    def test_riichi_moves_stick_to_kyotaku(self):
        state, deltas, _ = apply_action(_state(), {"type": "riichi", "seat": 2})

        self.assertEqual(state["scores"], [25000, 25000, 24000, 25000])
        self.assertEqual(state["kyotaku"], 1000)
        self.assertTrue(state["riichi"][2])
        with self.assertRaises(LiveError):
            apply_action(state, {"type": "riichi", "seat": 2})

    def test_child_ron_with_honba_and_kyotaku_goes_to_next_round(self):
        state, deltas, message = apply_action(
            _state(honba=1, kyotaku=1000),
            {"type": "ron", "winner": 1, "loser": 2, "han": 4, "fu": 30},
        )

        self.assertEqual(deltas, [0, 7700 + 300 + 1000, -(7700 + 300), 0])
        self.assertEqual((state["round"], state["honba"], state["kyotaku"]), (1, 0, 0))
        self.assertIn("ロン", message)

    def test_dealer_win_continues_with_extra_honba(self):
        state, _, _ = apply_action(_state(), {"type": "ron", "winner": 0, "loser": 3, "points": 12000})

        self.assertEqual(state["scores"], [37000, 25000, 25000, 13000])
        self.assertEqual((state["round"], state["honba"]), (0, 1))

    def test_child_tsumo_with_honba(self):
        # 東2局（親は席1）2本場、子（席3）が 30符3翻ツモ: 子1000・親2000 に各200
        state, deltas, _ = apply_action(
            _state(round_index=1, honba=2),
            {"type": "tsumo", "winner": 3, "han": 3, "fu": 30},
        )

        self.assertEqual(deltas, [-1200, -2200, -1200, 4600])
        self.assertEqual((state["round"], state["honba"]), (2, 0))

    def test_dealer_tsumo_direct_input(self):
        state, deltas, _ = apply_action(_state(), {"type": "tsumo", "winner": 0, "points_each": 4000})

        self.assertEqual(deltas, [12000, -4000, -4000, -4000])
        self.assertEqual((state["round"], state["honba"]), (0, 1))

    def test_draw_with_dealer_noten_moves_to_next_round(self):
        state, deltas, _ = apply_action(_state(), {"type": "draw", "tenpai": [2]})

        self.assertEqual(deltas, [-1000, -1000, 3000, -1000])
        self.assertEqual((state["round"], state["honba"]), (1, 1))

    def test_draw_with_dealer_tenpai_continues(self):
        state, deltas, _ = apply_action(_state(), {"type": "draw", "tenpai": [0, 1]})

        self.assertEqual(deltas, [1500, 1500, -1500, -1500])
        self.assertEqual((state["round"], state["honba"]), (0, 1))

    def test_draw_all_tenpai_or_noten_has_no_payment(self):
        for tenpai in ([], [0, 1, 2, 3]):
            _, deltas, _ = apply_action(_state(), {"type": "draw", "tenpai": tenpai})
            self.assertEqual(deltas, [0, 0, 0, 0])

    def test_transfer(self):
        state, _, _ = apply_action(_state(), {"type": "transfer", "from": 1, "to": 3, "amount": 1000})

        self.assertEqual(state["scores"], [25000, 24000, 25000, 26000])
        self.assertEqual(state["round"], 0)

    def test_finish_gives_kyotaku_to_top_with_seat_order_for_ties(self):
        state, deltas, _ = apply_action(
            _state(scores=[30000, 20000, 30000, 19000], kyotaku=1000),
            {"type": "finish"},
        )

        self.assertEqual(deltas, [1000, 0, 0, 0])
        self.assertTrue(state["finished"])
        self.assertEqual(sum(state["scores"]), 100000)
        with self.assertRaises(LiveError):
            apply_action(state, {"type": "riichi", "seat": 0})

    def test_invalid_actions(self):
        for action in [
            {"type": "ron", "winner": 1, "loser": 1, "points": 1000},
            {"type": "ron", "winner": 1, "loser": 2, "points": 1050},
            {"type": "transfer", "from": 0, "to": 1, "amount": -100},
            {"type": "unknown"},
        ]:
            with self.assertRaises(LiveError, msg=action):
                apply_action(_state(), action)

    def test_original_state_is_not_modified(self):
        original = _state()
        apply_action(original, {"type": "riichi", "seat": 0})

        self.assertEqual(original["scores"], [25000] * 4)

    def test_round_label(self):
        self.assertEqual([round_label(i) for i in (0, 3, 4, 7)], ["東1局", "東4局", "南1局", "南4局"])


class LiveTableRouteTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.players = [Player(name=n) for n in ["みぞぐち", "西田", "ゆすけ", "りょう"]]
        self.operator = User(username="operator")
        self.viewer = User(username="viewer")
        db.session.add_all(self.players + [self.operator, self.viewer])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _client(self, user):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["login_user_id"] = user.id
        return client

    def _create(self, client, order=(2, 0, 3, 1)):
        data = {f"seat{i}": self.players[index].id for i, index in enumerate(order)}
        response = client.post("/live/", data=data)
        self.assertEqual(response.status_code, 302)
        return LiveTable.query.order_by(LiveTable.id.desc()).first()

    def test_live_pages_require_login(self):
        client = app.test_client()
        self.assertIn("/login", client.get("/live/").headers["Location"])

    def test_create_table_keeps_seat_order(self):
        client = self._client(self.operator)
        table = self._create(client)

        state = json.loads(table.state)
        self.assertEqual([p["name"] for p in state["players"]], ["ゆすけ", "みぞぐち", "りょう", "西田"])
        self.assertEqual(state["scores"], [25000] * 4)
        self.assertEqual(client.get(f"/live/{table.id}").status_code, 200)

    def test_operator_actions_are_visible_to_viewer(self):
        operator = self._client(self.operator)
        table = self._create(operator)
        viewer = self._client(self.viewer)
        before = viewer.get(f"/live/{table.id}/state").get_json()
        self.assertFalse(before["can_operate"])

        response = operator.post(f"/live/{table.id}/action", json={"type": "ron", "winner": 1, "loser": 0, "points": 8000})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["can_operate"])

        after = viewer.get(f"/live/{table.id}/state?version={before['version']}").get_json()
        self.assertEqual(after["state"]["scores"], [17000, 33000, 25000, 25000])
        self.assertEqual(after["round_label"], "東2局")
        # 変更がなければ中身は返さない
        unchanged = viewer.get(f"/live/{table.id}/state?version={after['version']}").get_json()
        self.assertTrue(unchanged["unchanged"])

    def test_viewer_cannot_operate_until_takeover(self):
        table = self._create(self._client(self.operator))
        viewer = self._client(self.viewer)

        self.assertEqual(viewer.post(f"/live/{table.id}/action", json={"type": "riichi", "seat": 0}).status_code, 403)

        self.assertTrue(viewer.post(f"/live/{table.id}/takeover").get_json()["can_operate"])
        self.assertEqual(viewer.post(f"/live/{table.id}/action", json={"type": "riichi", "seat": 0}).status_code, 200)

    def test_dry_run_does_not_save(self):
        client = self._client(self.operator)
        table = self._create(client)

        response = client.post(f"/live/{table.id}/action", json={"type": "ron", "winner": 1, "loser": 0, "han": 4, "fu": 30, "dry_run": True})

        self.assertEqual(response.get_json()["deltas"], [-7700, 7700, 0, 0])
        db.session.expire_all()
        self.assertEqual(json.loads(db.session.get(LiveTable, table.id).state)["scores"], [25000] * 4)

    def test_undo_restores_previous_state(self):
        client = self._client(self.operator)
        table = self._create(client)
        client.post(f"/live/{table.id}/action", json={"type": "riichi", "seat": 0})
        client.post(f"/live/{table.id}/action", json={"type": "ron", "winner": 1, "loser": 2, "points": 3900})

        state = client.post(f"/live/{table.id}/undo").get_json()["state"]
        self.assertEqual(state["scores"], [24000, 25000, 25000, 25000])
        self.assertEqual(state["kyotaku"], 1000)

        client.post(f"/live/{table.id}/undo")
        self.assertEqual(client.post(f"/live/{table.id}/undo").status_code, 400)

    def test_finished_table_prefills_game_input(self):
        client = self._client(self.operator)
        table = self._create(client, order=(0, 1, 2, 3))
        client.post(f"/live/{table.id}/action", json={"type": "ron", "winner": 2, "loser": 3, "points": 12000})
        client.post(f"/live/{table.id}/action", json={"type": "finish"})

        html = client.get(f"/?live={table.id}").data.decode("utf-8")

        for i, (player, score) in enumerate(zip(self.players, [25000, 25000, 37000, 13000]), start=1):
            select_html = html.split(f'name="player{i}_id"')[1].split("</select>")[0]
            self.assertIn(f'<option value="{player.id}" selected', select_html)
            self.assertIn(f'value="{score // 100}"', html.split(f'name="score{i}"')[1].split(">")[0])
        # 同点時は起家に近い順（席順）で決まるよう、席順を引き継ぐ
        tie_order = ",".join(str(p.id) for p in self.players)
        self.assertIn(f'id="tie_order" value="{tie_order}"', html)

    def test_unfinished_table_does_not_prefill_game_input(self):
        client = self._client(self.operator)
        table = self._create(client)

        html = client.get(f"/?live={table.id}").data.decode("utf-8")

        self.assertIn('id="tie_order" value=""', html)

    def test_close_removes_table_from_list(self):
        client = self._client(self.operator)
        table = self._create(client)

        client.post(f"/live/{table.id}/close")

        self.assertEqual(client.get(f"/live/{table.id}/state").status_code, 404)
        self.assertNotIn(f"/live/{table.id}", client.get("/live/").data.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
