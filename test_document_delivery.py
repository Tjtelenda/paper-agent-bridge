import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import document_delivery as d

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.file = self.root / 'Reading.pdf'
        self.file.write_bytes(b'%PDF-1.7\nsynthetic-test-only')
        self.state = self.root / 'state'
        self.request = 'delivery_test_00001'
        self.folder = dict(id='folder-id', name='Reading', type='CollectionType', parent='')
        self.doc = dict(id='document-id', name='Reading', type='DocumentType', parent='folder-id')
        self.uploaded = False
        self.calls = []

    def runner(self, binary, *args):
        self.calls.append(args)
        if args == ('-json', 'ls', '/'):
            return json.dumps([self.folder]).encode()
        if args == ('-json', 'ls', '/Reading'):
            return json.dumps([self.doc] if self.uploaded else []).encode()
        if args[0] == 'put':
            self.uploaded = True
            self.assertEqual(args[2], '/Reading')
            self.assertEqual(Path(args[1]).read_bytes(), self.file.read_bytes())
            return b'OK'
        self.fail('unexpected command')

    def send(self):
        with patch.object(d.shutil, 'which', return_value='/client/rmapi'), patch.object(d, 'run', side_effect=self.runner):
            return d.deliver(self.file, '/Reading', self.request, self.state)

    def test_readback_and_replay(self):
        result = self.send()
        self.assertEqual(result['status'], 'cloud_visible')
        self.assertFalse(result['device_sync_verified'])
        count = len(self.calls)
        self.assertEqual(self.send(), result)
        self.assertEqual(len(self.calls), count)
        self.file.write_bytes(b'%PDF-1.7\nchanged')
        with self.assertRaisesRegex(ValueError, 'different content'):
            self.send()

    def test_uncertain_never_retries(self):
        runner = self.runner
        def fail(binary, *args):
            if args[0] == 'put':
                raise subprocess.TimeoutExpired('rmapi', 180)
            return runner(binary, *args)
        with patch.object(d.shutil, 'which', return_value='/client/rmapi'), patch.object(d, 'run', side_effect=fail) as mock:
            first = d.deliver(self.file, '/Reading', self.request, self.state)
            count = mock.call_count
            self.assertEqual(d.deliver(self.file, '/Reading', self.request, self.state), first)
            self.assertEqual(mock.call_count, count)
            self.assertEqual(first['status'], 'dispatch_uncertain')

    def test_existing_and_ambiguous_refused(self):
        self.uploaded = True
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.send()
        self.assertFalse(any(c[0] == 'put' for c in self.calls))
        with patch.object(d, 'listing', return_value=[self.folder, self.folder]):
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                d.resolve_folder('rmapi', '/Reading')

    def test_missing_client_and_permissions(self):
        with patch.object(d.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'not installed'):
                d.deliver(self.file, '/', self.request, self.state)
        self.state.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'mode 700'):
            self.send()

    def test_validation_and_epub(self):
        for folder in ('Reading', '/Reading/../Other', '/Reading/*', '//Reading', '/Reading/'):
            with self.assertRaises(ValueError):
                d.validate(self.file, folder, self.request)
        self.file.write_bytes(b'not pdf')
        with self.assertRaisesRegex(ValueError, 'PDF'):
            d.validate(self.file, '/', self.request)
        epub = self.root / 'Book.epub'
        with zipfile.ZipFile(epub, 'w') as archive:
            archive.writestr('mimetype', 'application/epub+zip')
        self.assertTrue(d.validate(epub, '/', self.request))

    def test_noninteractive_no_shell_no_trace(self):
        result = subprocess.CompletedProcess([], 0, b'[]', b'private debug')
        with patch.dict(d.os.environ, {'RMAPI_TRACE': '1'}), patch.object(d.subprocess, 'run', return_value=result) as mock:
            self.assertEqual(d.run('/client/rmapi', '-json', 'ls', '/Reading'), b'[]')
        args, kwargs = mock.call_args
        self.assertEqual(args[0], ['/client/rmapi', '-ni', '-json', 'ls', '/Reading'])
        self.assertNotIn('RMAPI_TRACE', kwargs['env'])
        self.assertNotIn('shell', kwargs)
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)

    def test_predictable_temporary_links_never_clobber_victim(self):
        self.state.mkdir(mode=0o700)
        victim = self.root / 'victim'
        victim.write_text('preserve me')
        for kind in ('symlink', 'hardlink'):
            path = self.state / (kind + '.json')
            predictable = path.with_suffix('.tmp')
            if kind == 'symlink':
                predictable.symlink_to(victim)
            else:
                predictable.hardlink_to(victim)
            d.save(path, {'status': 'test'})
            self.assertEqual(victim.read_text(), 'preserve me')
            self.assertEqual(json.loads(path.read_text()), {'status': 'test'})
            self.assertTrue(predictable.exists())

    def test_receipt_and_lock_links_refused_without_reading_or_writing(self):
        victim = self.root / 'victim'
        victim.write_text('preserve me')
        for entry in ('json', 'lock'):
            for kind in ('symlink', 'hardlink'):
                state = self.root / (entry + '-' + kind)
                state.mkdir(mode=0o700)
                linked = state / (self.request + '.' + entry)
                if kind == 'symlink':
                    linked.symlink_to(victim)
                else:
                    linked.hardlink_to(victim)
                with patch.object(d, 'run') as runner:
                    with self.assertRaisesRegex(ValueError, 'without links'):
                        d.deliver(self.file, '/Reading', self.request, state)
                    runner.assert_not_called()
                self.assertEqual(victim.read_text(), 'preserve me')
                if entry == 'json':
                    with self.assertRaisesRegex(ValueError, 'without links'):
                        d.save(linked, {'status': 'test'})
                self.assertEqual(victim.read_text(), 'preserve me')
