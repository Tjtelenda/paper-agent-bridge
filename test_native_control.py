import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import native_control as n

class NativeControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.request={'request_id':'native_fixture_00001','operation':'create_notebook','arguments':{'parent_id':'root','name':'Disposable'}}
    def test_receipt_replay_and_changed_request_refusal(self):
        receipt={'request_id':self.request['request_id'],'operation':'create_notebook','status':'verified','result':{'id':'created'}}
        with patch.object(n,'invoke',return_value=receipt) as call:
            self.assertEqual(n.execute('remarkable',self.request,self.root),receipt)
            self.assertEqual(n.execute('remarkable',self.request,self.root),receipt)
            self.assertEqual(call.call_count,1)
            native=call.call_args.args[1];self.assertIn('document_id',native['arguments']);self.assertIn('page_id',native['arguments'])
            changed=dict(self.request,arguments={'parent_id':'root','name':'Other'})
            with self.assertRaises(ValueError):n.execute('remarkable',changed,self.root)
    def test_uncertain_dispatch_never_reexecutes(self):
        with patch.object(n,'invoke',side_effect=ValueError('timeout')) as call:
            with self.assertRaises(ValueError):n.execute('remarkable',self.request,self.root)
            self.assertEqual(n.execute('remarkable',self.request,self.root)['status'],'needs_review')
            self.assertEqual(call.call_count,1)
    def test_reconcile_absent_journal_allows_only_explicit_same_id_retry(self):
        with patch.object(n,'invoke',side_effect=ValueError('timeout')):
            with self.assertRaises(ValueError):n.execute('remarkable',self.request,self.root)
        absent={'status':'verified','result':{'found':False}}
        with patch.object(n,'invoke',return_value=absent):
            self.assertTrue(n.reconcile('remarkable',self.request['request_id'],self.root)['result']['not_dispatched'])
        done={'request_id':self.request['request_id'],'operation':'create_notebook','status':'verified','result':{}}
        with patch.object(n,'invoke',return_value=done) as call:
            n.execute('remarkable',self.request,self.root);call.assert_not_called()
            self.assertEqual(n.execute('remarkable',self.request,self.root,retry_undispatched=True),done)
            self.assertEqual(call.call_count,1)

    def test_delete_requires_explicit_scope_and_real_backup(self):
        request=dict(self.request,operation='delete_pages',arguments={'id':'target','page_ids':['p']})
        with patch.object(n,'backup') as backup,patch.object(n,'invoke') as call:
            with self.assertRaises(ValueError):n.execute('remarkable',request,self.root)
            backup.assert_not_called();call.assert_not_called()
    def test_nonzero_device_review_receipt_is_preserved(self):
        result={'request_id':self.request['request_id'],'operation':'create_notebook','status':'needs_review','result':{'reason':'changed'}}
        with patch.object(n,'ssh',return_value=json.dumps(result).encode()) as call:
            self.assertEqual(n.invoke('remarkable',self.request),result)
            self.assertEqual(call.call_args.kwargs['accepted'],(0,1,2))
if __name__=='__main__':unittest.main()
