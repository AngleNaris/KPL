#!/usr/bin/env python3
"""Read-only integrity and resume hints for a kpl-state block in a creative spec.

This cannot verify that a person or agent saw pixels, understood a document, or
watched video; it reports recorded claims and asks the caller to verify traces.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import importlib.util
from pathlib import Path

_paths_spec = importlib.util.spec_from_file_location('kpl_project_paths', Path(__file__).with_name('project_paths.py'))
project_paths = importlib.util.module_from_spec(_paths_spec)
_paths_spec.loader.exec_module(project_paths)

PHASES = {'brief', 'rhythm', 'shot_group', 'shot_spec', 'asset_prep', 'sample_review', 'edit_review'}
KINDS = {'technical', 'document', 'design', 'image', 'video'}
ID = re.compile(r'^[A-Za-z][A-Za-z0-9_.-]{0,63}$')
SHA = re.compile(r'^[a-f0-9]{64}$')
REVIEW_STAGES = ('global', 'directing', 'prompts', 'assets', 'deliverables')
REVIEW_DEPS = {'global': (), 'directing': ('global',), 'prompts': ('global', 'directing'),
               'assets': ('global', 'directing', 'prompts'), 'deliverables': REVIEW_STAGES[:-1]}
REVIEW_DONE = {'passed', 'failed', 'blocked', 'unverified'}
SUMMARY_FIELDS = ('conclusion', 'finding', 'impact', 'coverage', 'next_step')
REQUIRED_SETTINGS = ('resolution', 'fps', 'aspect_ratio')


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def load_state(spec: Path):
    text = spec.read_text(encoding='utf-8-sig')
    blocks = re.findall(r'^```kpl-state\s*\r?\n([\s\S]*?)^```\s*$', text, re.MULTILINE)
    if not blocks:
        return None
    if len(blocks) != 1:
        raise ValueError('spec must contain exactly one kpl-state block')
    value = json.loads(blocks[0], object_pairs_hook=unique_object,
                       parse_constant=lambda s: (_ for _ in ()).throw(ValueError(f'invalid number: {s}')))
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('expected kpl-state object with version 1')
    return value


def load_external_state(path: Path, spec: Path):
    """Load a derived machine state, requiring an explicit source spec/version."""
    state = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique_object,
                       parse_constant=lambda s: (_ for _ in ()).throw(ValueError(f'invalid number: {s}')))
    if not isinstance(state, dict) or state.get('version') != 1:
        raise ValueError('expected external kpl-state object with version 1')
    binding = state.get('spec_binding')
    if not isinstance(binding, dict) or not isinstance(binding.get('path_from_state'), str):
        raise ValueError('external state must reference the actual spec and its sha256')
    expected = binding.get('sha256')
    if not isinstance(expected, str) or not SHA.fullmatch(expected):
        raise ValueError('external state spec sha256 must be lowercase hex')
    declared = (path.resolve().parent / binding['path_from_state']).resolve()
    actual_path = spec.resolve()
    actual_hash = sha_file(actual_path)
    return state, {'status': 'current' if declared == actual_path and actual_hash == expected else 'stale',
                   'declared_spec': str(declared), 'actual_spec': str(actual_path),
                   'expected_sha256': expected, 'actual_sha256': actual_hash}


def board_coverage(root, evidence, artifacts, files, inputs):
    """Check an overview page's registered index/source mapping, not actual viewing."""
    page_id, index_id = evidence.get('artifact'), evidence.get('board_index')
    if (page_id not in artifacts or index_id not in artifacts or
            artifacts[page_id].get('kind') != 'image' or
            files.get(page_id, {}).get('status') != 'current' or
            files.get(index_id, {}).get('status') != 'current' or
            evidence.get('board_index_sha256') != files[index_id]['sha256']):
        return None
    index_path = (root / artifacts[index_id]['path']).resolve()
    page_path = (root / artifacts[page_id]['path']).resolve()
    try:
        index = json.loads(index_path.read_text(encoding='utf-8-sig'))
        if index.get('schema') != 'kpl-review-board-v1' or index.get('mode') not in {'sequence', 'cut'}:
            return None
        if project_paths.selection_issues(index_path, index):
            return None
        pages = [p for p in index['pages'] if isinstance(p, dict) and isinstance(p.get('file'), str)
                 and project_paths.output_file(index_path, index, p['file']) == page_path
                 and p.get('sha256') == files[page_id]['sha256']]
        if len(pages) != 1:
            return None
        page_no = pages[0]['page']
        cells = [c for c in index['cells'] if isinstance(c, dict) and c.get('page') == page_no
                 and c.get('source_status') == 'ok' and c.get('image_rendered') is True]
        covered = set()
        for aid in inputs:
            if artifacts[aid].get('kind') not in {'image', 'reference_image'} or files[aid]['status'] != 'current':
                continue
            source = (root / artifacts[aid]['path']).resolve()
            if any(isinstance(c.get('source_path'), str) and os.path.normcase(str(project_paths.input_file(index_path, index, c['source_path']))) == os.path.normcase(str(source))
                   and c.get('source_sha256') == files[aid]['sha256']
                   for c in cells):
                covered.add(aid)
        return covered if covered else None
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def check_review_round(root, state, artifacts, files, requirements, required=False):
    """Validate recorded review ordering and versions, never delivery or content quality."""
    review = state.get('review_round')
    if review is None:
        return ({'status': 'missing', 'next_stage': 'global', 'stages': [],
                 'delivery_verified_by_script': False, 'semantics_verified_by_script': False}
                if required else None)
    if not isinstance(review, dict) or not isinstance(review.get('id'), str) or not ID.fullmatch(review['id']):
        raise ValueError('review_round needs a valid id')
    if review.get('mode') not in {'review', 'production'} or not str(review.get('scope') or '').strip():
        raise ValueError('review_round needs review/production mode and scope')
    stages = review.get('stages')
    if not isinstance(stages, list) or [s.get('id') if isinstance(s, dict) else None for s in stages] != list(REVIEW_STAGES):
        raise ValueError('review_round stages must contain all five IDs in the required order')
    questions = review.get('questions', [])
    if not isinstance(questions, list):
        raise ValueError('review_round questions must be an array')
    question_ids = set()
    for q in questions:
        if (not isinstance(q, dict) or not isinstance(q.get('id'), str) or not ID.fullmatch(q['id'])
                or q['id'] in question_ids or q.get('status') not in {'open', 'answered'}
                or not isinstance(q.get('topic'), str) or not q['topic'].strip()
                or not isinstance(q.get('question'), str) or not q['question'].strip()
                or not isinstance(q.get('stages'), list) or not q['stages']
                or any(s not in REVIEW_STAGES for s in q['stages'])):
            raise ValueError('questions need unique IDs, topic, question, affected stages and status')
        if q['status'] == 'answered' and any(not isinstance(q.get(k), str) or not q[k].strip()
                                             for k in ('answer', 'answer_source')):
            raise ValueError(f"{q['id']}: an answer needs its actual source")
        question_ids.add(q['id'])
    settings = review.get('settings', {})
    if not isinstance(settings, dict):
        raise ValueError('review_round settings must be an object')
    missing_settings = []
    for key in REQUIRED_SETTINGS:
        value = settings.get(key)
        if (not isinstance(value, dict) or not str(value.get('value') or '').strip()
                or not str(value.get('source') or '').strip()
                or value.get('authority') not in {'confirmed', 'delegated'}):
            missing_settings.append(key)

    results, bases, by_id, prior_incomplete = [], {}, {}, False
    for stage in stages:
        sid = stage['id']
        status = stage.get('status')
        if status not in {'pending', 'running'} | REVIEW_DONE:
            raise ValueError(f'{sid}: invalid review status')
        inputs, reqs = stage.get('inputs', []), stage.get('requirements', [])
        if (not isinstance(inputs, list) or len(set(inputs)) != len(inputs)
                or any(not isinstance(a, str) or a not in artifacts for a in inputs)):
            raise ValueError(f'{sid}: inputs must be unique artifact IDs')
        if (not isinstance(reqs, list) or len(set(reqs)) != len(reqs)
                or any(not isinstance(r, str) or r not in requirements for r in reqs)):
            raise ValueError(f'{sid}: requirements must be unique requirement IDs')
        blocks = stage.get('blocks', [])
        if (not isinstance(blocks, list) or len(set(blocks)) != len(blocks)
                or any(not isinstance(b, str) or b not in REVIEW_STAGES[REVIEW_STAGES.index(sid) + 1:] for b in blocks)
                or (status == 'passed' and blocks)):
            raise ValueError(f'{sid}: blocks must name later stages and cannot accompany passed')
        report = stage.get('report') or {}
        if not isinstance(report, dict):
            raise ValueError(f'{sid}: report must be an object')
        report_core = {k: v for k, v in report.items() if k not in {'delivery', 'delivery_trace'}}
        core = {k: v for k, v in stage.items() if k not in {'basis', 'report'}}
        relevant_questions = [q for q in questions if sid in q['stages']]
        basis = digest({'review_id': review['id'], 'mode': review['mode'], 'scope': review['scope'],
                        'stage': core, 'report': report_core,
                        'inputs': {a: {'artifact': artifacts.get(a), 'file': files.get(a)} for a in inputs},
                        'requirements': {r: requirements.get(r) for r in reqs},
                        'settings': settings if sid == 'global' else None, 'questions': relevant_questions,
                        'dependencies': {d: bases[d] for d in REVIEW_DEPS[sid]}})
        bases[sid] = basis
        problems, trace_checks = [], []
        blocked_by = [d for d in REVIEW_STAGES[:REVIEW_STAGES.index(sid)]
                      if sid in by_id[d].get('blocks', [])]
        open_questions = [q['id'] for q in relevant_questions if q['status'] == 'open']
        checks = stage.get('checks', [])
        if not isinstance(checks, list) or any(not isinstance(c, dict) or
                not isinstance(c.get('name'), str) or not c['name'].strip() or
                c.get('result') not in {'checked', 'issue', 'missing', 'not_applicable'} or
                not isinstance(c.get('observation'), str) or not c['observation'].strip() or
                (c.get('result') == 'not_applicable' and not str(c.get('reason') or '').strip()) for c in checks):
            raise ValueError(f'{sid}: checks need name, result, observation and a reason for not_applicable')
        evidence = stage.get('evidence', [])
        if not isinstance(evidence, list):
            raise ValueError(f'{sid}: evidence must be an array')
        good, board_sources = [], set()
        for ev in evidence:
            if (not isinstance(ev, dict) or any(not isinstance(ev.get(k), str) or not ev[k].strip()
                                                for k in ('method', 'trace', 'observation'))):
                problems.append('evidence_incomplete')
                continue
            aid = ev.get('artifact')
            if aid is None:
                if ev['method'] != 'read_user_message':
                    problems.append('evidence_missing_artifact')
                    continue
            elif (aid not in artifacts or files.get(aid, {}).get('status') != 'current'
                  or ev.get('sha256') != files[aid]['sha256']):
                problems.append('evidence_stale')
                continue
            if ev['method'] == 'view_board':
                covered = board_coverage(root, ev, artifacts, files, inputs)
                if covered is None:
                    problems.append('board_mapping_invalid_or_stale')
                    continue
                board_sources.update(covered)
            good.append(ev)
            trace_checks.append(ev['trace'])

        report_ok = False
        delivery = 'unconfirmed'
        if status in REVIEW_DONE:
            summary = stage.get('summary')
            aid = report.get('artifact')
            if (not isinstance(summary, dict) or any(not isinstance(summary.get(k), str) or not summary[k].strip()
                                                    for k in SUMMARY_FIELDS)):
                problems.append('report_summary_missing')
            elif (aid not in artifacts or artifacts[aid].get('kind') != 'document'
                  or files.get(aid, {}).get('status') != 'current' or report.get('sha256') != files[aid]['sha256']):
                problems.append('report_missing_or_stale')
            else:
                try:
                    content = (root / artifacts[aid]['path']).resolve().read_text(encoding='utf-8-sig')
                except (OSError, UnicodeError):
                    content = ''
                if any(summary[k] not in content for k in SUMMARY_FIELDS):
                    problems.append('report_summary_not_in_file')
                else:
                    report_ok = True
            delivery_claim = report.get('delivery', 'generated_only')
            if delivery_claim not in {'generated_only', 'claimed_user_visible'}:
                problems.append('delivery_claim_invalid')
                report_ok = False
            if delivery_claim == 'claimed_user_visible':
                if not str(report.get('delivery_trace') or '').strip():
                    problems.append('delivery_trace_missing')
                    report_ok = False
                else:
                    trace_checks.append(report['delivery_trace'])

            if not checks:
                problems.append('checks_missing')
            for c in checks:
                if c.get('result') == 'missing' and c.get('question_id') not in question_ids:
                    problems.append('missing_question:' + c['name'])
            if sid == 'global':
                for field in missing_settings:
                    if not any(q['topic'] == field and q['status'] == 'open' for q in relevant_questions):
                        problems.append('missing_question:' + field)
            if status in {'blocked', 'unverified'} and not str(stage.get('reason') or '').strip():
                problems.append('uncertainty_reason_missing')
            if status == 'failed' and (not good or not any(c.get('result') == 'issue' for c in checks)):
                problems.append('failure_needs_evidence_and_issue')
            if sid == 'deliverables' and status in {'passed', 'failed'} and not any(
                    e.get('artifact') == aid and e['method'] == 'read_text' for e in good):
                problems.append('report_self_check_missing')
            if status == 'passed':
                if open_questions or (sid == 'global' and missing_settings):
                    problems.append('unanswered_questions_or_settings')
                if not inputs or not good:
                    problems.append('passed_without_inputs_or_evidence')
                if any(c.get('result') in {'issue', 'missing'} for c in checks):
                    problems.append('passed_with_unresolved_check')
                for inp in inputs:
                    kind = artifacts[inp].get('kind')
                    method = 'view_image' if kind in {'image', 'reference_image'} else 'read_text'
                    if kind in {'image', 'reference_image'} and inp in board_sources and not any(
                            e.get('artifact') == inp and e['method'] == 'view_image' for e in good):
                        problems.append('overview_only_unverified:' + inp)
                    elif files[inp]['status'] != 'current' or not any(e.get('artifact') == inp and e['method'] == method for e in good):
                        problems.append('input_not_reviewed:' + inp)
                role = {'directing': 'shot_design', 'prompts': 'video_prompt', 'deliverables': 'deliverable'}.get(sid)
                if role and not any(artifacts[a].get('role') == role for a in inputs):
                    problems.append('required_input_missing:' + role)
                if sid == 'global' and not (any(artifacts[a].get('role') == 'user_source' for a in inputs)
                                            or any(e['method'] == 'read_user_message' for e in good)):
                    problems.append('original_user_source_unchecked')
                if sid == 'assets' and not any(artifacts[a].get('kind') in {'image', 'reference_image'} for a in inputs):
                    problems.append('required_input_missing:image')
        if prior_incomplete:
            effective, action = 'blocked_order', 'finish_earlier_stage_and_report'
        elif blocked_by and status not in {'blocked', 'unverified'}:
            effective, action = 'blocked_dependency', 'inspect_unaffected_scope_or_report_block'
        elif status in {'pending', 'running'}:
            effective, action = status, ('execute_checks' if status == 'pending' else 'resume_checks')
        elif not report_ok:
            effective, action = 'needs_report', 'write_or_recover_stage_report'
        elif status == 'passed' and (any(files[a]['status'] in {'missing', 'invalid', 'unreadable'} for a in inputs)
                                     or any(p.startswith('required_input_missing:') for p in problems)):
            effective, action = 'blocked_input', 'locate_or_ask_about_missing_input'
        elif stage.get('basis') != basis:
            effective, action = 'stale', 'recheck_current_inputs_and_record_basis'
        elif problems:
            effective, action = 'needs_evidence', 'inspect_inputs_and_fix_record'
        else:
            effective, action = ('recorded_pass' if status == 'passed' else 'recorded_' + status), 'verify_traces_before_reuse'
        complete = effective in {'recorded_pass', 'recorded_failed', 'recorded_blocked', 'recorded_unverified'}
        result = {'id': sid, 'status': effective, 'action': action, 'basis': basis, 'problems': problems,
                  'blocks': blocks, 'blocked_by': blocked_by, 'report_delivery': delivery,
                  'open_questions': open_questions, 'overview_source_coverage': sorted(board_sources),
                  'trace_checks_required': trace_checks, 'delivery_verified_by_script': False,
                  'semantics_verified_by_script': False}
        results.append(result)
        by_id[sid] = result
        if not complete:
            prior_incomplete = True

    next_stage = next((r['id'] for r in results if not r['status'].startswith('recorded_')), None)
    if next_stage is None:
        overall = ('recorded_needs_fix' if any(r['status'] == 'recorded_failed' for r in results)
                   else 'recorded_unverified' if any(r['status'] in {'recorded_blocked', 'recorded_unverified'} for r in results)
                   else 'recorded_pass')
    else:
        overall = 'in_progress'
    return {'id': review['id'], 'status': overall, 'next_stage': next_stage, 'stages': results,
            'missing_settings': missing_settings,
            'questions_to_ask_or_verify': [q for q in questions if q['status'] == 'open'],
            'delivery_verified_by_script': False, 'semantics_verified_by_script': False}


