import json
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import library_control as control
import document_delivery


class LibraryControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.document_id = '11111111-2222-3333-4444-555555555555'
        self.nodes = {
            '/Notes': {'id': 'folder-id', 'parent': '', 'name': 'Notes', 'type': 'CollectionType', 'version': 1},
            '/Sketch': {'id': self.document_id, 'parent': '', 'name': 'Sketch', 'type': 'DocumentType', 'version': 2}}
        self.calls = []
        self.listing_patch = patch.object(control, 'listing', side_effect=self.listing)
        self.run_patch = patch.object(control, 'run', side_effect=self.command)
        self.listing_patch.start(); self.run_patch.start()
        self.addCleanup(self.listing_patch.stop); self.addCleanup(self.run_patch.stop)
        common_listing = patch.object(document_delivery, 'listing', side_effect=self.listing)
        common_listing.start()
        self.addCleanup(common_listing.stop)

    def listing(self, binary, folder):
        return [dict(node) for path, node in self.nodes.items() if str(PurePosixPath(path).parent) == folder]

    def command(self, binary, *arguments):
        self.calls.append(arguments)
        if arguments[0] == 'mkdir':
            target = arguments[1]
            self.nodes[target] = {'id': 'new-folder', 'name': PurePosixPath(target).name,
                                  'type': 'CollectionType', 'parent': '', 'version': 1}
        else:
            source, target = arguments[1:]
            node = self.nodes.pop(source)
            node.update(name=PurePosixPath(target).name,
                        parent=self.nodes[str(PurePosixPath(target).parent)]['id'], version=node['version'] + 1)
            self.nodes[target] = node
        return b''

    def move(self, **changes):
        args = {'operation': 'move', 'source': '/Sketch', 'target': '/Notes/Renamed',
                'expected_id': self.document_id, 'expected_version': 2,
                'request_id': 'library_move_000001', 'state_dir': self.state}
        return control.mutate('rmapi', **dict(args, **changes))

    def test_move_preserves_identity_and_replay_never_reissues(self):
        receipt = self.move()
        self.assertEqual(receipt['status'], 'cloud_verified')
        self.assertEqual(receipt['entry']['id'], self.document_id)
        self.assertFalse(receipt['device_sync_verified'])
        self.assertNotIn('/Sketch', self.nodes)
        self.assertEqual(self.move(), receipt)
        self.assertEqual(self.calls, [('mv', '/Sketch', '/Notes/Renamed')])
        with self.assertRaises(ValueError):
            self.move(target='/Notes/Different')

    def test_source_identity_version_and_destination_collision_stop_before_write(self):
        for changes in ({'expected_id': 'different'}, {'expected_version': 1}, {'target': '/Notes'}):
            with self.assertRaises(ValueError):
                self.move(**changes)
        self.assertEqual(self.calls, [])
        self.nodes['/sketch'] = dict(self.nodes['/Sketch'], id='duplicate', name='sketch')
        with self.assertRaises(ValueError):
            self.move()

    def test_ambiguous_parent_and_traversal_stop_before_write(self):
        self.nodes['/notes'] = dict(self.nodes['/Notes'], id='different-folder', name='notes')
        with self.assertRaises(ValueError):
            self.move()
        for path in ('/Notes/*', '/Notes/../Other', '//Notes', '/Notes/'):
            with self.assertRaises(ValueError):
                control.library_path(path)
        self.assertEqual(self.calls, [])

    def test_uncertain_write_is_preserved_without_retry(self):
        with patch.object(control, 'run', side_effect=subprocess.TimeoutExpired('rmapi', 180)) as runner:
            first = self.move()
            self.assertEqual(first['status'], 'dispatch_uncertain')
            self.assertEqual(self.move(), first)
            self.assertEqual(runner.call_count, 1)

    def test_create_folder_receipt_and_existing_name_refusal(self):
        result = control.mutate('rmapi', operation='create-folder', target='/Agent documents',
                                request_id='library_folder_0001', state_dir=self.state)
        self.assertEqual(result['status'], 'cloud_verified')
        self.assertEqual(result['entry']['type'], 'CollectionType')
        with self.assertRaises(ValueError):
            control.mutate('rmapi', operation='create-folder', target='/Agent documents',
                           request_id='library_folder_0002', state_dir=self.state)

    def test_linked_receipt_cannot_clobber_other_file(self):
        self.state.mkdir(mode=0o700)
        victim = self.root / 'victim'
        victim.write_text('preserved')
        (self.state / 'library_move_000001.json').symlink_to(victim)
        with self.assertRaises(ValueError):
            self.move()
        self.assertEqual(victim.read_text(), 'preserved')
        self.assertEqual(self.calls, [])

    def test_download_by_id_is_private_verified_and_never_overwrites(self):
        payload = b'PK\x03\x04synthetic-rmdoc'
        def fetch(arguments, **kwargs):
            self.assertEqual(arguments[-2:], ['--id', self.document_id])
            self.assertEqual(kwargs['stdout'], subprocess.DEVNULL)
            (Path(kwargs['cwd']) / 'Sketch.rmdoc').write_bytes(payload)
            return subprocess.CompletedProcess(arguments, 0)
        with patch.object(control.subprocess, 'run', side_effect=fetch):
            result = control.download('rmapi', '/Sketch', self.document_id, self.root / 'backups')
            repeated = control.download('rmapi', '/Sketch', self.document_id, self.root / 'backups')
        self.assertEqual(result, repeated)
        self.assertEqual(Path(result['path']).read_bytes(), payload)
        self.assertEqual(Path(result['path']).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            control.download('rmapi', '/Sketch', '../../bad', self.root / 'backups')


if __name__ == '__main__':
    unittest.main()
