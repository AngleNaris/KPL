"""Association planning and authorization; all mutations here are simulated."""
import copy
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import editor_ready


def owned_snapshot(old):
    return {
        'extension': {'values': {'': 'KPL.SpecDocument.Prototype.1'}, 'subkeys': []},
        'program': {'values': {'': 'KPL 规格文档（原型）',
                               'KplPrototypeOwner': 'KPL-native-prototype-v1'}, 'subkeys': ['shell']},
        'shell': {'values': {}, 'subkeys': ['open']},
        'open': {'values': {}, 'subkeys': ['command']},
        'command': {'values': {'': '"' + os.path.abspath(old) + '" "%1"'}, 'subkeys': []},
    }


class OwnershipTests(unittest.TestCase):
    def test_only_exact_kpl_owned_shape_is_repairable(self):
        old = str(Path('C:/synthetic/old editor.exe').absolute())
        original = owned_snapshot(old)
        self.assertTrue(editor_ready.pristine_owned(original, old))
        variants = []
        changed = copy.deepcopy(original)
        changed['program']['values']['KplPrototypeOwner'] = 'other'
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed['extension']['subkeys'].append('extra')
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed['command']['values'][''] = '"C:\\other.exe" "%1"'
        variants.append(changed)
        for variant in variants:
            self.assertFalse(editor_ready.pristine_owned(variant, old))
        # A moved/deleted old executable is still identifiable by its exact record.
        self.assertFalse(Path(old).exists())
        self.assertTrue(editor_ready.pristine_owned(original, old))

    def test_userchoice_key_blocks_even_without_progid(self):
        info = {'account': r'PC\user', 'exe': True, 'core': True, 'loader': True,
                'dotnet': True, 'webview': True, 'user_choice': None,
                'user_choice_present': True, 'association_records': True,
                'effective_command': '"C:\\other.exe" "%1"', 'inspection_error': None}
        self.assertEqual(editor_ready.classify(info, r'PC\user', Path('C:/new.exe')), 'protected_choice')


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.old = str(Path('C:/synthetic/old editor.exe').absolute())
        self.new = Path('C:/synthetic/new editor.exe').absolute()
        self.account = r'PC\user'
        self.info = {'account': self.account, 'exe': True, 'core': True, 'loader': True,
                     'dotnet': True, 'webview': True, 'user_choice': None,
                     'user_choice_present': False, 'association_records': True,
                     'effective_command': '"' + self.old + '" "%1"', 'inspection_error': None,
                     'ownership': 'kpl_owned'}
        self.before = {'status': 'association_conflict', 'checks': self.info,
                       'plan': {'current_target': self.old, 'requested_target': str(self.new)}}
        self.after = {'status': 'ready', 'checks': {}, 'plan': {'check_is_read_only': True}}

    def test_check_is_read_only_and_shows_old_new_and_stale_path(self):
        with mock.patch.object(editor_ready, 'probe', return_value=self.info):
            result = editor_ready.check(self.new, self.account)
        self.assertEqual(result['plan']['next_step'], 'authorize_owned_rebind')
        self.assertEqual(result['plan']['current_target'], self.old)
        self.assertEqual(result['plan']['requested_target'], str(self.new))
        self.assertFalse(result['plan']['current_target_exists'])
        self.assertTrue(result['plan']['check_is_read_only'])

    def test_foreign_and_protected_connections_offer_system_choice(self):
        for info in ({**self.info, 'ownership': 'other'},
                     {**self.info, 'user_choice_present': True}):
            with mock.patch.object(editor_ready, 'probe', return_value=info):
                result = editor_ready.check(self.new, self.account)
            self.assertEqual(result['plan']['next_step'], 'user_choose_in_windows')

    def test_owned_rebind_requires_specific_original_path_and_authorization(self):
        cases = [(None, 'approval', 'specific_rebind_approval_required'),
                 (self.old, None, 'not_modified'),
                 ('C:/synthetic/different.exe', 'approval', 'not_modified')]
        for expected, approval, action in cases:
            with self.subTest(expected=expected), \
                 mock.patch.object(editor_ready, 'check', return_value=self.before), \
                 mock.patch.object(editor_ready.subprocess, 'run') as invoke:
                result = editor_ready.setup(self.new, self.account, approval, expected)
                invoke.assert_not_called()
                self.assertEqual(result['action'], action)
        with mock.patch.object(editor_ready, 'check', return_value=self.before), \
             mock.patch.object(editor_ready.subprocess, 'run') as invoke:
            result = editor_ready.setup(self.new, self.account, None, self.old)
            self.assertEqual(result['status'], 'authorization_required')
            invoke.assert_not_called()

    def test_absent_association_requires_authorization_then_verifies_shell(self):
        before = {'status': 'needs_setup', 'checks': {}, 'plan': {}}
        with mock.patch.object(editor_ready, 'check', return_value=before), \
             mock.patch.object(editor_ready.subprocess, 'run') as invoke:
            result = editor_ready.setup(self.new, self.account, None)
            self.assertEqual(result['status'], 'authorization_required')
            invoke.assert_not_called()
        completed = SimpleNamespace(returncode=0, stdout='', stderr='')
        with mock.patch.object(editor_ready, 'check', side_effect=[before, self.after]), \
             mock.patch.object(editor_ready.subprocess, 'run', return_value=completed) as invoke:
            result = editor_ready.setup(self.new, self.account, '用户同意首次配置')
            self.assertEqual(result['action'], 'installed')
            self.assertIn('Install', invoke.call_args.args[0])

    def test_rebind_must_recheck_actual_shell_target_after_authorized_write(self):
        completed = SimpleNamespace(returncode=0, stdout='', stderr='')
        with mock.patch.object(editor_ready, 'check', side_effect=[self.before, self.after]) as checks, \
             mock.patch.object(editor_ready.subprocess, 'run', return_value=completed) as invoke:
            result = editor_ready.setup(self.new, self.account, '用户具体同意两个路径', self.old)
        self.assertEqual(checks.call_count, 2)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['action'], 'rebound')
        cmd = invoke.call_args.args[0]
        self.assertIn('Rebind', cmd)
        self.assertIn('-ConfirmRebind', cmd)
        self.assertEqual(cmd[cmd.index('-ExpectedExecutable') + 1], self.old)
        self.assertFalse(any('bypass' in str(part).lower() for part in cmd))

    def test_foreign_protected_and_wrong_user_never_write(self):
        foreign = {**self.before, 'checks': {**self.info, 'ownership': 'other'}}
        for before in (foreign, {'status': 'protected_choice'}, {'status': 'wrong_user_context'},
                       {'status': 'runtime_missing'}, {'status': 'editor_missing'}):
            with self.subTest(before=before), mock.patch.object(editor_ready, 'check', return_value=before), \
                 mock.patch.object(editor_ready.subprocess, 'run') as invoke:
                self.assertEqual(editor_ready.setup(self.new, self.account, 'approval', self.old)['action'],
                                 'not_modified')
                invoke.assert_not_called()

    def test_denial_timeout_and_unverified_result_do_not_claim_success(self):
        for side_effect, status in [(PermissionError('denied'), 'setup_denied'),
                                    (subprocess.TimeoutExpired('powershell', 30), 'setup_unconfirmed')]:
            with self.subTest(status=status), \
                 mock.patch.object(editor_ready, 'check', return_value=self.before), \
                 mock.patch.object(editor_ready.subprocess, 'run', side_effect=side_effect):
                result = editor_ready.setup(self.new, self.account, 'approval', self.old)
                self.assertEqual(result['status'], status)
        denied = SimpleNamespace(returncode=1, stdout='', stderr='SecurityError: denied')
        for post in (self.before, self.after):
            with mock.patch.object(editor_ready, 'check', side_effect=[self.before, post]), \
                 mock.patch.object(editor_ready.subprocess, 'run', return_value=denied):
                result = editor_ready.setup(self.new, self.account, 'approval', self.old)
                self.assertEqual(result['status'], 'setup_unconfirmed')
                self.assertEqual(result['action'], 'inspect_before_retry')
        completed = SimpleNamespace(returncode=0, stdout='', stderr='')
        with mock.patch.object(editor_ready, 'check', side_effect=[self.before, self.before]), \
             mock.patch.object(editor_ready.subprocess, 'run', return_value=completed):
            result = editor_ready.setup(self.new, self.account, 'approval', self.old)
            self.assertEqual(result['action'], 'inspect_before_retry')


if __name__ == '__main__':
    unittest.main()