def check(spec: Path, state: dict, allowed_roots=None, require_project_review=False) -> dict:
    root = spec.resolve().parent
    allowed = [root] + [Path(p).resolve() for p in (allowed_roots or [])]
    errors, warnings, traces = [], [], []

    def error(code, detail, task=None, scope=None):
        errors.append({'code': code, 'detail': detail, **({'task': task} if task else {}),
                       **({'scope': scope} if scope else {})})

    def entries(key):
        value = state.get(key, [])
        if not isinstance(value, list):
            raise ValueError(f'{key} must be an array')
        result = {}
        for item in value:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not ID.fullmatch(item['id']):
                raise ValueError(f'{key} item must have a valid id')
            if item['id'] in result:
                raise ValueError(f'duplicate {key} id: {item["id"]}')
            result[item['id']] = item
        return result

    requirements, artifacts, tasks = entries('requirements'), entries('artifacts'), entries('tasks')
    cuts, issues, source_audits = entries('cuts'), entries('issues'), entries('source_audits')
    for rid, req in requirements.items():
        if not isinstance(req.get('text'), str) or not req['text'].strip() or not isinstance(req.get('source'), str) or not req['source'].strip():
            error('requirement_source', f'{rid}: text and source are required', scope=f'requirement:{rid}')
        if req.get('applicable', True) is False and not str(req.get('reason') or '').strip():
            error('na_reason', f'{rid}: not applicable needs a reason', scope=f'requirement:{rid}')
        if not isinstance(req.get('constraints', {}), dict):
            error('bad_constraints', f'{rid}: constraints must be an object', scope=f'requirement:{rid}')

    files = {}
    for aid, artifact in artifacts.items():
        name = artifact.get('path')
        if not isinstance(name, str) or not name.strip():
            error('artifact_path', f'{aid}: path required', scope=f'artifact:{aid}')
            files[aid] = {'status': 'invalid', 'sha256': None}
            continue
        path = (root / name).resolve()
        if not any(path.is_relative_to(parent) for parent in allowed):
            error('artifact_outside_project', f'{aid}: outside the spec directory or explicitly allowed roots', scope=f'artifact:{aid}')
            files[aid] = {'status': 'invalid', 'sha256': None}
            continue
        expected = artifact.get('sha256')
        if expected is not None and (not isinstance(expected, str) or not SHA.fullmatch(expected)):
            error('artifact_version', f'{aid}: sha256 must be lowercase hex or null', scope=f'artifact:{aid}')
        if not path.is_file():
            files[aid] = {'status': 'missing', 'sha256': None}
        else:
            try:
                actual = sha_file(path)
                files[aid] = {'status': 'current' if expected == actual else 'untracked' if expected is None else 'changed',
                              'sha256': actual}
            except OSError:
                files[aid] = {'status': 'unreadable', 'sha256': None}

    def refs(task, key, pool):
        value = task.get(key, [])
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value) or len(set(value)) != len(value):
            error('bad_refs', f'{task["id"]}: {key} must be an array of unique IDs', task['id'])
            return []
        for item in value:
            if item not in pool:
                error('missing_reference', f'{task["id"]}: {key} references {item}', task['id'])
        return value

    graph, task_refs = {}, {}
    for tid, task in tasks.items():
        if task.get('phase') not in PHASES:
            error('bad_phase', f'{tid}: phase must use a KPL stage', tid)
        if task.get('status') not in {'pending', 'running', 'done', 'paused'}:
            error('bad_status', f'{tid}: unknown status', tid)
        if not isinstance(task.get('action'), str) or not task['action'].strip():
            error('missing_action', f'{tid}: action required', tid)
        if task.get('operation') not in {'write', 'generate', 'review', 'edit', 'other'}:
            error('missing_operation', f'{tid}: classify operation as write, generate, review, edit or other', tid)
        capabilities = task.get('required_capabilities', [])
        if not isinstance(capabilities, list) or any(not isinstance(c, str) or not c.strip() for c in capabilities):
            error('bad_capabilities', f'{tid}: required_capabilities must be an array of names', tid)
        if task.get('operation') == 'generate':
            attempts = task.get('attempts', [])
            limit = task.get('max_attempts', 3)
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                error('bad_retry_budget', f'{tid}: max_attempts must be a positive integer', tid)
            if not isinstance(attempts, list) or any(not isinstance(a, dict) or a.get('status') not in
                    {'submitted', 'running', 'unknown', 'failed', 'succeeded'} or
                    (a.get('status') == 'unknown' and a.get('job_ref') is not None and
                     (not isinstance(a.get('job_ref'), str) or not a['job_ref'].strip())) or
                    (a.get('status') == 'unknown' and not a.get('job_ref') and
                     (not isinstance(a.get('uncertainty'), str) or not a['uncertainty'].strip())) or
                    (a.get('status') != 'unknown' and (not isinstance(a.get('job_ref'), str) or not a['job_ref'].strip()))
                    for a in attempts):
                error('bad_attempts', f'{tid}: record job_ref, or unknown status and uncertainty when no receipt arrived', tid)
            elif len({a['job_ref'] for a in attempts if a.get('job_ref')}) != len([a for a in attempts if a.get('job_ref')]):
                error('bad_attempts', f'{tid}: duplicate job_ref', tid)
        if not isinstance(task.get('shots', []), list) or any(not isinstance(s, str) or not s.strip() for s in task.get('shots', [])):
            error('bad_shots', f'{tid}: shots must be an array of IDs', tid)
            task = {**task, 'shots': []}
            tasks[tid] = task
        task_refs[tid] = {k: refs(task, k, pool) for k, pool in
                          [('depends_on', tasks), ('requirements', requirements), ('inputs', artifacts), ('outputs', artifacts)]}
        graph[tid] = task_refs[tid]['depends_on']
        required = task.get('required_reviews', [])
        if (not isinstance(required, list) or any(not isinstance(k, str) or k not in KINDS for k in required)
                or len(set(required)) != len(required)):
            error('bad_reviews', f'{tid}: required_reviews invalid', tid)
            required = []
        output_kinds = {artifacts.get(a, {}).get('kind') for a in task_refs[tid]['outputs']}
        for kind, relevant_kinds in [('document', {'document'}), ('image', {'image', 'reference_image'}), ('video', {'video'})]:
            if output_kinds & relevant_kinds and kind not in required:
                error('required_review_missing', f'{tid}: {kind} output needs its actual review', tid)
        if task.get('status') == 'done' and not required:
            error('missing_review_policy', f'{tid}: completed task needs explicit review types', tid)
        not_applicable = task.get('not_applicable', {})
        if not isinstance(not_applicable, dict) or any(k not in KINDS or not isinstance(v, str) or not v.strip()
                                                        for k, v in not_applicable.items()):
            error('na_reason', f'{tid}: not_applicable needs review types and reasons', tid)
        else:
            for kind in not_applicable:
                if kind in required or (kind == 'image' and output_kinds & {'image', 'reference_image'}) or (kind == 'video' and 'video' in output_kinds):
                    error('bad_na', f'{tid}: {kind} is required or its output exists', tid)

    mapped = {rid for refs_ in task_refs.values() for rid in refs_['requirements']}
    unmapped = [rid for rid, req in requirements.items() if req.get('applicable', True) is not False and rid not in mapped]
    for rid in unmapped:
        error('requirement_unmapped', f'{rid}: no task links this requirement', scope=f'requirement:{rid}')

    order, visited, visiting = [], set(), set()
    def visit(tid):
        if tid in visiting:
            raise ValueError(f'dependency cycle at {tid}')
        if tid in visited:
            return
        visiting.add(tid)
        for dep in graph[tid]:
            if dep in tasks:
                visit(dep)
        visiting.remove(tid)
        visited.add(tid)
        order.append(tid)
    for tid in tasks:
        visit(tid)

    cut_errors = []
    for cid, cut in cuts.items():
        if any(not isinstance(cut.get(k), str) or not cut[k].strip() for k in ('from', 'to')):
            cut_errors.append((cid, 'cut_scope', 'from and to shot IDs are required'))
            continue
        kind = cut.get('kind')
        if kind not in {'continuous', 'montage', 'contrast'}:
            cut_errors.append((cid, 'cut_kind', 'kind must be continuous, montage or contrast'))
            continue
        if kind in {'montage', 'contrast'}:
            if not isinstance(cut.get('purpose'), str) or not cut['purpose'].strip():
                cut_errors.append((cid, 'cut_purpose', 'intentional montage/contrast needs a purpose'))
        left, right, exceptions = cut.get('exit', {}), cut.get('entry', {}), cut.get('exceptions', {})
        if not all(isinstance(x, dict) for x in (left, right, exceptions)):
            cut_errors.append((cid, 'cut_shape', 'entry, exit and exceptions must be objects'))
            continue
        if any(not isinstance(v, str) or not v.strip() for v in exceptions.values()):
            cut_errors.append((cid, 'cut_exception', 'each exception needs an explicit reason'))
            continue
        if kind in {'montage', 'contrast'} or exceptions:
            ref = cut.get('intent_ref')
            valid_ref = isinstance(ref, dict) and isinstance(ref.get('trace'), str) and bool(ref['trace'].strip())
            if valid_ref and isinstance(ref.get('requirement'), str):
                req = requirements.get(ref['requirement'], {})
                valid_ref = bool(req.get('text') and req.get('source'))
            elif valid_ref and isinstance(ref.get('artifact'), str):
                aid = ref['artifact']
                valid_ref = (artifacts.get(aid, {}).get('kind') == 'document' and files.get(aid, {}).get('status') == 'current'
                             and isinstance(ref.get('excerpt'), str) and bool(ref['excerpt'].strip()))
                if valid_ref:
                    try:
                        valid_ref = ref['excerpt'] in (root / artifacts[aid]['path']).resolve().read_text(encoding='utf-8-sig')
                    except (OSError, UnicodeError):
                        valid_ref = False
            else:
                valid_ref = False
            if not valid_ref:
                cut_errors.append((cid, 'cut_intent_missing', 'intentional discontinuity needs a traceable upstream design basis'))
            else:
                traces.append({'kind': 'cut_intent', 'trace': ref['trace'], 'cut': cid})
        if kind in {'montage', 'contrast'}:
            continue
        for key in left.keys() & right.keys():
            if left[key] != right[key] and not str(exceptions.get(key) or '').strip():
                cut_errors.append((cid, 'cut_conflict', f'{key}: {left[key]} -> {right[key]}'))
    for cid, code, detail in cut_errors:
        error(code, f'{cid}: {detail}', scope=f'cut:{cid}')
    for iid, issue in issues.items():
        if issue.get('task') not in tasks:
            error('issue_scope', f'{iid}: task does not exist')

    for audit_id, audit in source_audits.items():
        scope = f'source_audit:{audit_id}'
        aid, target_tasks = audit.get('source_artifact'), audit.get('tasks')
        if not isinstance(aid, str) or aid not in artifacts or artifacts.get(aid, {}).get('kind') != 'document':
            error('source_audit_source', f'{audit_id}: source_artifact must identify a document', scope=scope)
            continue
        if not isinstance(target_tasks, list) or not target_tasks or any(not isinstance(t, str) or t not in tasks for t in target_tasks):
            error('source_audit_scope', f'{audit_id}: tasks must identify existing affected tasks', scope=scope)
            continue
        if any(aid not in task_refs[t]['inputs'] for t in target_tasks):
            error('source_audit_scope', f'{audit_id}: each task must bind the original source as an input', scope=scope)
        trace = audit.get('trace')
        if not isinstance(trace, str) or not trace.strip():
            error('source_trace_missing', f'{audit_id}: record an actual source-reading trace', scope=scope)
        else:
            traces.append({'kind': 'source_audit', 'trace': trace, 'source_audit': audit_id})
        if files.get(aid, {}).get('status') != 'current':
            error('source_audit_stale', f'{audit_id}: original source file is missing or changed', scope=scope)
            continue
        try:
            original = (root / artifacts[aid]['path']).resolve().read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError):
            error('source_audit_unreadable', f'{audit_id}: cannot read source as UTF-8', scope=scope)
            continue
        items = audit.get('items')
        if not isinstance(items, list) or not items:
            error('source_audit_empty', f'{audit_id}: extract original requirements independently', scope=scope)
            continue
        item_ids = set()
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not ID.fullmatch(item['id']) or item['id'] in item_ids:
                error('source_item_id', f'{audit_id}: source items need unique IDs', scope=scope)
                continue
            item_ids.add(item['id'])
            excerpt = item.get('excerpt')
            if not isinstance(excerpt, str) or not excerpt.strip() or excerpt not in original:
                error('source_item_excerpt', f'{audit_id}/{item["id"]}: excerpt absent from original source', scope=scope)
            mapped_ids = item.get('requirements', [])
            if item.get('applicable', True) is False:
                if isinstance(item.get('reason'), str) and item['reason'].strip():
                    continue
                error('na_reason', f'{audit_id}/{item["id"]}: not applicable needs a reason', scope=scope)
                continue
            if not isinstance(mapped_ids, list) or not mapped_ids or any(not isinstance(r, str) or r not in requirements for r in mapped_ids):
                error('source_item_unmapped', f'{audit_id}/{item["id"]}: original requirement has no valid mapping', scope=scope)
            elif any(not any(r in task_refs[t]['requirements'] for t in target_tasks) for r in mapped_ids):
                error('source_item_unmapped', f'{audit_id}/{item["id"]}: mapped requirement has no affected task', scope=scope)

    for tid, task in tasks.items():
        for aid in task_refs[tid]['inputs']:
            if artifacts.get(aid, {}).get('role') == 'user_source' and not any(
                    audit.get('source_artifact') == aid and tid in audit.get('tasks', [])
                    for audit in source_audits.values() if isinstance(audit.get('tasks', []), list)):
                error('source_audit_missing', f'{tid}: independently map original source {aid}', tid)

    bases, reports = {}, {}
    for tid in order:
        task = tasks[tid]
        r = task_refs[tid]
        core = {k: v for k, v in task.items() if k not in {'status', 'reviews', 'updated', 'attempts', 'job_ref'}}
        associated_cuts = [c for c in cuts.values() if c.get('from') in task.get('shots', [])
                           or c.get('to') in task.get('shots', [])]
        cut_sources = {}
        for cut in associated_cuts:
            ref = cut.get('intent_ref')
            if isinstance(ref, dict):
                if isinstance(ref.get('requirement'), str):
                    cut_sources[cut['id']] = requirements.get(ref['requirement'])
                elif isinstance(ref.get('artifact'), str):
                    cut_sources[cut['id']] = files.get(ref['artifact'])
        basis = digest({'task': core, 'requirements': {i: requirements.get(i) for i in r['requirements']},
                        'files': {i: files.get(i) for i in r['inputs'] + r['outputs']},
                        'dependencies': {i: bases.get(i) for i in r['depends_on']},
                        'source_audits': [a for a in source_audits.values()
                                          if isinstance(a.get('tasks'), list) and tid in a['tasks']],
                        'cuts': associated_cuts, 'cut_sources': cut_sources})
        bases[tid] = basis
        scopes = {f'requirement:{i}' for i in r['requirements']} | {f'artifact:{i}' for i in r['inputs'] + r['outputs']}
        scopes |= {f'cut:{cid}' for cid, cut in cuts.items()
                   if cut.get('from') in task.get('shots', []) or cut.get('to') in task.get('shots', [])}
        scopes |= {f'source_audit:{aid}' for aid, audit in source_audits.items()
                   if isinstance(audit.get('tasks'), list) and tid in audit['tasks']}
        local = [e for e in errors if e.get('task') == tid or e.get('scope') in scopes]
        if task.get('phase') == 'edit_review':
            local.extend(e for e in errors if e.get('code') == 'requirement_unmapped' and e not in local)
            local.extend(e for e in errors if str(e.get('scope', '')).startswith('source_audit:') and e not in local)
            if not task.get('shots'):
                local.append({'code': 'edit_scope_unknown', 'detail': 'edit review must identify the ordered shot scope'})
            delivery = task.get('delivery')
            if (not isinstance(delivery, dict) or not isinstance(delivery.get('artifact'), str) or delivery.get('artifact') not in r['outputs'] or
                    artifacts.get(delivery.get('artifact'), {}).get('kind') != 'video'):
                local.append({'code': 'delivery_missing', 'detail': 'edit review must bind the final video output'})
            elif task.get('status') == 'done':
                if delivery.get('observed_shots') != task.get('shots'):
                    local.append({'code': 'delivery_sequence', 'detail': 'observed final sequence differs from expected shots'})
                versions = delivery.get('source_versions')
                shots = task.get('shots', [])
                if not isinstance(versions, list) or len(versions) != len(shots):
                    local.append({'code': 'delivery_versions', 'detail': 'bind each expected shot to its actual source version'})
                else:
                    for shot_id, source in zip(shots, versions):
                        if (not isinstance(source, dict) or source.get('shot') != shot_id or source.get('artifact') not in r['inputs']
                                or files.get(source.get('artifact'), {}).get('status') != 'current'
                                or source.get('sha256') != files.get(source.get('artifact'), {}).get('sha256')):
                            local.append({'code': 'delivery_versions', 'detail': f'{shot_id}: source version is missing or stale'})
                text_required = delivery.get('text_required')
                if not isinstance(text_required, bool):
                    local.append({'code': 'delivery_text', 'detail': 'state whether text/subtitles are required'})
                elif text_required and (delivery.get('text_status') != 'passed' or not str(delivery.get('text_observation') or '').strip()):
                    local.append({'code': 'delivery_text', 'detail': 'required text needs a real visual check and observations'})
                if text_required:
                    expected_text, observed_text = delivery.get('expected_text'), delivery.get('observed_text')
                    if (not isinstance(expected_text, list) or not expected_text or any(not isinstance(e, dict) or
                            e.get('requirement') not in r['requirements'] or not isinstance(e.get('text'), str) or not e['text']
                            for e in expected_text) or not isinstance(observed_text, list) or
                            [e['text'] for e in expected_text] != observed_text):
                        local.append({'code': 'delivery_text_mismatch', 'detail': 'expected and observed text must match applicable requirements'})
                elif not text_required and (delivery.get('text_status') != 'not_applicable' or not str(delivery.get('text_reason') or '').strip()):
                    local.append({'code': 'delivery_text', 'detail': 'not-applicable text check needs a reason'})
        checks = []
        raw_required = task.get('required_reviews', [])
        required = [k for k in raw_required if isinstance(k, str) and k in KINDS] if isinstance(raw_required, list) else []
        reviews = task.get('reviews', [])
        if not isinstance(reviews, list):
            reviews = []
            local.append({'code': 'bad_review_records', 'detail': 'reviews must be an array'})
        reviews_by_id = {v.get('id'): v for v in reviews if isinstance(v, dict) and isinstance(v.get('id'), str)}
        if len(reviews_by_id) != len(reviews):
            local.append({'code': 'duplicate_review', 'detail': 'reviews need unique IDs'})
        relevant = r['inputs'] + r['outputs']
        for kind in required:
            options = [v for v in reviews if isinstance(v, dict) and v.get('kind') == kind]
            if options and options[-1].get('decision') == 'needs_fix' and options[-1].get('basis') == basis:
                checks.append({'kind': kind, 'status': 'needs_fix'})
                continue
            valid = []
            # Only the latest record may represent the current conclusion.
            for review in options[-1:]:
                if review.get('decision') != 'passed' or review.get('basis') != basis:
                    continue
                evidence = review.get('evidence')
                if not isinstance(evidence, list) or not evidence:
                    continue
                good = []
                for ev in evidence:
                    if not isinstance(ev, dict) or not all(isinstance(ev.get(k), str) and ev[k].strip()
                            for k in ('method', 'trace', 'observation')):
                        continue
                    aid = ev.get('artifact')
                    if aid is not None and (aid not in relevant or files.get(aid, {}).get('status') != 'current'
                                            or ev.get('sha256') != files[aid]['sha256']):
                        continue
                    good.append(ev)
                if not good:
                    continue
                if kind == 'document':
                    docs = [a for a in relevant if artifacts.get(a, {}).get('kind') == 'document']
                    if not docs or any(not any(e.get('artifact') == a and e['method'] == 'read_text' for e in good) for a in docs):
                        continue
                if kind in {'image', 'video'}:
                    target_kinds = {'image', 'reference_image'} if kind == 'image' else {'video'}
                    targets = [a for a in relevant if artifacts.get(a, {}).get('kind') in target_kinds]
                    output_kinds = {'image', 'reference_image'} if kind == 'image' else {'video'}
                    candidates = r['outputs'] or (r['inputs'] if task.get('operation') == 'review' else [])
                    if not any(artifacts.get(a, {}).get('kind') in output_kinds for a in candidates):
                        continue
                    method = 'view_image' if kind == 'image' else 'view_video_1x'
                    if any(not any(e.get('artifact') == a and e['method'] == method and
                                   (kind != 'video' or not task.get('audio_required') or
                                    (a not in r['outputs'] and bool(r['outputs'])) or e.get('audio_observed') is True)
                                   for e in good) for a in targets):
                        continue
                valid.append((review, good))
            if not valid:
                checks.append({'kind': kind, 'status': 'unverified'})
            else:
                review, evidence = valid[-1]
                checks.append({'kind': kind, 'status': 'recorded', 'review_id': review['id'],
                               'trace_verified_by_script': False})
                traces.extend({'task': tid, 'kind': kind, 'trace': e['trace']} for e in evidence)

        if 'document' in required and task.get('status') == 'done':
            claims = task.get('claims', [])
            if not isinstance(claims, list):
                claims = []
            for rid in r['requirements']:
                req = requirements.get(rid, {})
                if req.get('applicable', True) is False and str(req.get('reason') or '').strip():
                    continue
                rows = [c for c in claims if isinstance(c, dict) and c.get('requirement') == rid]
                if not rows:
                    local.append({'code': 'requirement_uncovered', 'detail': f'{rid}: no document claim'})
                    continue
                for claim in rows:
                    aid, excerpt = claim.get('artifact'), claim.get('excerpt')
                    doc = artifacts.get(aid, {})
                    if aid not in relevant or doc.get('kind') != 'document' or files.get(aid, {}).get('status') != 'current' or not isinstance(excerpt, str) or not excerpt.strip():
                        local.append({'code': 'claim_unverified', 'detail': f'{rid}: current document and excerpt required'})
                        continue
                    try:
                        content = (root / doc['path']).resolve().read_text(encoding='utf-8-sig')
                    except (OSError, UnicodeError):
                        local.append({'code': 'claim_unverified', 'detail': f'{rid}: document cannot be read as UTF-8'})
                        continue
                    if excerpt not in content:
                        local.append({'code': 'claim_missing_excerpt', 'detail': f'{rid}: excerpt absent from document'})
                    values = claim.get('values', {})
                    constraints = req.get('constraints', {})
                    if not isinstance(values, dict) or not isinstance(constraints, dict):
                        local.append({'code': 'claim_unverified', 'detail': f'{rid}: values/constraints must be objects'})
                    else:
                        for key, expected in constraints.items():
                            if key not in values:
                                local.append({'code': 'constraint_uncovered', 'detail': f'{rid}: {key} not checked'})
                            elif values[key] != expected or isinstance(values[key], bool) != isinstance(expected, bool):
                                local.append({'code': 'constraint_conflict', 'detail': f'{rid}: {key} differs'})

        unresolved = []
        for iid, issue in issues.items():
            if issue.get('task') != tid:
                continue
            severity, status = issue.get('severity'), issue.get('status')
            if severity not in {'blocker', 'major', 'minor'} or status not in {'open', 'resolved', 'accepted'}:
                unresolved.append(iid)
            elif status == 'open':
                if severity in {'blocker', 'major'}:
                    unresolved.append(iid)
                else:
                    warnings.append({'code': 'minor_issue', 'detail': iid, 'task': tid})
            elif status == 'accepted':
                if not str(issue.get('reason') or '').strip() or not str(issue.get('user_decision_ref') or '').strip():
                    unresolved.append(iid)
                else:
                    warnings.append({'code': 'risk_accepted', 'detail': iid, 'task': tid})
            elif status == 'resolved':
                review = reviews_by_id.get(issue.get('recheck_review_id'))
                if not review or review.get('id') == issue.get('opened_review_id') or not any(c.get('review_id') == review.get('id') for c in checks):
                    unresolved.append(iid)

        bad_files = [a for a in relevant if files.get(a, {}).get('status') != 'current']
        dep_not_ready = [d for d in r['depends_on'] if reports.get(d, {}).get('status') != 'recorded_pass']
        affecting_cuts = [c for c, _, _ in cut_errors if cuts[c].get('from') in task.get('shots', []) or cuts[c].get('to') in task.get('shots', [])]
        attempts = task.get('attempts', []) if task.get('operation') == 'generate' and isinstance(task.get('attempts', []), list) else []
        active = [a for a in attempts if isinstance(a, dict) and a.get('status') in {'submitted', 'running', 'unknown'}]
        outstanding = active + ([attempts[-1]] if attempts and isinstance(attempts[-1], dict)
                                and attempts[-1].get('status') == 'succeeded' else [])
        limit = task.get('max_attempts', 3)
        budget_exhausted = isinstance(limit, int) and not isinstance(limit, bool) and len(attempts) >= limit
        missing_repair = bool(attempts and isinstance(attempts[-1], dict) and attempts[-1].get('status') == 'failed'
                              and not str(attempts[-1].get('repair') or '').strip())
        if task.get('status') == 'paused':
            status, action = 'paused', 'wait_for_resolution'
        elif task.get('status') == 'running' or active or (task.get('status') != 'done' and outstanding):
            status, action = 'running_or_unknown', 'check_existing_job'
        elif dep_not_ready:
            status, action = 'blocked_dependency', 'wait_for_dependency'
        elif local or unresolved or affecting_cuts or any(c['status'] == 'needs_fix' for c in checks):
            status, action = 'needs_fix', 'repair_and_recheck'
        elif task.get('status') == 'done':
            if bad_files:
                status, action = 'stale', 'inspect_or_restore_files'
            elif any(c['status'] != 'recorded' for c in checks) or not required:
                status, action = 'needs_review', 'review_current_outputs'
            else:
                status, action = 'recorded_pass', 'verify_traces_before_reuse'
        elif any(files.get(a, {}).get('sha256') for a in r['outputs']):
            status, action = 'output_exists', 'inspect_existing_output'
        elif budget_exhausted:
            status, action = 'retry_budget_exhausted', 'wait_for_resolution'
        elif missing_repair:
            status, action = 'needs_fix', 'repair_and_recheck'
        elif any(files.get(a, {}).get('status') != 'current' for a in r['inputs']):
            status, action = 'blocked_input', 'inspect_inputs'
        else:
            status, action = 'ready', 'execute_with_authorization'
        caps = [c for c in task.get('required_capabilities', []) if isinstance(c, str) and c.strip()] if isinstance(task.get('required_capabilities', []), list) else []
        inferred = {'document': 'read_document', 'image': 'view_image', 'video': 'view_video_1x'}
        caps = list(dict.fromkeys(caps + [inferred[k] for k in required if k in inferred]
                                  + (['generate'] if task.get('operation') == 'generate' else [])))
        reports[tid] = {'id': tid, 'status': status, 'action': action, 'basis': basis, 'checks': checks,
                        'problems': local, 'unresolved_issues': unresolved, 'blocked_by': dep_not_ready,
                        'files_requiring_attention': bad_files,
                        'job_ref': task.get('job_ref') or (outstanding[-1].get('job_ref') if outstanding else None),
                        'capabilities_to_verify': caps, 'capabilities_verified_by_script': False}

    ready = [reports[tid] for tid in order if reports[tid]['action'] in
             {'execute_with_authorization', 'inspect_existing_output', 'review_current_outputs',
              'inspect_or_restore_files', 'repair_and_recheck', 'check_existing_job'}]
    if any(e.get('task') is None and e.get('scope') is None for e in errors):
        ready = []
    review_round = check_review_round(root, state, artifacts, files, requirements, require_project_review)
    if require_project_review:
        # Review mode is diagnostic: do not present production tasks as work to run.
        ready = []
    result = {'schema': 'kpl-check-report-v1', 'spec': str(spec), 'artifacts': files,
              'errors': errors, 'warnings': warnings, 'tasks': [reports[t] for t in order],
              'next_candidates': ready, 'trace_checks_required': traces,
              'script_verifies_viewing_or_semantics': False,
              'note': 'Recorded evidence is not proof of viewing, delivery or semantic correctness; verify traces and inspect as needed.'}
    if review_round is not None:
        result['review_round'] = review_round
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec', type=Path, nargs='?', help='creative-spec Markdown with optional kpl-state block')
    parser.add_argument('--state', type=Path, help='separate machine state JSON bound to this spec; legacy embedded blocks still work')
    parser.add_argument('--hash', type=Path, help='read-only SHA256 of a single actual file')
    parser.add_argument('--allow-root', type=Path, action='append', default=[],
                        help='explicitly allow reading artifact files under this additional root')
    parser.add_argument('--review', action='store_true',
                        help='require the five-stage project review; do not suggest production tasks')
    args = parser.parse_args(argv)
    try:
        if args.hash:
            print(json.dumps({'path': str(args.hash), 'sha256': sha_file(args.hash)}, ensure_ascii=False))
            return 0
        if not args.spec:
            parser.error('provide a spec path or --hash')
        binding = None
        if args.state:
            state, binding = load_external_state(args.state, args.spec)
            if binding['status'] != 'current':
                result = {'schema': 'kpl-check-report-v1', 'state_binding': binding, 'next_candidates': [],
                          'script_verifies_viewing_or_semantics': False,
                          'note': '规格或选择版本已改变；检查影响并复核状态，不能执行旧任务或沿用旧验收。'}
                if args.review:
                    result['review_round'] = {'status': 'spec_changed_requires_review', 'next_stage': None,
                                              'stages': [], 'delivery_verified_by_script': False,
                                              'semantics_verified_by_script': False}
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 1
        else:
            state = load_state(args.spec)
        if state is None:
            result = {'schema': 'kpl-check-report-v1', 'legacy': True, 'next_candidates': [],
                      'script_verifies_viewing_or_semantics': False,
                      'note': 'No state block; read the existing spec and outputs. No migration was performed.'}
            if args.review:
                result['review_round'] = {'status': 'missing', 'next_stage': 'global', 'stages': [],
                                          'delivery_verified_by_script': False, 'semantics_verified_by_script': False}
        else:
            result = check(args.spec, state, args.allow_root, args.review)
        if binding is not None:
            result['state_binding'] = binding
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc), 'script_verifies_viewing_or_semantics': False}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
