"""Offline delivery-material audit; no model, database, or remote Git access."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from urllib.parse import unquote
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'examples/submission/delivery-audit.json'
PRUNE = {'.git', '.venv', 'node_modules', '.runtime', '__pycache__',
         '.pytest_cache', '.ruff_cache', 'dist'}


def read_json(path: str):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))


def main() -> int:
    checks = []

    def record(name, passed, details=None):
        checks.append({'name': name, 'passed': bool(passed), 'details': details})

    files = []
    for current, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in PRUNE]
        files.extend((Path(current) / name).relative_to(ROOT).as_posix() for name in names)
    files.sort()
    required = ['README.md', 'docs/01-requirements.md', 'docs/02-solution.md',
                'docs/03-evaluation.md', 'docs/04-ai-usage.md', 'docs/05-retrospective.md',
                'requirements.txt', 'requirements.lock.txt', 'package.json',
                'package-lock.json', '.env.example', 'config/app.example.yaml']
    record('required_files_nonempty', all((ROOT / p).is_file() and (ROOT / p).stat().st_size for p in required), required)
    record('required_directories_have_materials', all(any(p.startswith(d + '/') for p in files)
           for d in ['src', 'tests', 'examples', 'data', 'docs']))

    # Test actual ignore patterns in a disposable repository, never init this project.
    probes = ['.env', 'config/app.yaml', '.runtime/probe', '.venv/probe',
              'node_modules/probe', 'src/frontend/dist/probe']
    with tempfile.TemporaryDirectory(prefix='studio-delivery-') as temporary:
        temporary_root = Path(temporary)
        subprocess.run(['git', 'init', '--quiet', temporary], check=True, capture_output=True)
        for name in files:
            if Path(name).name == '.gitignore':
                dest = temporary_root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes((ROOT / name).read_bytes())
        result = subprocess.run(['git', '-C', temporary, '-c', 'core.excludesFile=' + os.devnull,
                                 'check-ignore', '--no-index', '-z', '--stdin'],
                                input=b'\0'.join(p.encode('utf-8') for p in files + probes) + b'\0',
                                capture_output=True)
        if result.returncode not in (0, 1):
            raise RuntimeError('Git ignore verification failed')
        ignored = {p.decode('utf-8') for p in result.stdout.split(b'\0') if p}
    candidates = [p for p in files if p not in ignored]
    candidate_set = set(candidates)
    record('local_configuration_and_generated_directories_ignored', all(p in ignored for p in probes))
    record('required_files_are_submission_candidates', all(p in candidate_set for p in required))
    evidence_files = [p for p in files if p.startswith('examples/') and
                      Path(p).suffix.lower() in {'.json', '.md', '.txt', '.xml', '.zip', '.png', '.html'}]
    record('verification_materials_not_ignored', not [p for p in evidence_files if p in ignored],
           [p for p in evidence_files if p in ignored])

    manifest = read_json('examples/submission/manifest.json')
    coverage = set()
    case_reports = []
    for case in manifest['cases']:
        evidence = read_json(case['evidence'])
        saved = read_json(case['saved_proposals'])
        state = evidence['state']
        active = state.get('active_run')
        actual_status = active['status'] if active else 'no_run'
        passed = (bool(evidence['inputs']) and bool(evidence['workspace']) and bool(state)
                  and isinstance(evidence['tool_calls'], list) and bool(state['model_calls'])
                  and bool(evidence['events']) and bool(evidence['jobs'])
                  and evidence['passed'] and all(evidence['checks'].values())
                  and evidence.get('external_service_fixture') is False
                  and actual_status == case['expected_status']
                  and len(saved) == case['expected_proposal_count']
                  and case['evidence'] in candidate_set and case['saved_proposals'] in candidate_set)
        coverage.update(case['coverage'])
        case_reports.append({'name': case['name'], 'actual_status': actual_status,
                             'input_count': len(evidence['inputs']),
                             'tool_attempt_count': len(evidence['tool_calls']),
                             'event_count': len(evidence['events']),
                             'saved_count': len(saved), 'passed': bool(passed),
                             'evidence_sha256': hashlib.sha256((ROOT / case['evidence']).read_bytes()).hexdigest()})
        record('actual_case:' + case['name'], passed)
    record('at_least_three_actual_cases', len(case_reports) >= 3)
    mandatory = {'normal', 'missing_budget', 'missing_deadline', 'tool_failure',
                 'budget_exceeded', 'repeated_execution'}
    record('all_required_acceptance_points_covered', mandatory <= coverage, sorted(coverage))

    review = read_json('examples/external-live/after/review-audit.json')
    record('recorded_core_review_all_passed', review['passed'] and len(review['checks']) == 27
           and all(review['checks'].values()))
    index = read_json('examples/external-live/after/index.json')
    audit = read_json('examples/external-live/after/audit.json')
    record('all_eight_real_scenarios_and_fact_audits_passed', len(index) == len(audit) == 8
           and all(row['passed'] for row in index + audit))
    suites = ET.parse(ROOT / 'examples/external-live/backend.xml').getroot()
    totals = {key: sum(int(s.get(key, 0)) for s in suites.iter('testsuite'))
              for key in ['tests', 'failures', 'errors', 'skipped']}
    record('recorded_backend_results_170_without_failures_or_skips',
           totals == {'tests': 170, 'failures': 0, 'errors': 0, 'skipped': 0}, totals)
    summary = read_json('examples/external-live/summary.json')
    mismatches = [p for p, digest in summary['source_hashes'].items()
                  if hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != digest]
    record('audited_business_code_matches_real_test_snapshot', not mismatches, mismatches)

    design = (ROOT / 'docs/02-solution.md').read_text(encoding='utf-8')
    python_lock = (ROOT / 'requirements.lock.txt').read_text(encoding='utf-8')
    npm_lock = read_json('package-lock.json')['packages']
    versions = {}
    for package in ['fastapi', 'pydantic', 'SQLAlchemy', 'psycopg', 'alembic',
                    'langgraph', 'langgraph-checkpoint-postgres', 'mcp', 'pytest']:
        match = re.search(r'^' + re.escape(package) + r'==([^;\s]+)', python_lock, re.M | re.I)
        versions[package] = match.group(1) if match else None
    for package in ['react', 'vite', 'typescript', 'antd', 'react-router-dom',
                    '@tanstack/react-query', '@playwright/test']:
        versions[package] = npm_lock['node_modules/' + package]['version']
    record('framework_versions_documented_and_locked',
           all(value and value in design for value in versions.values()), versions)

    broken_links = []
    documents = ['README.md', 'execution-plan.md', 'examples/README.md']
    documents += [p for p in candidates if p.startswith('docs/') and p.endswith('.md')]
    for name in documents:
        for link in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', (ROOT / name).read_text(encoding='utf-8')):
            target = link.strip().split(' "', 1)[0].strip('<>')
            if not target or target.startswith('#') or re.match(r'^[\w+.-]+:', target):
                continue
            target = unquote(target.split('#', 1)[0])
            resolved = (ROOT / name).parent / target
            if not resolved.exists() and resolved.resolve() != OUTPUT:
                broken_links.append({'document': name, 'target': target})
    record('local_document_links_resolve', not broken_links, broken_links)

    env_example = (ROOT / '.env.example').read_text(encoding='utf-8')
    variables = ['DEEPSEEK_API_KEY', 'TAVILY_API_KEY']
    record('credential_template_has_empty_keys', all(re.search(r'^' + name + r'=\s*$', env_example, re.M)
           for name in variables))
    # Check only the currently configured credentials; never include their values in output.
    configured = dict(os.environ)
    if (ROOT / '.env').is_file():
        for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() in variables:
                configured[key.strip()] = value.strip().strip('"\'')
    secrets = [configured[name].encode('utf-8') for name in variables
               if configured.get(name) and len(configured[name]) >= 12]
    leaks = []
    for name in candidates:
        raw = (ROOT / name).read_bytes()
        if any(secret in raw for secret in secrets):
            leaks.append(name)
        if name.endswith('.zip'):
            with zipfile.ZipFile(ROOT / name) as archive:
                for member in archive.infolist():
                    if not member.is_dir() and any(secret in archive.read(member) for secret in secrets):
                        leaks.append(name + ':' + member.filename)
    record('configured_credentials_absent_from_candidates_and_zip_members', not leaks,
           {'credentials_checked': len(secrets), 'files': leaks,
            'scope': 'current configured API keys only; not a general secret scanner'})
    largest = sorted(candidates, key=lambda p: (ROOT / p).stat().st_size, reverse=True)[:5]
    large = [p for p in candidates if (ROOT / p).stat().st_size >= 100 * 1024 * 1024]
    record('individual_candidate_files_below_100_mib', not large, large)
    total_bytes = sum((ROOT / name).stat().st_size for name in candidates)
    passed = all(check['passed'] for check in checks)
    report = {'recorded_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'offline audit of local delivery materials; no live requests or database access',
              'local_delivery_complete': passed,
              'repository_metadata_present': (ROOT / '.git').exists(),
              'github_publication_verified': False,
              'remote_ci_executed_by_this_check': False,
              'passed': passed, 'checks': checks, 'cases': case_reports,
              'candidate_file_count': len(candidates), 'candidate_bytes': total_bytes,
              'largest_candidates': [{'path': p, 'bytes': (ROOT / p).stat().st_size} for p in largest]}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Local delivery audit:', sum(c['passed'] for c in checks), '/', len(checks))
    print('GitHub publication: not verified; local repository metadata:', report['repository_metadata_present'])
    print('Candidate files:', len(candidates), 'size MiB:', round(total_bytes / 2**20, 2))
    for check in checks:
        if not check['passed']:
            print('FAIL:', check['name'])
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
