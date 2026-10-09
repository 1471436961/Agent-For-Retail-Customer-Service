"""Push once, then read the matching managed GitHub evaluation. Python stdlib + gh."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlparse


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Platform redirects are not accepted')


class LiveReceipt:
    """Read the same owner's existing GitHub job; never submits or runs student code."""
    def __init__(self, config_path):
        path = Path(config_path).expanduser().resolve()
        config = json.loads(path.read_text(encoding='utf-8-sig'))
        self.base = config.get('platformUrl', '').rstrip('/')
        url = urlparse(self.base)
        hosts = {'agentist.org', 'test.agentist.org', 'parallight-lab-git-staging-mrvgaos-projects.vercel.app'}
        if (url.scheme != 'https' or url.username or url.password or url.port or url.query or url.fragment
            or url.path != '/lab/api/enterprise-ai' or not (url.hostname in hosts or
                re.fullmatch(r'parallight-[a-z0-9]+-mrvgaos-projects\.vercel\.app', url.hostname or ''))):
            raise ValueError('Invalid platform URL')
        token_path = config.get('tokenFile')
        self.token = (path.parent / token_path).read_text(encoding='utf-8').strip() if token_path else config.get('token')
        if not re.fullmatch(r'[\x21-\x7e]{32,512}', self.token or ''):
            raise ValueError('Invalid credential')
        self.job_id = None
        self.cursor = 0

    def get(self, path):
        request = urllib.request.Request(self.base + path, headers={'Authorization': 'Bearer ' + self.token})
        with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
            data = response.read(600001)
            if len(data) > 600000:
                raise ValueError('Receipt too large')
            return json.loads(data)

    def poll(self, repo, branch, sha, run_id):
        def matches(row):
            github = row.get('github') or {}
            return (row.get('commit_sha') == sha and str(github.get('run_id')) == str(run_id)
                    and github.get('repository') == repo and github.get('branch') == branch)
        if self.job_id is None:
            jobs = self.get('/v1/evaluations?github_run_id=' + str(int(run_id))).get('jobs', [])
            matching_jobs = [job for job in jobs if matches(job)]
            if not matching_jobs:
                return
            self.job_id = matching_jobs[0]['job_id']
            if not re.fullmatch(r'[a-f0-9-]{36}', self.job_id):
                raise ValueError('Invalid job ID')
        receipt = self.get('/v1/evaluations/' + self.job_id)
        if not matches(receipt):
            raise ValueError('Run identity mismatch')
        self.display(receipt)

    def display(self, receipt):
        labels = {'system': '测试', 'user': '模拟用户', 'authority': '授权负责人',
                  'agent': 'Agent 回复', 'tool': '工具调用', 'result': 'Agent 返回结果'}
        for event in sorted(receipt.get('interaction_events') or [], key=lambda e: e['seq']):
            if event['seq'] <= self.cursor:
                continue
            if event['seq'] != self.cursor + 1:
                say('[对话记录缺段，等待下一次同步]')
                break
            self.cursor = event['seq']
            say(f"\n[{event['case_id']}/{event['scenario']} #{event['seq']}] {labels.get(event['role'], '记录')}\n{event['text']}")


def say(message):
    # Reports are untrusted text: strip terminal control sequences.
    print(re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", "", str(message)), flush=True)


def run(args):
    return subprocess.check_output(args, text=True, encoding="utf-8", errors="replace", stderr=subprocess.PIPE, timeout=60)


def api(path):
    return json.loads(run(["gh", "api", path]))


def targets(output):
    """Read actual updated refs from Git's porcelain output, not local HEAD."""
    repo = None
    updates = []
    for line in output.splitlines():
        if line.startswith("To "):
            match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?", line[3:])
            repo = match.group(1) if match else None
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0] in (" ", "+", "*"):
            src, dest = parts[1].split(":", 1)
            if src and dest.startswith("refs/heads/"):
                updates.append((src, dest[11:]))
    return repo, updates


def matching(runs, sha, branch, since):
    return [r for r in runs if r.get("head_sha") == sha and r.get("head_branch") == branch
            and r.get("event") == "push" and r.get("path", "").split("@")[0] == ".github/workflows/hyper-lab.yml"
            and r.get("created_at", "") >= since]


