import unittest
from unittest.mock import patch

import app as element_app


class ElementIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        element_app.room.reset()
        element_app.background_task_started = True
        self.ensure_patch = patch.object(element_app, "ensure_background_task", lambda: None)
        self.ensure_patch.start()

    def tearDown(self) -> None:
        self.ensure_patch.stop()
        element_app.room.players.clear()
        element_app.room.cards.clear()
        element_app.room.barriers.clear()
        element_app.room.projectiles.clear()
        element_app.room.effects.clear()
        element_app.room.running = False
        element_app.background_task_started = False

    def test_multi_client_connect_zone_shrink_and_room_limit(self) -> None:
        clients = []
        try:
            for _ in range(10):
                client = element_app.socketio.test_client(
                    element_app.app,
                    flask_test_client=element_app.app.test_client(),
                )
                self.assertTrue(client.is_connected())
                clients.append(client)

            self.assertEqual(len(element_app.room.players), 10)

            extra_client = element_app.socketio.test_client(
                element_app.app,
                flask_test_client=element_app.app.test_client(),
            )
            self.assertFalse(extra_client.is_connected())

            for client in clients[:2]:
                received = client.get_received()
                self.assertTrue(any(packet["name"] == "connected" for packet in received))

            current_radius = element_app.room.zone_radius()
            element_app.room.started_at -= 900.0
            shrunken_radius = element_app.room.zone_radius()
            self.assertLess(shrunken_radius, current_radius)
            self.assertGreaterEqual(shrunken_radius, element_app.ZONE_END_RADIUS)

            player = next(iter(element_app.room.players.values()))
            player.hp = 123
            player.inventory[:] = ["fire_breath", "wind_barrier"]
            element_app.room.drop_inventory(player)
            self.assertEqual(player.hp, 123)
            self.assertTrue(all(card is None for card in player.inventory))
            self.assertGreaterEqual(len(element_app.room.cards), 2)
        finally:
            for client in clients:
                client.disconnect()


if __name__ == "__main__":
    unittest.main()
