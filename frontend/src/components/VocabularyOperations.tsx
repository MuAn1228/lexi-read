import { Check, ChevronDown, FolderPlus, Minus, RotateCcw, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import { ApiError, errorMessage, request, requestFile } from '../api';
import { statusLabels } from '../WordControls';
import type { Vocabulary, WordStatus } from '../types';
import { normalizeGroupName, selectVocabularyPage, selectionLimit, validGroupName, vocabularyBatch } from '../vocabularyOperations';
import type { VocabularyBatch, VocabularyBatchResult, VocabularyGroup } from '../vocabularyOperations';
import './VocabularyOperations.css';

export function useVocabularyOperations({ vocabulary, saving, onVocabularyChanged, onBeforeChange, onBusyChange, onReloadVocabulary }: {
  vocabulary: Vocabulary[]; saving: boolean; onVocabularyChanged: (items: Vocabulary[]) => void;
  onBeforeChange: () => void; onBusyChange?: (busy: boolean) => void; onReloadVocabulary?: () => Promise<void>;
}) {
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [groups, setGroups] = useState<VocabularyGroup[]>([]);
  const [groupFilter, setGroupFilter] = useState<number | null>(null);
  const groupFilterRef = useRef(groupFilter);
  groupFilterRef.current = groupFilter;
  const [groupsLoaded, setGroupsLoaded] = useState(false);
  const [groupError, setGroupError] = useState('');
  const [retryGroups, setRetryGroups] = useState(0);
  const [notice, setNotice] = useState('');
  const [operationError, setOperationError] = useState('');
  const [busy, setBusy] = useState(false);
  const active = useRef(false);
  const pendingRef = useRef<VocabularyBatch | null>(null);
  const [pending, setPending] = useState<VocabularyBatch | null>(null);
  const [undo, setUndo] = useState<{ id: string; count: number } | null>(null);
  const mounted = useRef(true);
  const callbacks = useRef({ onVocabularyChanged, onBeforeChange, onBusyChange, onReloadVocabulary });
  callbacks.current = { onVocabularyChanged, onBeforeChange, onBusyChange, onReloadVocabulary };
  const applyGroups = (next: VocabularyGroup[]) => {
    setGroups(next); setGroupsLoaded(true); setGroupError('');
    if (groupFilterRef.current !== null && !next.some(group => group.id === groupFilterRef.current)) {
      setGroupFilter(null); setSelected(new Set());
      setNotice('当前分组已删除，已返回全部分组并清除选择；词汇仍保留。');
    }
  };

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; callbacks.current.onBusyChange?.(false); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setGroupError('');
    request<{ groups: VocabularyGroup[] }>('/vocabulary-groups', { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) applyGroups(value.groups); })
      .catch(error => { if (!controller.signal.aborted) setGroupError(errorMessage(error)); });
    return () => controller.abort();
  }, [retryGroups]);

  const locked = busy || !!pending;
  const disabled = saving || locked;
  const begin = () => {
    if (active.current) return false;
    active.current = true; setBusy(true); callbacks.current.onBusyChange?.(true); setOperationError('');
    return true;
  };
  const finish = () => {
    active.current = false;
    if (mounted.current) setBusy(false);
    callbacks.current.onBusyChange?.(!!pendingRef.current);
  };
  const clearSelection = useCallback((reason = '') => {
    if (selected.size && reason) setNotice(reason);
    setSelected(new Set());
  }, [selected.size]);
  const select = (ids: number[], checked: boolean) => {
    if (disabled) return;
    setSelected(current => {
      if (checked && ids.some(id => !current.has(id)) && new Set([...current, ...ids]).size > selectionLimit)
        setNotice('每次最多选择 200 个词，已保留其他页的选择。');
      return selectVocabularyPage(current, ids, checked);
    });
  };
  const batch = async (words?: Vocabulary[], status?: WordStatus) => {
    if (active.current || (!pendingRef.current && saving)) return;
    const payload = pendingRef.current ?? (words?.length && status ? vocabularyBatch(words, status, crypto.randomUUID()) : null);
    if (!payload || !begin()) return;
    callbacks.current.onBeforeChange();
    pendingRef.current = payload; setPending(payload);
    try {
      const result = await request<VocabularyBatchResult>('/vocabulary/batch', { method: 'POST', body: JSON.stringify(payload) });
      if (!mounted.current) return;
      pendingRef.current = null; setPending(null);
      callbacks.current.onVocabularyChanged(result.items);
      setSelected(new Set());
      if (result.undone) setUndo(null);
      else if (result.changed_count) setUndo({ id: result.operation_id, count: result.changed_count });
      setNotice(result.undone ? '这次操作已撤销，未重复修改。' : result.changed_count ? `已将 ${result.changed_count} 个词标为「${statusLabels[payload.status]}」。` : '所选单词已是这个状态，无需修改。');
    } catch (error) {
      if (!mounted.current) return;
      const definite = error instanceof ApiError && error.status >= 400 && error.status < 500;
      let refreshed = false;
      if (definite) {
        pendingRef.current = null; setPending(null);
        try { if (callbacks.current.onReloadVocabulary) { await callbacks.current.onReloadVocabulary(); refreshed = true; } } catch { /* Offer an explicit read retry below. */ }
      }
      setOperationError(`${errorMessage(error)}${definite ? refreshed ? ' 未执行本次修改，已重新读取词库；请检查后重试。' : ' 未执行本次修改；请重新读取词库后重试。' : ' 结果尚未确认，请重试确认本次操作；不会重复记录学习。'}`);
    } finally { finish(); }
  };
  const undoLast = async () => {
    if (!undo || disabled || !begin()) return;
    callbacks.current.onBeforeChange();
    try {
      const result = await request<VocabularyBatchResult>(`/vocabulary/batch/${undo.id}/undo`, { method: 'POST' });
      if (!mounted.current) return;
      callbacks.current.onVocabularyChanged(result.items);
      setNotice(`已撤销最近一次标记，恢复 ${result.changed_count} 个词的原状态与复习安排。`);
      setUndo(null);
    } catch (error) {
      if (!mounted.current) return;
      if (error instanceof ApiError && (error.status === 409 || error.status === 404)) {
        setUndo(null); await callbacks.current.onReloadVocabulary?.().catch(() => {});
        setOperationError('这次操作之后已有新的学习记录，无法安全撤销。后续学习记录已保留。');
      } else setOperationError(`${errorMessage(error)} 可重试撤销。`);
    } finally { finish(); }
  };
  const groupMutation = async (path: string, method: string, body?: unknown) => {
    if (disabled || !begin()) return false;
    try {
      if (method === 'DELETE') await requestFile(`/api${path}`, { method });
      else await request<VocabularyGroup>(path, { method, body: JSON.stringify(body) });
      const next = await request<{ groups: VocabularyGroup[] }>('/vocabulary-groups');
      if (!mounted.current) return true;
      applyGroups(next.groups);
      setNotice(method === 'DELETE' ? '分组已删除，组内单词仍保留在词库。' : '分组已更新。');
      return true;
    } catch (error) {
      if (mounted.current) { setOperationError(`${errorMessage(error)} 请重新读取分组确认结果后再操作。`); setRetryGroups(value => value + 1); }
      return false;
    } finally { finish(); }
  };
  const reload = async () => {
    if (disabled || !callbacks.current.onReloadVocabulary || !begin()) return;
    try {
      await callbacks.current.onReloadVocabulary();
      if (mounted.current) setNotice('已重新读取词库；当前选择保留，请检查后继续。');
    } catch (error) { if (mounted.current) setOperationError(errorMessage(error)); }
    finally { finish(); }
  };
  return {
    selected, groups, groupFilter, setGroupFilter, groupsLoaded, groupError, retryGroups: () => setRetryGroups(value => value + 1),
    notice, operationError, busy, pending, disabled, locked, undo, undoLast, select, clearSelection, batch, groupMutation, reload,
    selectedWords: vocabulary.filter(word => selected.has(word.id)),
  };
}

