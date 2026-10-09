// A cumulative, ordered receipt stream: reconnecting never submits a new job.
const labels = { system: '测试', user: '模拟用户', authority: '授权负责人',
  agent: 'Agent 回复', tool: '工具调用', result: 'Agent 返回结果' };
const safe = v => String(v ?? '').replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
  .replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, '');
export function interactionPrinter(write = console.log) {
  let cursor = 0, jobId = null;
  return job => {
    if (jobId !== job.job_id) { cursor = 0; jobId = job.job_id; }
    for (const e of [...(job.interaction_events || [])].sort((a, b) => a.seq - b.seq)) {
      if (!Number.isSafeInteger(e.seq) || e.seq <= cursor) continue;
      if (e.seq !== cursor + 1) { write('[对话记录缺段，等待下一次同步]'); break; }
      cursor = e.seq;
      write(`\n[${safe(e.case_id)}/${safe(e.scenario)} #${e.seq}] ${labels[e.role] || '记录'}\n${safe(e.text)}`);
    }
  };
}
export function simulationSummary(job) {
  const s = job.report?.simulation;
  if (!s) return '本轮未提供模拟用户测试（可能是旧版服务）；不能据此视为通过。';
  const rows = [`模拟用户测试：${s.passed}/${s.total} 通过；执行 ${s.completed}/${s.total}；${s.status}（观察项，不计分）`];
  for (const r of s.results || []) rows.push(`  ${safe(r.status)} ${safe(r.case_id)}/${safe(r.scenario)}${r.status === 'passed' ? '' : ' — ' + safe(r.error || Object.entries(r.checks || {}).filter(([, v]) => !v).map(([k]) => k).join(', '))}`);
  if (s.omitted_events) rows.push(`注意：${s.omitted_events} 条展示事件因达到上限未记录。`);
  return rows.join('\n');
}
