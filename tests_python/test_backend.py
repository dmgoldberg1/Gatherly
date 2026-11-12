import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import boto3
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from bench import backend


class SharedStateTest(unittest.TestCase):
    def setUp(self):
        self.client = boto3.client('s3', region_name='ru-central1', endpoint_url='https://storage.yandexcloud.net',
                                   aws_access_key_id='testing', aws_secret_access_key='testing')
        self.state = {'version': 4, 'lineage': 'existing-stand', 'serial': 12, 'resources': [{'mode': 'managed'}]}
        self.raw = json.dumps(self.state).encode()
        self.location = {'Bucket': 'state-bucket', 'Key': 'gatherly-bench/terraform.tfstate'}

    def files(self, directory):
        root = Path(directory)
        (root / 'terraform.tfstate').write_bytes(self.raw)
        (root / 'backend.s3.tf.example').write_text('terraform { backend "s3" {} }')
        return root

    def response(self, state):
        raw = json.dumps(state).encode()
        return {'Body': StreamingBody(io.BytesIO(raw), len(raw))}

    def test_local_state_cannot_be_silently_ignored_by_remote_reconfiguration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.files(directory)
            run = Mock()
            with self.assertRaisesRegex(ValueError, 'migrate_state'):
                backend.initialize(root, 'state-bucket', 'gatherly-bench', run)
            run.assert_not_called()
            self.assertEqual((root / 'terraform.tfstate').read_bytes(), self.raw)

    def test_first_migration_uses_conditional_put_and_keeps_local_backup(self):
        with tempfile.TemporaryDirectory() as directory, Stubber(self.client) as stub:
            root = self.files(directory)
            stub.add_client_error('get_object', service_error_code='NoSuchKey', http_status_code=404,
                                  expected_params=self.location)
            stub.add_response('put_object', {}, self.location | {'Body': self.raw,
                              'ContentType': 'application/json', 'IfNoneMatch': '*'})
            stub.add_response('get_object', self.response(self.state), self.location)
            run = Mock()
            backend.migrate(root, 'state-bucket', 'gatherly-bench', run, self.client)
            self.assertEqual((root / 'terraform.tfstate').read_bytes(), self.raw)
            self.assertIn('-backend-config=key=gatherly-bench/terraform.tfstate', run.call_args.args[0])
            stub.assert_no_pending_responses()

    def test_identical_remote_state_makes_migration_idempotent(self):
        with tempfile.TemporaryDirectory() as directory, Stubber(self.client) as stub:
            root = self.files(directory)
            stub.add_response('get_object', self.response(self.state), self.location)
            stub.add_response('get_object', self.response(self.state), self.location)
            run = Mock()
            backend.migrate(root, 'state-bucket', 'gatherly-bench', run, self.client)
            run.assert_called_once()
            stub.assert_no_pending_responses()

    def test_seed_verifies_remote_copy_without_reconfiguring_local_backend(self):
        with tempfile.TemporaryDirectory() as directory, Stubber(self.client) as stub:
            root = self.files(directory)
            stub.add_client_error('get_object', service_error_code='NoSuchKey', http_status_code=404,
                                  expected_params=self.location)
            stub.add_response('put_object', {}, self.location | {'Body': self.raw,
                              'ContentType': 'application/json', 'IfNoneMatch': '*'})
            stub.add_response('get_object', self.response(self.state), self.location)
            backend.seed(root, 'state-bucket', 'gatherly-bench', self.client)
            self.assertEqual((root / 'terraform.tfstate').read_bytes(), self.raw)
            self.assertFalse((root / 'backend.tf').exists())
            stub.assert_no_pending_responses()

    def test_changed_remote_copy_prevents_backend_reconfiguration(self):
        with tempfile.TemporaryDirectory() as directory, Stubber(self.client) as stub:
            root = self.files(directory)
            stub.add_response('get_object', self.response(self.state), self.location)
            stub.add_response('get_object', self.response(self.state | {'serial': 13}), self.location)
            run = Mock()
            with self.assertRaisesRegex(ValueError, 'changed during migration'):
                backend.migrate(root, 'state-bucket', 'gatherly-bench', run, self.client)
            run.assert_not_called()
            self.assertFalse((root / 'backend.tf').exists())

    def test_migration_can_repeat_after_terraform_empties_local_state(self):
        with tempfile.TemporaryDirectory() as directory, Stubber(self.client) as stub:
            root = self.files(directory)
            (root / 'terraform.tfstate').write_bytes(b'')
            (root / 'terraform.tfstate.backup').write_bytes(self.raw)
            (root / '.terraform').mkdir()
            (root / '.terraform/terraform.tfstate').write_text(json.dumps({'backend': {
                'type': 's3', 'config': {'bucket': 'state-bucket', 'key': self.location['Key']}}}))
            stub.add_response('get_object', self.response(self.state), self.location)
            stub.add_response('get_object', self.response(self.state), self.location)
            run = Mock()
            backend.migrate(root, 'state-bucket', 'gatherly-bench', run, self.client)
            run.assert_called_once()
            self.assertEqual((root / 'terraform.tfstate.backup').read_bytes(), self.raw)

    def test_backup_is_not_used_for_an_unrelated_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.files(directory)
            (root / 'terraform.tfstate').write_bytes(b'')
            (root / 'terraform.tfstate.backup').write_bytes(self.raw)
            with self.assertRaises(ValueError):
                backend.migrate(root, 'another-bucket', 'gatherly-bench', Mock(), self.client)

    def test_terraform_check_order_is_ignored_but_changed_status_is_rejected(self):
        checks = [{'config_addr': name, 'status': 'pass', 'objects': [
            {'object_addr': name, 'status': 'pass'}]} for name in ['var.zone', 'var.replicas']]
        local = self.state | {'check_results': checks}
        for changed in (False, True):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory, Stubber(self.client) as stub:
                root = self.files(directory)
                (root / 'terraform.tfstate').write_text(json.dumps(local))
                reordered = [dict(check) for check in reversed(checks)]
                if changed:
                    reordered[0]['status'] = 'fail'
                remote = self.state | {'check_results': reordered}
                stub.add_response('get_object', self.response(remote), self.location)
                if changed:
                    with self.assertRaisesRegex(ValueError, 'differs'):
                        backend.seed(root, 'state-bucket', 'gatherly-bench', self.client)
                else:
                    stub.add_response('get_object', self.response(remote), self.location)
                    backend.seed(root, 'state-bucket', 'gatherly-bench', self.client)
                stub.assert_no_pending_responses()

    def test_different_or_newer_remote_state_is_never_overwritten(self):
        for changes in ({'lineage': 'another-stand'}, {'serial': 13}, {'serial': 11}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory, Stubber(self.client) as stub:
                root = self.files(directory)
                stub.add_response('get_object', self.response(self.state | changes), self.location)
                run = Mock()
                with self.assertRaisesRegex(ValueError, 'refusing to overwrite'):
                    backend.migrate(root, 'state-bucket', 'gatherly-bench', run, self.client)
                run.assert_not_called()
                self.assertFalse((root / 'backend.tf').exists())

    def test_concurrent_backend_creation_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory, Stubber(self.client) as stub:
            root = self.files(directory)
            stub.add_client_error('get_object', service_error_code='NoSuchKey', http_status_code=404,
                                  expected_params=self.location)
            stub.add_client_error('put_object', service_error_code='PreconditionFailed', http_status_code=412,
                                  expected_params=self.location | {'Body': self.raw,
                                  'ContentType': 'application/json', 'IfNoneMatch': '*'})
            run = Mock()
            with self.assertRaises(ClientError):
                backend.migrate(root, 'state-bucket', 'gatherly-bench', run, self.client)
            run.assert_not_called()

    def test_state_keys_isolate_stands_and_reject_paths(self):
        self.assertNotEqual(backend.state_key('stand-one'), backend.state_key('stand-two'))
        for stand in ('../../other', 'x', 'name\nOTHER=value'):
            with self.subTest(stand=stand), self.assertRaises(ValueError):
                backend.state_key(stand)