export type VocabularyOperations = ReturnType<typeof useVocabularyOperations>;

export function VocabularyGroupTools({ operations: op, onFilter }: { operations: VocabularyOperations; onFilter: (id: number | null) => void }) {
  const [name, setName] = useState('');
  const [editing, setEditing] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [deleting, setDeleting] = useState<number | null>(null);
  const [nameError, setNameError] = useState('');
  const saveName = async (id?: number) => {
    const value = normalizeGroupName(id === undefined ? name : editName);
    if (!validGroupName(value)) { setNameError('组名需要 1 至 30 个字。'); return; }
    setNameError('');
    const ok = await op.groupMutation(id === undefined ? '/vocabulary-groups' : `/vocabulary-groups/${id}`, id === undefined ? 'POST' : 'PUT', { name: value });
    if (ok) { setName(''); setEditing(null); }
  };
  return <div className="vocabulary-group-tools">
    <label className="vocabulary-group-filter">分组<select aria-label="筛选自定义分组" disabled={op.disabled || !op.groupsLoaded} value={op.groupFilter ?? ''} onChange={event => onFilter(event.target.value ? Number(event.target.value) : null)}><option value="">全部分组</option>{op.groups.map(group => <option value={group.id} key={group.id}>{group.name}（{group.vocabulary_ids.length}）</option>)}</select></label>
    <details className="vocabulary-group-manager"><summary><FolderPlus size={15} aria-hidden="true" />管理分组<ChevronDown size={14} aria-hidden="true" /></summary>
      <p className="vocabulary-group-hint">按主题整理单词；删除分组不会删除词汇。最多 50 组。</p>
      <form className="vocabulary-group-form" onSubmit={event => { event.preventDefault(); void saveName(); }}><input aria-label="新分组名称" placeholder="例如：工作与表达" maxLength={60} value={name} disabled={op.disabled} onChange={event => setName(event.target.value)} /><button className="button secondary" disabled={op.disabled || !op.groupsLoaded || op.groups.length >= 50}>新建分组</button></form>
      {nameError && <p className="modal-error" role="alert">{nameError}</p>}
      {op.groups.map(group => <div className="vocabulary-group-row" key={group.id}>{editing === group.id ? <form className="vocabulary-group-form" onSubmit={event => { event.preventDefault(); void saveName(group.id); }}><input aria-label={`重命名 ${group.name}`} maxLength={60} disabled={op.disabled} value={editName} onChange={event => setEditName(event.target.value)} /><button className="text-button" disabled={op.disabled}>保存</button><button type="button" className="text-button" disabled={op.disabled} onClick={() => setEditing(null)}>取消</button></form> : <><span>{group.name}<small>{group.vocabulary_ids.length} 个词</small></span><button className="text-button" disabled={op.disabled} onClick={() => { setEditing(group.id); setEditName(group.name); setNameError(''); }}>重命名</button><button className="text-button" disabled={op.disabled} onClick={() => setDeleting(group.id)}>删除</button></>}{deleting === group.id && <div className="vocabulary-group-confirm"><span>仅删除「{group.name}」分组，保留全部单词？</span><button className="text-button" disabled={op.disabled} onClick={async () => { if (await op.groupMutation(`/vocabulary-groups/${group.id}`, 'DELETE')) setDeleting(null); }}>确认删除</button><button className="text-button" disabled={op.disabled} onClick={() => setDeleting(null)}>取消</button></div>}</div>)}
    </details>
    {op.groupError && <p className="vocabulary-operation-error" role="status">分组暂未加载：{op.groupError}<button className="text-button" disabled={op.disabled} onClick={op.retryGroups}>重试分组</button></p>}
  </div>;
}

