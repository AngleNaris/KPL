"""Exercise route selection without connecting to or generating with any model."""
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'video_route.py'
loader = importlib.util.spec_from_file_location('video_route_test', SCRIPT)
route = importlib.util.module_from_spec(loader)
loader.loader.exec_module(route)


class VideoRouteTests(unittest.TestCase):
    def test_every_online_model_uses_general_not_local_h3(self):
        for model in ('MiniMax H3', 'Seedance 2.5', '其他模型', None):
            with self.subTest(model=model):
                result = route.select_route('online', model)
                self.assertEqual(result['route'], 'online_general')
                self.assertEqual(result['read'], ['references/video.md'])
                self.assertIn('references/formats/minimax-h3.md', result['do_not_autoload'])
                self.assertFalse(result['inputs_verified_by_tool'])

    def test_local_h3_routes_to_existing_guide(self):
        for model in ('MiniMax H3', 'MINIMAX-H3', 'H3', 'MiniMax H3 Base'):
            result = route.select_route('local', model)
            self.assertEqual(result['route'], 'local_minimax_h3')
            self.assertIn('references/formats/minimax-h3.md', result['read'])
            self.assertFalse(result['inputs_verified_by_tool'])

    def test_missing_mode_and_unsupported_local_models_require_question(self):
        for deployment, model in ((None, 'MiniMax H3'), ('local', None), ('local', 'Seedance 2.5'),
                            ('local', '其他模型')):
            with self.subTest(deployment=deployment, model=model):
                result = route.select_route(deployment, model)
                self.assertEqual(result['status'], 'needs_clarification')
                self.assertIsNone(result['route'])
                self.assertTrue(result['question'])

    def test_cli_does_not_claim_surface_or_binding_was_verified(self):
        result = subprocess.run([sys.executable, str(SCRIPT), '--deployment', 'online', '--model', 'MiniMax H3'],
                                capture_output=True, text=True, encoding='utf-8', check=True)
        output = json.loads(result.stdout)
        self.assertEqual(output['route'], 'online_general')
        self.assertFalse(output['inputs_verified_by_tool'])
        result = subprocess.run([sys.executable, str(SCRIPT), '--model', 'MiniMax H3'],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['status'], 'needs_clarification')


if __name__ == '__main__': unittest.main()