def render(receipt):
    report = receipt.get("report") or {}
    cases = report.get("cases") or []
    counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "OTHER": 0}
    say("\n──────── 远程评测结果 ────────")
    say(f"任务: {receipt.get('task', '?')} | Job: {receipt.get('job_id', '?')} | Commit: {receipt.get('commit_sha', '?')}")
    for case in sorted(cases, key=lambda c: c.get("status") == "passed"):
        status = case.get("status")
        label = {"passed": "PASS", "failed": "FAIL", "error": "ERROR"}.get(status, "OTHER")
        counts[label] += 1
        say(f"  {label:5} {case.get('case_id', '?')} [{status}]")
        if label != "PASS":
            say(f"        {case.get('feedback') or '未提供判定详情'}")
        for name, value in (case.get("checks") or {}).items():
            say(f"        {'✓' if value is True else '✗' if value is False else '·'} {name}: {value}")
    total = report.get("total", len(cases))
    say(f"合计 {total} | 通过 {counts['PASS']} | 未通过 {counts['FAIL']} | 执行错误 {counts['ERROR']} | 其他 {counts['OTHER']}")
    if len(cases) != total:
        say(f"注意：只收到 {len(cases)} 条用例结果，不能视为全部执行完成。")
    if not cases:
        say("未收到用例结果；这不是测试通过。请查看工作流与网页错误信息。")
    say(f"最终判定: {report.get('verdict', receipt.get('status', 'unknown'))}")
    if receipt.get("web_url"):
        say(f"网页详情与历史入口: {receipt['web_url']}")
    if receipt.get("task") == "t1":
        say("t1 仅为接入检查，不代表完整业务评测通过。")
    simulation = report.get('simulation')
    if simulation:
        say(f"模拟用户测试：{simulation['passed']}/{simulation['total']} 通过，执行 {simulation['completed']}/{simulation['total']}（观察项，不计分）")
        for item in simulation.get('results', []):
            say(f"  {item['status']} {item['case_id']}/{item['scenario']}")
    return 0 if cases and len(cases) == total and counts['PASS'] == total and report.get('verdict') == 'passed' else 1


