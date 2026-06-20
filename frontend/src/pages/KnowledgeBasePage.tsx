import { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import {
  Card, Btn, Modal, Input, Textarea, Select,
  Spinner, EmptyState, Badge, TabBar, Divider
} from '@/components/ui';
import { knowledgeApi } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { fmtDate, parseDays } from '@/lib/utils';
import type { DependencyEdge, RebootWindow, ServicePauseConfig } from '@/types';
import {
  BookOpen, Plus, Trash2, Brain, CheckCircle2, AlertTriangle,
  ArrowRight, Clock, Server, Settings2, RefreshCw, ChevronRight,
  FileText, Eye, Wand2
} from 'lucide-react';
import { useState as useGraphState } from 'react';

// ── KB Dependency Graph SVG ───────────────────────────────────────────────────
function KBDependencyGraph({ edges }: { edges: DependencyEdge[] }) {
  const [hovered, setHovered] = useGraphState<string | null>(null);

  // Build node list from edges
  const nodeSet = new Set<string>();
  edges.forEach(e => { nodeSet.add(e.dependent_server); nodeSet.add(e.dependency_server); });
  const nodes = [...nodeSet];

  if (nodes.length === 0) return (
    <div style={{ padding: 32, textAlign: 'center', color: '#454C75', fontSize: 12 }}>
      Add dependencies to see the graph
    </div>
  );

  // Topological layout
  const inCount: Record<string, number> = {};
  const outMap: Record<string, string[]> = {};
  nodes.forEach(n => { inCount[n] = 0; outMap[n] = []; });
  edges.forEach(e => { outMap[e.dependency_server]?.push(e.dependent_server); inCount[e.dependent_server] = (inCount[e.dependent_server] ?? 0) + 1; });

  const level: Record<string, number> = {};
  const queue = nodes.filter(n => (inCount[n] ?? 0) === 0);
  queue.forEach(n => { level[n] = 0; });
  let head = 0;
  while (head < queue.length) {
    const n = queue[head++];
    (outMap[n] ?? []).forEach(m => {
      const nl = (level[n] ?? 0) + 1;
      if (level[m] === undefined || level[m] < nl) { level[m] = nl; }
      if (!queue.includes(m)) queue.push(m);
    });
  }
  nodes.forEach(n => { if (level[n] === undefined) level[n] = 0; });

  const byLevel: Record<number, string[]> = {};
  nodes.forEach(n => { const l = level[n] ?? 0; if (!byLevel[l]) byLevel[l] = []; byLevel[l].push(n); });
  const numLevels = Math.max(...Object.keys(byLevel).map(Number)) + 1;

  const W = 700, H = 300, padX = 60, padY = 36, nW = 120, nH = 38;
  const xStep = numLevels > 1 ? (W - 2 * padX - nW) / (numLevels - 1) : 0;

  const pos: Record<string, { x: number; y: number }> = {};
  Object.entries(byLevel).forEach(([lStr, ns]) => {
    const l = parseInt(lStr);
    const x = padX + l * xStep;
    ns.forEach((n, i) => {
      const yStep = (H - 2 * padY) / Math.max(ns.length, 1);
      pos[n] = { x: x + nW / 2, y: padY + i * yStep + yStep / 2 };
    });
  });

  const typeColors: Record<string, string> = {};
  const palette = ['#60A5FA', '#A78BFA', '#FCD34D', '#F0ABFC', '#6EE7B7', '#FB923C'];
  nodes.forEach((n, i) => { typeColors[n] = palette[i % palette.length]; });

  return (
    <div style={{ background: '#06080F', borderRadius: 10, border: '1px solid #1C2038', overflow: 'hidden' }}>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: 'block' }}>
        <defs>
          <pattern id="kb-grid" width="32" height="32" patternUnits="userSpaceOnUse">
            <path d="M 32 0 L 0 0 0 32" fill="none" stroke="#0E1120" strokeWidth="1" />
          </pattern>
          <marker id="kb-dep-arr" markerWidth="6" markerHeight="4" refX="6" refY="2" orient="auto">
            <polygon points="0 0, 6 2, 0 4" fill="#252C4A" />
          </marker>
          <marker id="kb-dep-arr-hov" markerWidth="6" markerHeight="4" refX="6" refY="2" orient="auto">
            <polygon points="0 0, 6 2, 0 4" fill="#6366F1" />
          </marker>
        </defs>
        <rect width={W} height={H} fill="url(#kb-grid)" />

        {/* Edges */}
        {edges.map((e, i) => {
          const fp = pos[e.dependency_server], tp = pos[e.dependent_server];
          if (!fp || !tp) return null;
          const sx = fp.x + nW / 2, ex = tp.x - nW / 2;
          const cpx = (sx + ex) / 2;
          const isHov = hovered === e.dependency_server || hovered === e.dependent_server;
          return (
            <path key={i}
              d={`M ${sx} ${fp.y} C ${cpx} ${fp.y}, ${cpx} ${tp.y}, ${ex} ${tp.y}`}
              stroke={isHov ? '#6366F1' : '#252C4A'} strokeWidth={isHov ? 1.8 : 1.2}
              fill="none" markerEnd={`url(#kb-dep-arr${isHov ? '-hov' : ''})`}
              opacity={0.8} style={{ transition: 'stroke 0.15s' }} />
          );
        })}

        {/* Nodes */}
        {nodes.map(n => {
          const p = pos[n];
          if (!p) return null;
          const c = typeColors[n] ?? '#8B91BE';
          const isHov = hovered === n;
          return (
            <g key={n} transform={`translate(${p.x - nW / 2},${p.y - nH / 2})`}
              onMouseEnter={() => setHovered(n)} onMouseLeave={() => setHovered(null)}
              style={{ cursor: 'default' }}>
              <rect width={nW} height={nH} rx={7}
                fill={`${c}10`} stroke={isHov ? c : `${c}40`}
                strokeWidth={isHov ? 1.5 : 1} style={{ transition: 'all 0.15s' }} />
              <circle cx={nW - 10} cy={nH / 2} r={3.5} fill={c} opacity={0.8} />
              <text x={10} y={nH / 2 + 4} fontSize="11" fill={isHov ? '#E8EAF6' : c}
                fontWeight="700" fontFamily="'DM Mono', monospace">
                {n.length > 13 ? n.slice(0, 13) + '…' : n}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ── Dependency Graph Tab ──────────────────────────────────────────────────────
function DepsTab() {
  const [edges, setEdges] = useState<DependencyEdge[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState<{valid: boolean; issues: string[]; reasoning: string} | null>(null);

  const [form, setForm] = useState({ dependent_server: '', dependency_server: '', reason: '' });
  const [adding, setAdding] = useState(false);
  const [addErr, setAddErr] = useState('');

  const load = () => {
    knowledgeApi.listDeps()
      .then((r) => setEdges(r.data))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleAdd = async () => {
    if (!form.dependent_server || !form.dependency_server) {
      setAddErr('Both server fields are required');
      return;
    }
    setAdding(true);
    setAddErr('');
    try {
      await knowledgeApi.createDep(form);
      setShowAdd(false);
      setForm({ dependent_server: '', dependency_server: '', reason: '' });
      load();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: { message?: string; ai_reasoning?: string } } } };
      const detail = err?.response?.data?.detail;
      if (typeof detail === 'object' && detail?.message) {
        setAddErr(`${detail.message}: ${detail.ai_reasoning ?? ''}`);
      } else {
        setAddErr('Failed to create dependency');
      }
    } finally {
      setAdding(false);
    }
  };

  const handleVerify = async () => {
    setVerifying(true);
    setVerifyResult(null);
    try {
      const r = await knowledgeApi.verifyGraph(edges.map(e => ({ dependent_server: e.dependent_server, dependency_server: e.dependency_server })));
      setVerifyResult(r.data);
    } finally {
      setVerifying(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Remove this dependency?')) return;
    await knowledgeApi.deleteDep(id);
    load();
  };

  return (
    <div>
      {/* Toolbar */}
      <div className="flex items-center gap-2 mb-4">
        <Btn variant="primary" size="sm" icon={<Plus size={13} />} onClick={() => setShowAdd(true)}>
          Add Dependency
        </Btn>
        <Btn variant="secondary" size="sm" icon={verifying ? <Spinner size={12} /> : <Brain size={13} />} onClick={handleVerify} loading={verifying}>
          AI Verify Graph
        </Btn>
        <span className="ml-auto text-xs text-[#454C75]">{edges.length} edges</span>
      </div>

      {/* AI verify result */}
      {verifyResult && (
        <Card
          className="p-4 mb-4"
          style={{
            border: verifyResult.valid ? '1px solid rgba(16,185,129,0.25)' : '1px solid rgba(239,68,68,0.25)',
            background: verifyResult.valid ? 'rgba(16,185,129,0.04)' : 'rgba(239,68,68,0.04)',
          }}
        >
          <div className="flex items-center gap-2 mb-2">
            {verifyResult.valid ? (
              <CheckCircle2 size={14} className="text-emerald-400" />
            ) : (
              <AlertTriangle size={14} className="text-red-400" />
            )}
            <span className={`text-sm font-bold ${verifyResult.valid ? 'text-emerald-400' : 'text-red-400'}`}>
              {verifyResult.valid ? 'Graph is valid' : 'Graph has issues'}
            </span>
          </div>
          {verifyResult.issues.length > 0 && (
            <ul className="mb-2 pl-4">
              {verifyResult.issues.map((issue, i) => (
                <li key={i} className="text-xs text-red-400 list-disc">{issue}</li>
              ))}
            </ul>
          )}
          <p className="text-xs text-[#8B91BE] leading-relaxed">{verifyResult.reasoning}</p>
          <button onClick={() => setVerifyResult(null)} className="mt-2 text-xs text-[#454C75] hover:text-[#8B91BE]">Dismiss</button>
        </Card>
      )}

{/* Graph visualization */}
{!loading && edges.length > 0 && (
        <Card className="p-4 mb-4">
          <div className="text-xs font-bold text-[#8B91BE] uppercase tracking-wider mb-3">Dependency Graph</div>
          <KBDependencyGraph edges={edges} />
        </Card>
      )}

      {loading ? (
        <div className="text-center py-8 text-[#454C75]"><Spinner /></div>
      ) : edges.length === 0 ? (
        <EmptyState icon={<ArrowRight size={20} />} title="No dependencies defined" description="Add server dependencies to control reboot ordering" />
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-[#1C2038]">
                <th className="px-4 py-2.5 text-left text-[10px] font-bold uppercase tracking-wider text-[#454C75]">Dependent Server</th>
                <th className="px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-[#454C75]"></th>
                <th className="px-4 py-2.5 text-left text-[10px] font-bold uppercase tracking-wider text-[#454C75]">Depends On</th>
                <th className="px-4 py-2.5 text-left text-[10px] font-bold uppercase tracking-wider text-[#454C75]">Reason</th>
                <th className="px-4 py-2.5 text-left text-[10px] font-bold uppercase tracking-wider text-[#454C75]">Added</th>
                <th className="px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody>
              {edges.map((e) => (
                <tr key={e.id} className="border-b border-[#1C2038]/50 hover:bg-[#141828]/50 group">
                  <td className="px-4 py-3 font-mono text-xs text-[#C4C8E8]">{e.dependent_server}</td>
                  <td className="px-2 text-center">
                    <ArrowRight size={12} className="text-[#454C75] mx-auto" />
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-indigo-400">{e.dependency_server}</td>
                  <td className="px-4 py-3 text-xs text-[#8B91BE] max-w-xs truncate">{e.reason ?? '—'}</td>
                  <td className="px-4 py-3 text-xs text-[#454C75]">{fmtDate(e.created_at)}</td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => handleDelete(e.id)}
                      className="opacity-0 group-hover:opacity-100 w-6 h-6 rounded-[5px] bg-red-500/10 flex items-center justify-center text-red-400 hover:bg-red-500/20 transition-all"
                    >
                      <Trash2 size={11} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {/* Add dependency modal */}
      <Modal open={showAdd} onClose={() => { setShowAdd(false); setAddErr(''); }} title="Add Server Dependency">
        <div className="flex flex-col gap-4">
          <div className="p-3 rounded-[8px] bg-indigo-500/8 border border-indigo-500/20 text-xs text-indigo-300">
            <strong>Note:</strong> Adding a dependency means the "dependent server" must be rebooted AFTER the "depends on" server.
            The AI will verify that the graph remains cycle-free.
          </div>
          <Input
            label="Dependent Server (boots after)"
            placeholder="app-server-01"
            value={form.dependent_server}
            onChange={(e) => setForm((f) => ({ ...f, dependent_server: e.target.value }))}
          />
          <Input
            label="Depends On (boots first)"
            placeholder="db-server-01"
            value={form.dependency_server}
            onChange={(e) => setForm((f) => ({ ...f, dependency_server: e.target.value }))}
          />
          <Textarea
            label="Reason (optional)"
            placeholder="App server requires DB to be up before starting"
            value={form.reason}
            onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))}
            rows={2}
          />
          {addErr && (
            <div className="p-3 rounded-[8px] bg-red-500/10 border border-red-500/25 text-xs text-red-400 leading-relaxed">
              {addErr}
            </div>
          )}
          <div className="flex gap-2">
            <Btn variant="primary" onClick={handleAdd} loading={adding} className="flex-1">
              Add & Verify
            </Btn>
            <Btn variant="secondary" onClick={() => { setShowAdd(false); setAddErr(''); }}>
              Cancel
            </Btn>
          </div>
        </div>
      </Modal>
    </div>
  );
}

// ── Reboot Windows Tab ────────────────────────────────────────────────────────
function RebootWindowsTab() {
  const [windows, setWindows] = useState<RebootWindow[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [editItem, setEditItem] = useState<RebootWindow | null>(null);
  const [saving, setSaving] = useState(false);

  const emptyForm = { name: '', description: '', timezone: 'UTC', preferred_start_time: '02:00', preferred_end_time: '04:00', allowed_days: '0,1,2,3,4', reason: '' };
  const [form, setForm] = useState(emptyForm);

  const load = () => {
    knowledgeApi.listRebootWindows()
      .then((r) => setWindows(r.data))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      if (editItem) {
        await knowledgeApi.updateRebootWindow(editItem.id, form);
      } else {
        await knowledgeApi.createRebootWindow(form);
      }
      setShowAdd(false);
      setEditItem(null);
      setForm(emptyForm);
      load();
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Remove this reboot window?')) return;
    await knowledgeApi.deleteRebootWindow(id);
    load();
  };

  const openEdit = (w: RebootWindow) => {
    setEditItem(w);
    setForm({
      name: w.name,
      description: w.description ?? '',
      timezone: w.timezone,
      preferred_start_time: w.preferred_start_time,
      preferred_end_time: w.preferred_end_time,
      allowed_days: w.allowed_days,
      reason: w.reason ?? '',
    });
    setShowAdd(true);
  };

  const COMMON_TIMEZONES = [
    { value: 'UTC', label: 'UTC' },
    { value: 'America/New_York', label: 'America/New_York (ET)' },
    { value: 'America/Los_Angeles', label: 'America/Los_Angeles (PT)' },
    { value: 'America/Chicago', label: 'America/Chicago (CT)' },
    { value: 'Europe/London', label: 'Europe/London (BST/GMT)' },
    { value: 'Europe/Berlin', label: 'Europe/Berlin (CET)' },
    { value: 'Asia/Kolkata', label: 'Asia/Kolkata (IST)' },
    { value: 'Asia/Singapore', label: 'Asia/Singapore (SGT)' },
    { value: 'Asia/Shanghai', label: 'Asia/Shanghai (CST)' },
    { value: 'Asia/Tokyo', label: 'Asia/Tokyo (JST)' },
    { value: 'Australia/Sydney', label: 'Australia/Sydney (AEST)' },
  ];

  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <Btn variant="primary" size="sm" icon={<Plus size={13} />} onClick={() => { setEditItem(null); setForm(emptyForm); setShowAdd(true); }}>
          Add Window
        </Btn>
        <div className="ml-auto text-xs text-[#454C75]">
          Servers' timezones are detected at runtime and matched to these windows
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-8"><Spinner /></div>
      ) : windows.length === 0 ? (
        <EmptyState icon={<Clock size={20} />} title="No reboot windows defined" />
      ) : (
        <div className="grid grid-cols-2 gap-3">
          {windows.map((w) => (
            <Card key={w.id} className="p-4">
              <div className="flex items-start justify-between gap-2 mb-3">
                <div>
                  <div className="text-sm font-semibold text-[#C4C8E8]">{w.name}</div>
                  {w.description && <div className="text-xs text-[#454C75] mt-0.5">{w.description}</div>}
                </div>
                <div className="flex gap-1 flex-shrink-0">
                  <button onClick={() => openEdit(w)} className="w-6 h-6 rounded-[5px] bg-[#141828] flex items-center justify-center text-[#454C75] hover:text-[#C4C8E8] transition-all">
                    <Settings2 size={11} />
                  </button>
                  <button onClick={() => handleDelete(w.id)} className="w-6 h-6 rounded-[5px] bg-red-500/10 flex items-center justify-center text-red-400 hover:bg-red-500/20 transition-all">
                    <Trash2 size={11} />
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="text-[10px] text-[#454C75] uppercase tracking-wider mb-1">Timezone</div>
                  <div className="text-xs font-mono text-[#8B91BE]">{w.timezone}</div>
                </div>
                <div>
                  <div className="text-[10px] text-[#454C75] uppercase tracking-wider mb-1">Window</div>
                  <div className="text-xs font-mono text-[#8B91BE]">{w.preferred_start_time} – {w.preferred_end_time}</div>
                </div>
                <div>
                  <div className="text-[10px] text-[#454C75] uppercase tracking-wider mb-1">Days</div>
                  <div className="text-xs text-[#8B91BE]">{parseDays(w.allowed_days)}</div>
                </div>
                {w.reason && (
                  <div>
                    <div className="text-[10px] text-[#454C75] uppercase tracking-wider mb-1">Reason</div>
                    <div className="text-xs text-[#8B91BE] truncate">{w.reason}</div>
                  </div>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={showAdd}
        onClose={() => { setShowAdd(false); setEditItem(null); }}
        title={editItem ? 'Edit Reboot Window' : 'Add Reboot Window'}
      >
        <div className="flex flex-col gap-4">
          <Input label="Name" placeholder="Production DB Servers" value={form.name} onChange={(e) => setForm(f => ({ ...f, name: e.target.value }))} />
          <Textarea label="Description (optional)" placeholder="..." value={form.description} onChange={(e) => setForm(f => ({ ...f, description: e.target.value }))} rows={2} />
          <Select label="Timezone" options={COMMON_TIMEZONES} value={form.timezone} onChange={(e) => setForm(f => ({ ...f, timezone: e.target.value }))} />
          <div className="grid grid-cols-2 gap-3">
            <Input label="Start Time (HH:MM)" value={form.preferred_start_time} onChange={(e) => setForm(f => ({ ...f, preferred_start_time: e.target.value }))} />
            <Input label="End Time (HH:MM)" value={form.preferred_end_time} onChange={(e) => setForm(f => ({ ...f, preferred_end_time: e.target.value }))} />
          </div>
          <Input label="Allowed Days (comma-separated, 0=Mon..6=Sun)" value={form.allowed_days} onChange={(e) => setForm(f => ({ ...f, allowed_days: e.target.value }))} hint={`Preview: ${parseDays(form.allowed_days)}`} />
          <Textarea label="Reason (optional)" value={form.reason} onChange={(e) => setForm(f => ({ ...f, reason: e.target.value }))} rows={2} />
          <div className="flex gap-2">
            <Btn variant="primary" onClick={handleSave} loading={saving} className="flex-1">
              {editItem ? 'Save Changes' : 'Create Window'}
            </Btn>
            <Btn variant="secondary" onClick={() => { setShowAdd(false); setEditItem(null); }}>Cancel</Btn>
          </div>
        </div>
      </Modal>
    </div>
  );
}

// ── Service Pauses Tab ────────────────────────────────────────────────────────
function ServicePausesTab() {
  const [configs, setConfigs] = useState<ServicePauseConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [editItem, setEditItem] = useState<ServicePauseConfig | null>(null);
  const [saving, setSaving] = useState(false);

  const emptyForm = {
    server_hostname: '', service_name: '', pause_script: 'Pause-Service.ps1',
    resume_script: 'Resume-Service.ps1', reason: '', pre_pause_wait_seconds: 5, post_resume_wait_seconds: 10,
  };
  const [form, setForm] = useState(emptyForm);

  const load = () => {
    knowledgeApi.listServicePauses()
      .then((r) => setConfigs(r.data))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      if (editItem) {
        await knowledgeApi.updateServicePause(editItem.id, form);
      } else {
        await knowledgeApi.createServicePause(form);
      }
      setShowAdd(false);
      setEditItem(null);
      setForm(emptyForm);
      load();
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Remove this service pause config?')) return;
    await knowledgeApi.deleteServicePause(id);
    load();
  };

  const openEdit = (c: ServicePauseConfig) => {
    setEditItem(c);
    setForm({
      server_hostname: c.server_hostname,
      service_name: c.service_name,
      pause_script: c.pause_script,
      resume_script: c.resume_script,
      reason: c.reason ?? '',
      pre_pause_wait_seconds: c.pre_pause_wait_seconds,
      post_resume_wait_seconds: c.post_resume_wait_seconds,
    });
    setShowAdd(true);
  };

  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <Btn variant="primary" size="sm" icon={<Plus size={13} />} onClick={() => { setEditItem(null); setForm(emptyForm); setShowAdd(true); }}>
          Add Config
        </Btn>
        <span className="ml-auto text-xs text-[#454C75]">{configs.length} configurations</span>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-8"><Spinner /></div>
      ) : configs.length === 0 ? (
        <EmptyState icon={<Settings2 size={20} />} title="No service pause configs" description="Define which services need pausing before server restarts" />
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-[#1C2038]">
                {['Server', 'Service', 'Pause Script', 'Pre-wait', 'Post-wait', 'Reason', ''].map((h) => (
                  <th key={h} className="px-4 py-2.5 text-left text-[10px] font-bold uppercase tracking-wider text-[#454C75] whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {configs.map((c) => (
                <tr key={c.id} className="border-b border-[#1C2038]/50 hover:bg-[#141828]/50 group">
                  <td className="px-4 py-3 font-mono text-xs text-[#C4C8E8]">{c.server_hostname}</td>
                  <td className="px-4 py-3 font-mono text-xs text-purple-400">{c.service_name}</td>
                  <td className="px-4 py-3 font-mono text-xs text-[#454C75]">{c.pause_script}</td>
                  <td className="px-4 py-3 text-xs text-[#8B91BE]">{c.pre_pause_wait_seconds}s</td>
                  <td className="px-4 py-3 text-xs text-[#8B91BE]">{c.post_resume_wait_seconds}s</td>
                  <td className="px-4 py-3 text-xs text-[#8B91BE] max-w-xs truncate">{c.reason ?? '—'}</td>
                  <td className="px-4 py-3">
                    <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-all">
                      <button onClick={() => openEdit(c)} className="w-6 h-6 rounded-[5px] bg-[#141828] flex items-center justify-center text-[#454C75] hover:text-[#C4C8E8]">
                        <Settings2 size={11} />
                      </button>
                      <button onClick={() => handleDelete(c.id)} className="w-6 h-6 rounded-[5px] bg-red-500/10 flex items-center justify-center text-red-400 hover:bg-red-500/20">
                        <Trash2 size={11} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <Modal
        open={showAdd}
        onClose={() => { setShowAdd(false); setEditItem(null); }}
        title={editItem ? 'Edit Service Pause Config' : 'Add Service Pause Config'}
      >
        <div className="flex flex-col gap-4">
          <Input label="Server Hostname" placeholder="app-server-01" value={form.server_hostname} onChange={(e) => setForm(f => ({ ...f, server_hostname: e.target.value }))} />
          <Input label="Service Name" placeholder="MyAppService" value={form.service_name} onChange={(e) => setForm(f => ({ ...f, service_name: e.target.value }))} />
          <div className="grid grid-cols-2 gap-3">
            <Input label="Pause Script" value={form.pause_script} onChange={(e) => setForm(f => ({ ...f, pause_script: e.target.value }))} />
            <Input label="Resume Script" value={form.resume_script} onChange={(e) => setForm(f => ({ ...f, resume_script: e.target.value }))} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Input label="Pre-Pause Wait (s)" type="number" value={form.pre_pause_wait_seconds} onChange={(e) => setForm(f => ({ ...f, pre_pause_wait_seconds: +e.target.value }))} />
            <Input label="Post-Resume Wait (s)" type="number" value={form.post_resume_wait_seconds} onChange={(e) => setForm(f => ({ ...f, post_resume_wait_seconds: +e.target.value }))} />
          </div>
          <Textarea label="Reason (optional)" value={form.reason} onChange={(e) => setForm(f => ({ ...f, reason: e.target.value }))} rows={2} />
          <div className="flex gap-2">
            <Btn variant="primary" onClick={handleSave} loading={saving} className="flex-1">
              {editItem ? 'Save Changes' : 'Create Config'}
            </Btn>
            <Btn variant="secondary" onClick={() => { setShowAdd(false); setEditItem(null); }}>Cancel</Btn>
          </div>
        </div>
      </Modal>
    </div>
  );
}

// ── Server KB Documents Tab ───────────────────────────────────────────────────
function ServerKBTab() {
  const [docs, setDocs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [editItem, setEditItem] = useState<any | null>(null);
  const [saving, setSaving] = useState(false);
  const [previewDoc, setPreviewDoc] = useState<any | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const emptyForm = { server_hostname: '', document_content: '' };
  const [form, setForm] = useState(emptyForm);

 const load = () => {
    fetch('/api/knowledge/server-kb', {
      headers: { Authorization: `Bearer ${localStorage.getItem('patchops_token')}` },
    })
      .then((r) => r.json())
      .then(setDocs)
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      const token = localStorage.getItem('patchops_token');
      if (editItem) {
        await fetch(`/api/knowledge/server-kb/${editItem.id}`, {  
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify({ document_content: form.document_content }),
        });
      } else {
        await fetch('/api/knowledge/server-kb', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify(form),
        });
      }
      setShowAdd(false);
      setEditItem(null);
      setForm(emptyForm);
      load();
    } finally {
      setSaving(false);
    }
  };

 const handleDelete = async (id: number) => {
    if (!confirm('Remove this KB document?')) return;
    await fetch(`/api/knowledge/server-kb/${id}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${localStorage.getItem('patchops_token')}` },
    });
    load();
  };

  const handlePreview = async (doc: any) => {
    setPreviewing(true);
    setPreviewDoc(doc);
    try {
      const token = localStorage.getItem('patchops_token');
      const r = await fetch(`/api/knowledge/server-kb/${doc.id}/preview-scripts`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await r.json();
      setPreviewDoc({ ...doc, ...data });
    } finally {
      setPreviewing(false);
    }
  };

  const EXAMPLE_KB = `This server runs the UK production web tier.

Applications running:
- IIS (W3SVC): Main web server. Should be gracefully stopped before reboot by draining connections. After reboot, verify it starts and responds on port 80.
- OrderProcessingService (Windows Service): Critical service that processes customer orders. Must be stopped gracefully — wait up to 60 seconds for in-flight orders to complete. After reboot, start it and wait 30 seconds before checking it is Running.
- HealthMonitor (Windows Service): Lightweight monitoring agent. Can be stopped immediately. Will auto-start after reboot.

Rules:
- Never stop Windows Defender or any security software.
- Never stop the WinRM service.
- SQL Server is on a different machine — do not try to stop it here.
- If OrderProcessingService fails to stop within 60 seconds, log a warning and proceed.`;

  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <Btn variant="primary" size="sm" icon={<Plus size={13} />}
          onClick={() => { setEditItem(null); setForm(emptyForm); setShowAdd(true); }}>
          Add Server KB
        </Btn>
        <span className="ml-auto text-xs text-[#454C75]">{docs.length} servers configured</span>
      </div>

      {/* Info banner */}
      <div className="mb-4 p-3 rounded-[8px] bg-indigo-500/8 border border-indigo-500/20 text-xs text-indigo-300 leading-relaxed">
        <strong>How it works:</strong> Write a plain English description of the applications running on each server and any operational rules. Before each reboot, Gemini AI reads this document and generates a custom PowerShell script to gracefully stop and restart your applications. The generated scripts are streamed live in the execution log.
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-8"><Spinner /></div>
      ) : docs.length === 0 ? (
        <EmptyState
          icon={<FileText size={20} />}
          title="No server KB documents"
          description="Add a knowledge base document for each server to enable AI-generated pre/post reboot scripts"
        />
      ) : (
        <div className="flex flex-col gap-3">
          {docs.map((doc) => (
            <Card key={doc.id} className="p-4">
              <div className="flex items-start justify-between gap-3 mb-3">
                <div className="flex items-center gap-2">
                  <div className="w-7 h-7 rounded-[6px] bg-indigo-500/12 flex items-center justify-center">
                    <Server size={12} className="text-indigo-400" />
                  </div>
                  <div>
                    <div className="text-sm font-bold font-mono text-[#C4C8E8]">{doc.server_hostname}</div>
                    {doc.last_script_generated_at && (
                      <div className="text-[10px] text-[#454C75] mt-0.5">
                        Scripts generated {new Date(doc.last_script_generated_at).toLocaleString()}
                      </div>
                    )}
                  </div>
                </div>
                <div className="flex gap-1 flex-shrink-0">
                  <button
                    onClick={() => handlePreview(doc)}
                    className="flex items-center gap-1 px-2 h-6 rounded-[5px] bg-indigo-500/10 text-indigo-400 hover:bg-indigo-500/20 text-[10px] font-semibold transition-all"
                    title="Preview AI-generated scripts"
                  >
                    <Wand2 size={10} />
                    Preview Scripts
                  </button>
                  <button
                    onClick={() => { setEditItem(doc); setForm({ server_hostname: doc.server_hostname, document_content: doc.document_content }); setShowAdd(true); }}
                    className="w-6 h-6 rounded-[5px] bg-[#141828] flex items-center justify-center text-[#454C75] hover:text-[#C4C8E8] transition-all"
                  >
                    <Settings2 size={11} />
                  </button>
                  <button
                    onClick={() => handleDelete(doc.id)}
                    className="w-6 h-6 rounded-[5px] bg-red-500/10 flex items-center justify-center text-red-400 hover:bg-red-500/20 transition-all"
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              </div>
              <div className="text-xs text-[#8B91BE] leading-relaxed whitespace-pre-wrap bg-[#060810] rounded-[6px] p-3 border border-[#1C2038] max-h-24 overflow-hidden relative">
                {doc.document_content}
                <div className="absolute bottom-0 left-0 right-0 h-6 bg-gradient-to-t from-[#060810] to-transparent" />
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Add/Edit Modal */}
      <Modal
        open={showAdd}
        onClose={() => { setShowAdd(false); setEditItem(null); }}
        title={editItem ? `Edit KB: ${editItem.server_hostname}` : 'Add Server KB Document'}
      >
        <div className="flex flex-col gap-4">
          {!editItem && (
            <Input
              label="Server Hostname"
              placeholder="uk01pr1abat01"
              value={form.server_hostname}
              onChange={(e) => setForm(f => ({ ...f, server_hostname: e.target.value }))}
            />
          )}
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs font-semibold text-[#8B91BE]">
                Knowledge Base Document (plain English)
              </label>
              <button
                className="text-[10px] text-indigo-400 hover:text-indigo-300"
                onClick={() => setForm(f => ({ ...f, document_content: EXAMPLE_KB }))}
              >
                Load example
              </button>
            </div>
            <Textarea
              placeholder="Describe the applications running on this server and any rules for stopping/starting them before/after a reboot..."
              value={form.document_content}
              onChange={(e) => setForm(f => ({ ...f, document_content: e.target.value }))}
              rows={12}
            />
            <p className="text-[10px] text-[#454C75] mt-1">
              Write in plain English. Gemini will read this to generate safe PowerShell scripts.
            </p>
          </div>
          <div className="flex gap-2">
            <Btn variant="primary" onClick={handleSave} loading={saving} className="flex-1">
              {editItem ? 'Save Changes' : 'Create KB Document'}
            </Btn>
            <Btn variant="secondary" onClick={() => { setShowAdd(false); setEditItem(null); }}>Cancel</Btn>
          </div>
        </div>
      </Modal>

      {/* Preview Scripts Modal */}
      <Modal
        open={!!previewDoc}
        onClose={() => setPreviewDoc(null)}
        title={`AI-Generated Scripts: ${previewDoc?.server_hostname ?? ''}`}
      >
        {previewing ? (
          <div className="flex flex-col items-center gap-3 py-8">
            <Spinner />
            <p className="text-sm text-[#8B91BE]">Gemini is reading the KB document and generating scripts...</p>
          </div>
        ) : previewDoc?.pre_reboot_script ? (
          <div className="flex flex-col gap-4">
            {previewDoc.apps_identified?.length > 0 && (
              <div className="p-3 rounded-[8px] bg-emerald-500/8 border border-emerald-500/20">
                <div className="text-[10px] text-emerald-400 font-semibold uppercase tracking-wider mb-1">Applications Identified</div>
                <div className="flex flex-wrap gap-1">
                  {previewDoc.apps_identified.map((app: string, i: number) => (
                    <span key={i} className="px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 text-[10px] font-mono">{app}</span>
                  ))}
                </div>
              </div>
            )}
            {previewDoc.reasoning && (
              <p className="text-xs text-[#8B91BE] leading-relaxed">{previewDoc.reasoning}</p>
            )}
            {previewDoc.warnings?.length > 0 && (
              <div className="p-3 rounded-[8px] bg-yellow-500/8 border border-yellow-500/20">
                <div className="text-[10px] text-yellow-400 font-semibold uppercase tracking-wider mb-1">Warnings</div>
                {previewDoc.warnings.map((w: string, i: number) => (
                  <p key={i} className="text-xs text-yellow-400">• {w}</p>
                ))}
              </div>
            )}
            <div>
              <div className="text-[10px] text-[#454C75] font-semibold uppercase tracking-wider mb-2">Pre-Reboot Script</div>
              <pre className="text-[10px] font-mono text-[#8B91BE] bg-[#060810] rounded-[6px] p-3 border border-[#1C2038] overflow-auto max-h-48 whitespace-pre-wrap">
                {previewDoc.pre_reboot_script}
              </pre>
            </div>
            <div>
              <div className="text-[10px] text-[#454C75] font-semibold uppercase tracking-wider mb-2">Post-Reboot Script</div>
              <pre className="text-[10px] font-mono text-[#8B91BE] bg-[#060810] rounded-[6px] p-3 border border-[#1C2038] overflow-auto max-h-48 whitespace-pre-wrap">
                {previewDoc.post_reboot_script}
              </pre>
            </div>
            <p className="text-[10px] text-[#454C75]">These scripts will be automatically executed during the next reboot operation for this server.</p>
          </div>
        ) : (
          <p className="text-sm text-[#454C75] py-4 text-center">Click "Preview Scripts" to generate scripts from the KB document.</p>
        )}
      </Modal>
    </div>
  );
}

// ── Main Knowledge Base Page ──────────────────────────────────────────────────
export function KnowledgeBasePage() {
  const { isAdmin } = useAuth();
 const [tab, setTab] = useState<'deps' | 'windows' | 'pauses' | 'server-kb'>('deps');

  if (!isAdmin) return <Navigate to="/dashboard" replace />;

  return (
    <Layout breadcrumbs={['PatchOps', 'Knowledge Base']}>
      <div className="flex items-center gap-3 mb-6">
        <div
          className="w-10 h-10 rounded-[10px] flex items-center justify-center"
          style={{ background: 'rgba(99,102,241,0.12)', border: '1px solid rgba(99,102,241,0.2)' }}
        >
          <BookOpen size={18} className="text-indigo-400" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-[#E8EAF6]" style={{ fontFamily: 'var(--font-display)' }}>
            Knowledge Base
          </h1>
          <p className="text-sm text-[#454C75]">Dependency graph, reboot windows, service pause configurations</p>
        </div>
        <Badge variant="purple" className="ml-auto">Admin Only</Badge>
      </div>

      <TabBar
        tabs={[
          { id: 'deps', label: 'Dependency Graph' },
          { id: 'windows', label: 'Reboot Windows' },
          { id: 'pauses', label: 'Service Pauses' },
          { id: 'server-kb', label: 'Server KB' },
        ]}
        active={tab}
        onChange={(t) => setTab(t as 'deps' | 'windows' | 'pauses' | 'server-kb')}
      />

      {tab === 'deps' && <DepsTab />}
      {tab === 'windows' && <RebootWindowsTab />}
      {tab === 'pauses' && <ServicePausesTab />}
      {tab === 'server-kb' && <ServerKBTab />}
    </Layout>
  );
}
