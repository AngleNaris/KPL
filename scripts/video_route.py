#!/usr/bin/env python3
"""Select a KPL video-writing route from explicitly known generation mode."""
import argparse
import json
import re
import sys


def select_route(deployment=None, model=None):
    """Return a routing decision, not evidence that any platform or input was verified."""
    if deployment not in ('online', 'local'):
        return {'status': 'needs_clarification', 'question': '这次使用在线平台还是本地生成？',
                'route': None, 'inputs_verified_by_tool': False}
    if deployment == 'online':
        return {'status': 'route_selected', 'route': 'online_general', 'read': ['references/video.md'],
                'do_not_autoload': ['references/formats/minimax-h3.md', 'minimax-h3-prompt-writing'],
                'note': '在线统一自然语言；实际界面的参考绑定、参数及限制须另行核对。',
                'inputs_verified_by_tool': False}
    key = re.sub(r'[\s_-]+', '', (model or '').casefold())
    if key in {'h3', 'h3base', 'minimaxh3', 'minimaxh3base'}:
        return {'status': 'route_selected', 'route': 'local_minimax_h3',
                'read': ['references/video.md', 'references/formats/minimax-h3.md'],
                'note': '继续核对实际本地工作流、节点及参考输入；工具不证明可执行。',
                'inputs_verified_by_tool': False}
    return {'status': 'needs_clarification', 'route': None,
            'question': 'KPL 当前本地只设有 MiniMax H3 专项路径；请确认生成方式与本地模型。',
            'note': '这不判断其他模型是否能在本地运行。', 'inputs_verified_by_tool': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description='按已确认的在线/本地方式选择 KPL 视频提示词路径。')
    parser.add_argument('--deployment', choices=('online', 'local'), help='用户明确的生成方式')
    parser.add_argument('--model', help='用户明确的模型；在线路径不因模型名切换')
    args = parser.parse_args(argv)
    result = select_route(args.deployment, args.model)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'route_selected' else 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
