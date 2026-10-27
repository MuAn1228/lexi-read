import { Check, CircleHelp, LoaderCircle, RefreshCw, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { errorMessage, request } from './api';
import { usableVocabularySenses, vocabularySensesPath } from './senseLearning';
import type { VocabularySense, VocabularySenses, WordStatus } from './types';
import './senseLearning.css';

const choices = [
  { status: 'KNOWN', label: '已掌握', Icon: Check },
  { status: 'UNCERTAIN', label: '不确定', Icon: CircleHelp },
  { status: 'UNKNOWN', label: '不认识', Icon: X },
] as const;

export default function SenseLearningPanel({ word, disabled = false, compact = false, onLearningChanged }: {
  word: string; disabled?: boolean; compact?: boolean; onLearningChanged?: () => void | Promise<void>;
}) {
  const [data, setData] = useState<VocabularySenses | null>(null);
  const [loading, setLoading] = useState(false);
  const [readError, setReadError] = useState('');
  const [savingKeys, setSavingKeys] = useState<Set<string>>(() => new Set());
  const [saveErrors, setSaveErrors] = useState<Record<string, { message: string; status: WordStatus }>>({});
  const [notice, setNotice] = useState('');
  const [retry, setRetry] = useState(0);
  const generation = useRef(0);
  const saving = useRef<Set<string>>(new Set());

  useEffect(() => {
    const current = ++generation.current;
    saving.current.clear();
    setData(null); setReadError(''); setSaveErrors({}); setSavingKeys(new Set()); setNotice('');
    if (!word.trim()) return;
    const controller = new AbortController(); setLoading(true);
    request<unknown>(vocabularySensesPath(word), { signal: controller.signal })
      .then(value => {
        if (controller.signal.aborted || current !== generation.current) return;
        const parsed = usableVocabularySenses(value);
        if (!parsed) throw new Error('义项数据格式不完整，请刷新后重试。');
        setData(parsed);
      })
      .catch(reason => { if (!controller.signal.aborted && current === generation.current) setReadError(errorMessage(reason)); })
      .finally(() => { if (!controller.signal.aborted && current === generation.current) setLoading(false); });
    return () => controller.abort();
  }, [word, retry]);

  const mark = async (sense: VocabularySense, next: WordStatus) => {
    if (disabled || saving.current.has(sense.sense_key) || sense.status === next) return;
    const requestGeneration = generation.current;
    saving.current.add(sense.sense_key);
    setSavingKeys(current => new Set(current).add(sense.sense_key));
    setSaveErrors(current => { const nextErrors = { ...current }; delete nextErrors[sense.sense_key]; return nextErrors; });
    setNotice('');
    try {
      const value = await request<VocabularySense>(vocabularySensesPath(word, sense.sense_key), {
        method: 'PUT', body: JSON.stringify({ status: next }),
      });
      if (requestGeneration === generation.current && value.sense_key === sense.sense_key) {
        setData(current => current ? { ...current, senses: current.senses.map(item => item.sense_key === value.sense_key ? { ...item, ...value } : item) } : current);
        setNotice(`“${sense.meaning}”已标为${choices.find(choice => choice.status === next)?.label}。`);
        await onLearningChanged?.();
      }
    } catch (reason) {
      if (requestGeneration === generation.current) setSaveErrors(current => ({ ...current, [sense.sense_key]: { message: errorMessage(reason), status: next } }));
    } finally {
      saving.current.delete(sense.sense_key);
      if (requestGeneration === generation.current) setSavingKeys(current => { const nextKeys = new Set(current); nextKeys.delete(sense.sense_key); return nextKeys; });
    }
  };

  return <section className={`sense-learning${compact ? ' is-compact' : ''}`} aria-label={`${word} 的义项学习`}>
    <div className="sense-learning-heading"><div><h4>按义项学习</h4><p>每条释义独立判断，不会覆盖这个词的其他含义。</p></div>{data && <span>{data.senses.length} 个义项</span>}</div>
    {loading && <p className="sense-learning-loading" role="status"><LoaderCircle size={15} className="spin" />正在读取可学习义项…</p>}
    {readError && <div className="sense-learning-error" role="alert"><p>{readError}</p><button type="button" className="text-button" onClick={() => setRetry(value => value + 1)}><RefreshCw size={13} />重试</button></div>}
    {data && data.senses.length === 0 && <p className="sense-learning-empty">当前词典还没有可稳定识别的义项，仍可使用整词判断。</p>}
    {data && data.senses.length > 0 && <ol className="sense-learning-list">{data.senses.map((sense, index) => {
      const busy = savingKeys.has(sense.sense_key);
      const error = saveErrors[sense.sense_key];
      const label = `${word} 第 ${index + 1} 个义项${sense.part_of_speech ? `，${sense.part_of_speech}` : ''}：${sense.meaning}`;
      return <li key={sense.sense_key} className={!sense.available ? 'is-unavailable' : undefined}>
        <div className="sense-learning-copy"><span className="sense-learning-number">{String(index + 1).padStart(2, '0')}</span><div>{sense.part_of_speech && <span className="sense-learning-pos">{sense.part_of_speech}</span>}<p>{sense.meaning}</p>{!sense.available && <small>当前词典已不再提供此项，以下保留你的学习快照</small>}</div></div>
        <div className="sense-learning-controls" role="group" aria-label={`${label}的掌握程度`}>{choices.map(({ status, label: choiceLabel, Icon }) => <button
          type="button" key={status} disabled={disabled || busy} aria-pressed={sense.status === status}
          className={`${status.toLowerCase()}${sense.status === status ? ' is-marked' : ''}`}
          onClick={() => void mark(sense, status)}><Icon size={13} />{choiceLabel}</button>)}</div>
        {!sense.available && <p className="sense-learning-snapshot">词典来源已更新，保留已学快照；你仍可继续标记。</p>}
        {busy && <p className="sense-learning-saving" role="status"><LoaderCircle size={13} className="spin" />正在保存这个义项…</p>}
        {error && <div className="sense-learning-error" role="alert"><p>保存失败：{error.message}</p><button type="button" className="text-button" disabled={disabled || busy} onClick={() => void mark(sense, error.status)}>重试保存</button></div>}
      </li>;
    })}</ol>}
    {notice && <p className="sense-learning-notice" role="status">{notice}</p>}
  </section>;
}
