"""중계 막기(relay.py): /proc/net/tcp 해석·허용 계정·막을 때 응답"""
import unittest
from types import SimpleNamespace

from fastportal import relay

TCP = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:B1F2 0100007F:21FC 01 00000000:00000000 00:00000000 00000000  1008        0 1 1 0
   1: 0100007F:B1F3 0100007F:21FC 01 00000000:00000000 00:00000000 00000000  1010        0 2 1 0
   2: 0100007F:21FC 0100007F:B1F2 01 00000000:00000000 00:00000000 00000000  1010        0 3 1 0
   3: 0100007F:C000 0100007F:1F90 01 00000000:00000000 00:00000000 00000000  1007        0 4 1 0
   4: 0100007F:B1F4 0100007F:21FC 06 00000000:00000000 00:00000000 00000000     0        0 0 1 0
"""
TCP6 = """  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0000000000000000FFFF00000100007F:B200 0000000000000000FFFF00000100007F:21FC 01 00000000:00000000 00:00000000 00000000  1007        0 5 1 0
   1: 00000000000000000000000001000000:B201 00000000000000000000000001000000:21FC 01 00000000:00000000 00:00000000 00000000  1009        0 6 1 0
"""


class Parse(unittest.TestCase):
    def test_v4_only_sockets_to_server_port(self):
        m = relay.parse(TCP, 0x21FC)   # 8700
        self.assertEqual(m[("127.0.0.1", 0xB1F2)], 1008)
        self.assertEqual(m[("127.0.0.1", 0xB1F3)], 1010)
        self.assertNotIn(("127.0.0.1", 0x21FC), m)   # 서버 쪽 소켓(원격 포트가 B1F2)은 아님
        self.assertNotIn(("127.0.0.1", 0xC000), m)   # 다른 포트(8080)로 가는 소켓
        self.assertNotIn(("127.0.0.1", 0xB1F4), m)   # TIME_WAIT(주인 없음, uid 0)

    def test_v6_mapped_and_loopback(self):
        m = relay.parse(TCP6, 0x21FC)
        self.assertEqual(m[("127.0.0.1", 0xB200)], 1007)
        self.assertEqual(m[("::1", 0xB201)], 1009)


class Check(unittest.TestCase):
    def setUp(self):
        self.saved = (relay.ENABLED, relay.ALLOW, relay._lookup)
        relay.ENABLED, relay.ALLOW = True, {1010}
        relay._cache.clear()

    def tearDown(self):
        relay.ENABLED, relay.ALLOW, relay._lookup = self.saved
        relay._cache.clear()

    def req(self, port, headers=None):
        return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1", port=port), scope={"server": ("0.0.0.0", 8700)},
                               headers=headers or {})

    def test_outside_connection_allowed(self):
        relay._lookup = lambda ip, port, sp: None
        self.assertIsNone(relay.check(self.req(1)))

    def test_remote_address_skips_proc_lookup(self):
        seen = []
        relay._lookup = lambda ip, port, sp: seen.append(ip) or 1008
        r = self.req(9); r.client.host = "192.0.2.10"   # 이 서버 주소가 아님(문서용 대역)
        self.assertIsNone(relay.check(r))
        self.assertEqual(seen, [], "다른 PC 접속은 /proc 표를 읽지 않음")
        self.assertTrue(relay.is_local("127.0.0.1")); self.assertFalse(relay.is_local("192.0.2.10"))

    def test_same_account_allowed(self):
        relay._lookup = lambda ip, port, sp: 1010
        self.assertIsNone(relay.check(self.req(2)))

    def test_other_account_blocked(self):
        relay._lookup = lambda ip, port, sp: 1008
        uid, _name = relay.check(self.req(3))
        self.assertEqual(uid, 1008)

    def test_other_account_direct_browser_allowed(self):   # 다른 계정 사용자의 SSH 터널로 브라우저가 직접 연 것
        relay._lookup = lambda ip, port, sp: 1007
        h = {"host": "localhost:8700", "sec-fetch-site": "same-origin", "sec-fetch-mode": "cors", "origin": "http://localhost:8700",
             "referer": "http://localhost:8700/tool/digitizer-local"}
        self.assertIsNone(relay.check(self.req(5, h)))
        self.assertIsNone(relay.check(self.req(6, {"host": "localhost:8700", "sec-fetch-site": "none", "sec-fetch-mode": "navigate"})))

    def test_relay_server_patterns_blocked(self):
        relay._lookup = lambda ip, port, sp: 1008
        cases = [
            {"host": "127.0.0.1:8700", "user-agent": "python-httpx/0.27"},                                  # 서버가 새로 만든 요청
            {"host": "127.0.0.1:8700", "sec-fetch-site": "same-origin", "referer": "http://10.0.0.5:8800/portal/"},   # 남의 화면 헤더를 넘김
            {"host": "127.0.0.1:8700", "sec-fetch-site": "same-origin", "origin": "http://10.0.0.5:8800"},
            {"host": "127.0.0.1:8700", "sec-fetch-site": "same-origin", "x-forwarded-for": "10.0.0.9"},
            {"host": "127.0.0.1:8700", "sec-fetch-site": "cross-site"},
        ]
        for i, h in enumerate(cases):
            self.assertIsNotNone(relay.check(self.req(10 + i, h)), h)

    def test_disabled(self):
        relay.ENABLED = False
        relay._lookup = lambda ip, port, sp: 1008
        self.assertIsNone(relay.check(self.req(4)))

    def test_log_once_per_hour(self):
        relay._warned.clear()
        self.assertTrue(relay.should_log(1008))
        self.assertFalse(relay.should_log(1008))


if __name__ == "__main__":
    unittest.main()