export function VocabularySelectionBar({ operations: op, visible, ready }: { operations: VocabularyOperations; visible: Vocabulary[]; ready: boolean }) {
  const [destination, setDestination] = useState('');
  const pageCheckbox = useRef<HTMLInputElement>(null);
  const pageSelected = visible.filter(word => op.selected.has(word.id)).length;
  const full = visible.length > 0 && pageSelected === visible.length;
  useEffect(() => { if (pageCheckbox.current) pageCheckbox.current.indeterminate = pageSelected > 0 && !full; }, [pageSelected, full]);
  useEffect(() => { if (destination && !op.groups.some(group => group.id === Number(destination))) setDestination(''); }, [op.groups, destination]);
  const members = async (id: number, action: 'add' | 'remove') => {
    if (!op.selected.size) return;
    if (await op.groupMutation(`/vocabulary-groups/${id}/members`, 'POST', { vocabulary_ids: [...op.selected], action })) op.clearSelection();
  };
  return <div className={`vocabulary-selection-bar ${op.selected.size || op.pending ? 'has-selection' : ''}`} aria-label="词库批量操作">
    <div className="vocabulary-selection-top"><label className="vocabulary-select-page"><input ref={pageCheckbox} type="checkbox" aria-label="选择本页单词" checked={full} disabled={op.disabled || !ready || !visible.length} onChange={(event: ChangeEvent<HTMLInputElement>) => op.select(visible.map(word => word.id), event.target.checked)} />选择本页</label><span className="vocabulary-selected-count" aria-live="polite">已选 {op.selected.size} / 200<span> · 可跨页</span></span>{op.selected.size > 0 && <button className="text-button" disabled={op.disabled} onClick={() => op.clearSelection()}>清除选择</button>}</div>
    {op.selected.size > 0 && <div className="vocabulary-batch-actions"><div className="vocabulary-batch-status" aria-label="批量调整状态">{([{ status: 'KNOWN', icon: Check }, { status: 'UNKNOWN', icon: X }, { status: 'UNCERTAIN', icon: Minus }] as const).map(({ status, icon: Icon }) => <button key={status} className={`button secondary ${status.toLowerCase()}`} disabled={op.disabled} onClick={() => void op.batch(op.selectedWords, status)}><Icon size={14} aria-hidden="true" />{statusLabels[status]}</button>)}</div><div className="vocabulary-batch-groups"><select aria-label="批量加入的分组" value={destination} disabled={op.disabled || !op.groupsLoaded || !op.groups.length} onChange={event => setDestination(event.target.value)}><option value="">选择分组…</option>{op.groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}</select><button className="text-button" disabled={op.disabled || !destination} onClick={() => void members(Number(destination), 'add')}>加入分组</button>{op.groupFilter !== null && <button className="text-button" disabled={op.disabled} onClick={() => void members(op.groupFilter!, 'remove')}>移出当前组</button>}</div></div>}
    {(op.notice || op.undo || op.busy || op.operationError || op.pending) && <div className="vocabulary-operation-feedback"><div role={op.operationError ? 'alert' : 'status'}><span>{op.busy ? '正在保存，请稍候…' : op.operationError || op.notice}</span>{op.pending && !op.busy && <button className="text-button" onClick={() => void op.batch()}>重试确认本次标记</button>}{op.operationError && !op.pending && <button className="text-button" disabled={op.disabled} onClick={() => void op.reload()}>重新读取词库</button>}</div>{op.undo && !op.pending && <button className="vocabulary-undo" disabled={op.disabled} onClick={() => void op.undoLast()}><RotateCcw size={14} aria-hidden="true" />撤销最近标记（{op.undo.count}）</button>}</div>}
  </div>;
}