def watch(repo, branch, sha, since, timeout=1800, discover_timeout=120, live=None):
    started = time.monotonic()
    selected = None
    failures = 0
    say(f"\n推送已成功。等待远程评测: {repo} / {branch} / {sha[:12]}")
    while time.monotonic() - started < timeout:
        elapsed = int(time.monotonic() - started)
        try:
            if selected is None:
                candidates = matching(api(f"repos/{repo}/actions/runs?head_sha={sha}&event=push&per_page=100").get("workflow_runs", []), sha, branch, since)
                if candidates:
                    selected = max(candidates, key=lambda r: r["id"])
                    say(f"Actions: {selected['html_url']}")
                elif elapsed >= discover_timeout:
                    say("未发现对应评测工作流。请检查绑定分支、hyper-lab.yml、Actions 权限，以及本次是否修改了 agent/。没有重复提交。")
                    return 2
            if selected:
                selected = api(f"repos/{repo}/actions/runs/{selected['id']}")
                jobs = api(f"repos/{repo}/actions/runs/{selected['id']}/jobs").get("jobs", [])
                steps = [s.get('name', '') for j in jobs for s in j.get('steps', []) if s.get('status') == 'in_progress']
                say(f"[{elapsed:>4}s] {selected['status']} | {', '.join(steps) or '等待执行器 / 报告'}")
                if live:
                    try:
                        live.poll(repo, branch, sha, selected['id'])
                    except (OSError, ValueError):
                        say('实时记录暂不可用，将重试；不影响远程任务，最终仍读取 GitHub 报告。')
                if selected["status"] == "completed":
                    artifacts = api(f"repos/{repo}/actions/runs/{selected['id']}/artifacts").get("artifacts", [])
                    if not any(a.get("name") == f"enterprise-ai-{sha}" and not a.get("expired") for a in artifacts):
                        say(f"GitHub 工作流已结束（{selected.get('conclusion')}），但没有评测报告；不能判定 Agent 通过或失败。")
                        for job in jobs:
                            if job.get("conclusion") not in {"failure", "cancelled", "timed_out", "action_required"}:
                                continue
                            say("未完成的任务: " + job.get("name", ""))
                            match = re.search(r"/check-runs/(\d+)$", job.get("check_run_url", ""))
                            if match:
                                try:
                                    for note in api(f"repos/{repo}/check-runs/{match[1]}/annotations")[:5]:
                                        if note.get("annotation_level") == "failure":
                                            say("GitHub 原因: " + note.get("message", "")[:2000])
                                except (subprocess.SubprocessError, OSError, ValueError):
                                    pass
                        say(f"查看失败步骤: {selected['html_url']}")
                        return 2
                    with tempfile.TemporaryDirectory(prefix="enterprise-evaluation-") as folder:
                        run(["gh", "run", "download", str(selected["id"]), "--repo", repo,
                             "--name", f"enterprise-ai-{sha}", "--dir", folder])
                        receipt = json.loads((Path(folder) / "report.json").read_text(encoding="utf-8"))
                    if receipt.get("commit_sha") != sha or str((receipt.get("github") or {}).get("run_id")) != str(selected["id"]):
                        raise ValueError("报告 commit/run_id 与本次推送不一致，拒绝展示为本次结果")
                    if live:
                        live.display(receipt)
                    elif receipt.get('interaction_events'):
                        replay = object.__new__(LiveReceipt)
                        replay.cursor = 0
                        say('以下为评测结束后的记录回放（没有配置实时读取凭证）。')
                        replay.display(receipt)
                    # Store outside tracked working files; never overwrite the student's source.
                    gitdir = Path(run(["git", "rev-parse", "--absolute-git-dir"]).strip())
                    dest = gitdir / "enterprise-evaluations" / str(selected["id"])
                    dest.mkdir(parents=True, exist_ok=True)
                    (dest / "report.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
                    result = render(receipt)
                    say(f"本地记录: {dest / 'report.json'}")
                    say(f"GitHub 工作流: {selected.get('conclusion')}")
                    return result if selected.get('conclusion') == 'success' else 2
            else:
                say(f"[{elapsed:>4}s] 等待 GitHub 创建评测工作流…")
            failures = 0
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            failures += 1
            say(f"[{elapsed:>4}s] 获取评测状态/报告失败 ({type(exc).__name__})，重试 {failures}/3。")
            if failures >= 3:
                if selected:
                    say(f"请查看: {selected['html_url']}（可能尚无报告 artifact）")
                return 2
        time.sleep(5)
    say("本地等待超时；远程评测不会被取消。请到 GitHub Actions 或平台运行与反馈查看。")
    return 2


def main(argv):
    if not argv or argv[0] in ('--help', '-h'):
        say('用法: python3 scripts/git-evaluate.py push [git push 参数]\n或: python3 scripts/git-evaluate.py report /path/report.json\n退出码: 0=通过/无需等待；1=业务未通过；2=评测未确认。推送失败保留 Git 退出码。')
        return 0
    if argv[0] == 'report' and len(argv) == 2:
        return render(json.loads(Path(argv[1]).read_text(encoding="utf-8")))
    if argv[0] != 'push':
        raise ValueError('仅支持 push / report')
    if not shutil.which('gh'):
        say('请先安装 GitHub CLI 并 gh auth login。未执行推送。可用 command git push 跳过终端等待。')
        return 2
    if subprocess.run(['gh', 'auth', 'status'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        say('请先执行 gh auth login。未执行推送。')
        return 2
    # Small clock-skew allowance; SHA/branch/workflow are also verified.
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    proc = subprocess.Popen(['git', 'push', '--porcelain', *argv[1:]], stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    output = []
    for line in proc.stdout:
        output.append(line)
        say(line.rstrip('\n'))
    if proc.wait():
        say('Git 推送失败，未启动本地评测等待。')
        return proc.returncode
    if '--dry-run' in argv or '-n' in argv:
        say('dry-run：未等待评测。')
        return 0
    repo, refs = targets(''.join(output))
    if not repo or not refs:
        say('没有更新 GitHub 分支（或远程地址不受支持），未等待评测。')
        return 0
    result = 0
    live = None
    if os.environ.get('HYPER_LAB_CONFIG'):
        try:
            live = LiveReceipt(os.environ['HYPER_LAB_CONFIG'])
        except (OSError, ValueError):
            say('无法读取实时配置；保留 GitHub 进度及最终报告，不输出凭证详情。')
    else:
        say('未设置 HYPER_LAB_CONFIG：显示 GitHub 进度和最终回放；设置个人配置后可同步实时交互。')
    for src, branch in refs:
        sha = run(['git', 'rev-parse', f'{src}^{{commit}}']).strip()
        if live:
            live.job_id, live.cursor = None, 0
        result = max(result, watch(repo, branch, sha, since, live=live))
    return result


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='backslashreplace')
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        say('\n已停止本地等待；不会取消远程任务，也不会重复推送。请查看 GitHub Actions。')
        sys.exit(130)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        say(f'终端集成失败 ({type(exc).__name__})；请检查 GitHub Actions，勿据此判断推送失败。')
        sys.exit(2)
