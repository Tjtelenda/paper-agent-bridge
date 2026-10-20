import base64
import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge import Bridge, make_server
import control_relay


class NativeRelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.bridge = Bridge(self.path / 'relay.db', {})
        self.command = {'request_id': 'native_command_0001', 'operation': 'inspect', 'arguments': {}}
        self.receipt = dict(self.command, status='verified', result={'id': 'fixture'})
        del self.receipt['arguments']
        self.device = 'device_' + 'a' * 40
        self.agent = 'agent_' + 'b' * 40

    def http(self, path, token, payload=None):
        raw = None if payload is None else json.dumps(payload).encode()
        req = Request(self.origin + path, data=raw, headers={'Authorization': 'Bearer ' + token})
        try:
            response = urlopen(req, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.code, response.read()

    def server(self):
        server = make_server(self.bridge, self.device, self.agent)
        self.origin = 'http://127.0.0.1:' + str(server.server_port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

    def test_restart_duplicate_and_lost_receipt_ack_preserve_exact_outcome(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            codes = [r[0] for r in pool.map(self.bridge.queue_control, [self.command] * 4)]
        self.assertEqual(codes.count(201), 1)
        changed = dict(self.command, arguments={'id': 'different'})
        self.assertEqual(self.bridge.queue_control(changed)[0], 409)
        self.assertEqual(self.bridge.control_receipt(self.receipt)[0], 409)
        self.assertEqual(self.bridge.next_control()['command'], self.command)
        restarted = Bridge(self.bridge.database, {})
        self.assertEqual(restarted.next_control()['command'], self.command)
        self.assertEqual(restarted.control_receipt(self.receipt)[0], 201)
        self.assertTrue(restarted.control_receipt(self.receipt)[1]['duplicate'])
        self.assertEqual(restarted.control_receipt(dict(self.receipt, result={'id': 'conflicting'}))[0], 409)
        final = Bridge(self.bridge.database, {})
        self.assertIsNone(final.next_control()['command'])
        self.assertEqual(final.control_status(self.command['request_id'])[1]['receipt'], self.receipt)
        self.assertEqual(final.queue_control(self.command)[0], 200)
        self.assertEqual(final.next_control(), {'command': None})

    def test_native_queue_has_no_note_or_shell_dispatch(self):
        for invalid in [dict(self.command, operation='system.run'), dict(self.command, arguments=[]),
                        dict(self.command, extra=True), dict(self.command, arguments={'n': float('nan')}),
                        dict(self.command, arguments={'text': 'x' * (128 * 1024)})]:
            self.assertEqual(self.bridge.queue_control(invalid)[0], 400)
        with self.bridge.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM threads').fetchone()[0], 0)
        for i in range(100):
            self.assertEqual(self.bridge.queue_control(dict(self.command, request_id=f'native_limit_{i:05d}'))[0], 201)
        self.assertEqual(self.bridge.queue_control(self.command)[0], 429)

    def test_native_tag_commands_are_allowlisted_but_global_tag_edits_are_not(self):
        for index, operation in enumerate(('list_tags', 'add_tags', 'remove_tags')):
            command = dict(self.command, request_id=f'native_tag_{index:08d}', operation=operation)
            self.assertEqual(self.bridge.queue_control(command)[0], 201)
        for operation in ('rename_tag', 'delete_tag'):
            command = dict(self.command, request_id='native_global_' + operation, operation=operation)
            self.assertEqual(self.bridge.queue_control(command)[0], 400)

    def test_poll_wakes_and_receipts_must_match_dispatched_identity(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.bridge.next_control, 2)
            time.sleep(0.05)
            self.bridge.queue_control(self.command)
            self.assertEqual(future.result(1), {'command': self.command})
        self.assertEqual(self.bridge.control_receipt(dict(self.receipt, operation='rename'))[0], 409)
        self.assertEqual(self.bridge.control_receipt(dict(self.receipt, request_id='native_unknown_0001'))[0], 404)
        self.assertEqual(self.bridge.control_receipt(dict(self.receipt, status='queued'))[0], 400)
        self.assertEqual(self.bridge.control_receipt(dict(self.receipt, result='wrong'))[0], 400)

    def test_http_roles_assets_and_cli_status_do_not_claim_completion(self):
        self.server()
        for path, token, payload in [('/v1/agent/control/requests', self.device, self.command),
                ('/v1/device/control/receipts', self.agent, self.receipt),
                ('/v1/device/control/next', self.agent, None),
                ('/v1/agent/control/requests/' + self.command['request_id'], self.device, None)]:
            self.assertEqual(self.http(path, token, payload)[0], 401)
        config = self.path / 'config'
        config.mkdir(mode=0o700)
        (config / 'url').write_text(self.origin)
        (config / 'token').write_text(self.agent)
        (config / 'token').chmod(0o600)
        code, queued = control_relay.submit(config, self.command)
        self.assertEqual(code, 200)
        self.assertEqual(queued['status'], 'queued')
        self.assertIsNone(queued['receipt'])
        self.assertEqual(json.loads(self.http('/v1/device/control/next?wait=0', self.device)[1])['command'], self.command)
        self.assertEqual(self.http('/v1/device/control/receipts', self.device, self.receipt)[0], 201)
        self.assertEqual(control_relay.status(config, self.command['request_id'])[1]['receipt'], self.receipt)
        self.assertEqual(self.http('/v1/device/control/next?wait=26', self.device)[0], 400)
        image = self.path / 'test.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        image.chmod(0o600)
        digest, image_format = control_relay.upload_image(config, image)
        route = '/v1/device/control/assets/' + digest + '.' + image_format
        self.assertEqual(self.http(route, self.agent)[0], 401)
        self.assertEqual(self.http(route, self.device), (200, image.read_bytes()))
        self.assertEqual(self.http('/v1/device/control/assets/../../secret.png', self.device)[0], 404)
        insert = dict(self.command, request_id='native_image_000001', operation='insert_image',
                      arguments={'image_sha256': digest, 'image_format': image_format})
        self.assertEqual(self.bridge.queue_control(insert)[0], 201)
        self.assertEqual(self.bridge.queue_control(dict(insert, request_id='native_image_000002', arguments={}))[0], 400)


if __name__ == '__main__':
    unittest.main()
