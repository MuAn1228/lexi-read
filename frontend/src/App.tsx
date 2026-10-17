import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, ArrowRight, BarChart3, BookMarked, BookOpen, Check, ChevronDown, Clock3, FileImage, FileText, Leaf, LoaderCircle, Network, Plus, RefreshCw, Upload, X } from 'lucide-react';
import { browserStorageKey, errorMessage, request } from './api';
import ContextPanel from './ContextPanel';
import DocumentPanel from './DocumentPanel';
import DashboardView from './DashboardView';
import RelationsView from './RelationsView';
import { HistoryView, VocabularyView } from './LibraryViews';
import WordList from './WordList';
import type { StatusFilter } from './WordList';
import type { DocumentSummary, Health, LearningEvent, Occurrence, ReadingDocument, View, Vocabulary, WordStatus } from './types';

const navigation = [{ value: 'reader', label: '阅读空间', icon: BookOpen }, { value: 'vocabulary', label: '我的词库', icon: BookMarked }, { value: 'dashboard', label: '学习概览', icon: BarChart3 }, { value: 'relations', label: '我的关联', icon: Network }, { value: 'history', label: '学习足迹', icon: Clock3 }] as const;
const titles: Record<View, { eyebrow: string; title: string; subtitle: string }> = {
  reader: { eyebrow: 'YOUR PERSONAL READING SPACE', title: '读懂文章，也读懂每一个词。', subtitle: '从真实阅读出发，让遇见的单词慢慢成为你的语言。' },
  vocabulary: { eyebrow: 'A VOCABULARY THAT GROWS WITH YOU', title: '把遇见的词，变成自己的。', subtitle: '认识、陌生、偶尔忘记。每一种状态，都值得被记住。' },
  history: { eyebrow: 'YOUR LEARNING JOURNEY', title: '每一小步，都有迹可循。', subtitle: '回看你的阅读、探索与反馈，慢慢看见积累的力量。' },
  dashboard: { eyebrow: 'A CLEARER VIEW OF YOUR LEARNING', title: '看见积累，也看见下一步。', subtitle: '用真实的学习记录，找到值得再读一次的词。' },
  relations: { eyebrow: 'WORDS CONNECTED BY YOUR CURIOSITY', title: '从一个词，走向更多理解。', subtitle: '你看过的关联、留下的反馈，慢慢连成自己的词汇地图。' },
};
function rememberDocument(id: number) { try { localStorage.setItem(browserStorageKey('document'), String(id)); } catch { /* Optional storage: the server already persists this document. */ } }
function rememberedDocument(): number { try { return Number(localStorage.getItem(browserStorageKey('document'))); } catch { return 0; } }

export default function App() {
  const [view, setView] = useState<View>('reader');
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [document, setDocument] = useState<ReadingDocument | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [vocabulary, setVocabulary] = useState<Vocabulary[]>([]);
  const [events, setEvents] = useState<LearningEvent[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [busy, setBusy] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [initializing, setInitializing] = useState(true);
  const [dragOver, setDragOver] = useState(false);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<StatusFilter>('ALL');
  const [uploadOpen, setUploadOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadModal = useRef<HTMLElement>(null);
  const pageHeading = useRef<HTMLHeadingElement>(null);
  const requestGeneration = useRef(0);
  const libraryGeneration = useRef(0);
  const savingRef = useRef(false);
  const busyRef = useRef(false);
  const initializingRef = useRef(false);

  const acceptDocument = useCallback((value: ReadingDocument) => {
    setDocument(value); setSelectedId(value.words[0]?.id ?? null);
    setQuery(''); setFilter('ALL'); setView('reader'); setUploadOpen(false); rememberDocument(value.id);
  }, []);
  const refreshLearning = useCallback(async () => {
    const generation = ++libraryGeneration.current;
    const [words, history] = await Promise.all([request<Vocabulary[]>('/vocabulary'), request<LearningEvent[]>('/events')]);
    if (generation === libraryGeneration.current) {
      setVocabulary(words); setEvents(history);
      const statuses = new Map(words.map(word => [word.lemma, word.status]));
      setDocument(value => value ? { ...value, words: value.words.map(word => statuses.has(word.lemma) ? { ...word, status: statuses.get(word.lemma) ?? null } : word) } : null);
    }
  }, []);
  const openDocument = useCallback(async (id: number) => {
    if (busyRef.current || savingRef.current) return;
    const generation = ++requestGeneration.current;
    busyRef.current = true; setBusy('正在打开阅读材料…'); setError('');
    try { const value = await request<ReadingDocument>(`/documents/${id}`); if (generation === requestGeneration.current) acceptDocument(value); }
    catch (e) { if (generation === requestGeneration.current) setError(errorMessage(e)); }
    finally { if (generation === requestGeneration.current) { busyRef.current = false; setBusy(''); } }
  }, [acceptDocument]);
  const initialize = useCallback(async () => {
    if (initializingRef.current || busyRef.current) return;
    initializingRef.current = true;
    setInitializing(true); setError('');
    const results = await Promise.allSettled([request<DocumentSummary[]>('/documents'), request<Health>('/health'), refreshLearning()]);
    if (results[0].status === 'fulfilled') {
      const list = results[0].value; setDocuments(list);
      if (list.length) await openDocument(list.find(item => item.id === rememberedDocument())?.id ?? list[0].id);
    } else setError(errorMessage(results[0].reason));
    if (results[1].status === 'fulfilled') setHealth(results[1].value);
    if (results[2].status === 'rejected') setError(errorMessage(results[2].reason));
    initializingRef.current = false; setInitializing(false);
  }, [openDocument, refreshLearning]);
  // Avoid duplicate initialization in React development StrictMode.
  const initialized = useRef(false);
  useEffect(() => { if (!initialized.current) { initialized.current = true; void initialize(); } }, [initialize]);
  useEffect(() => { if (view !== 'reader') void refreshLearning().catch(e => setError(errorMessage(e))); }, [view, refreshLearning]);
  useEffect(() => { if (!notice) return; const timer = setTimeout(() => setNotice(''), 3600); return () => clearTimeout(timer); }, [notice]);
  useEffect(() => {
    if (!uploadOpen || !uploadModal.current) return;
    const modal = uploadModal.current;
    const dom = window.document;
    const previousFocus = dom.activeElement instanceof HTMLElement ? dom.activeElement : null;
    const previousOverflow = dom.body.style.overflow;
    dom.body.style.overflow = 'hidden';
    const focusable = () => Array.from(modal.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), [tabindex="0"]')).filter(element => element.getClientRects().length > 0);
    const focusFirst = () => (focusable()[0] ?? modal).focus();
    focusFirst();
    const onModalKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (!busyRef.current) setUploadOpen(false);
      }
      if (event.key !== 'Tab') return;
      const elements = focusable();
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (!first) { event.preventDefault(); modal.focus(); }
      else if (event.shiftKey && (dom.activeElement === first || dom.activeElement === modal)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (dom.activeElement === last || dom.activeElement === modal)) { event.preventDefault(); first.focus(); }
    };
    const onFocus = (event: FocusEvent) => { if (!modal.contains(event.target as Node)) focusFirst(); };
    dom.addEventListener('keydown', onModalKey);
    dom.addEventListener('focusin', onFocus);
    return () => {
      dom.body.style.overflow = previousOverflow;
      dom.removeEventListener('keydown', onModalKey);
      dom.removeEventListener('focusin', onFocus);
      if (previousFocus?.isConnected && !previousFocus.matches(':disabled')) previousFocus.focus();
      else pageHeading.current?.focus();
    };
  }, [uploadOpen]);

  const upload = async (file?: File) => {
    if (!file || busyRef.current || savingRef.current || initializingRef.current) return;
    if (!/\.(jpe?g|png)$/i.test(file.name) || (file.type && !['image/jpeg', 'image/png'].includes(file.type))) { setError('请选择 JPG 或 PNG 格式的英文图片。'); return; }
    if (file.size > 10 * 1024 * 1024) { setError('图片不能超过 10 MB，请压缩后重新上传。'); return; }
    const form = new FormData(); form.append('file', file);
    busyRef.current = true; setBusy('正在识别英文与单词位置…'); setError('');
    const generation = ++requestGeneration.current;
    try {
      const value = await request<ReadingDocument>('/documents', { method: 'POST', body: form });
      if (generation !== requestGeneration.current) return;
      acceptDocument(value); setDocuments(await request<DocumentSummary[]>('/documents')); await refreshLearning();
      setNotice(`已识别 ${value.words.length} 处英文单词，开始阅读吧。`);
    } catch (e) { if (generation === requestGeneration.current) setError(errorMessage(e)); }
    finally { if (generation === requestGeneration.current) { busyRef.current = false; setBusy(''); } }
  };
  const loadDemo = async () => {
    if (busyRef.current || savingRef.current || initializingRef.current) return;
    busyRef.current = true; setBusy('正在准备示例阅读材料…'); setError('');
    const generation = ++requestGeneration.current;
    try {
      const value = await request<ReadingDocument>('/documents/demo', { method: 'POST' });
      if (generation !== requestGeneration.current) return;
      acceptDocument(value); setDocuments(await request<DocumentSummary[]>('/documents')); await refreshLearning();
    }
    catch (e) { if (generation === requestGeneration.current) setError(errorMessage(e)); }
    finally { if (generation === requestGeneration.current) { busyRef.current = false; setBusy(''); } }
  };
  const selected = document?.words.find(word => word.id === selectedId) ?? null;
  const uniqueWords = useMemo(() => {
    const seen = new Set<string>();
    return (document?.words ?? []).filter(word => { if (seen.has(word.lemma)) return false; seen.add(word.lemma); return true; });
  }, [document]);
  const filteredWords = useMemo(() => uniqueWords.filter(word => (word.word.toLowerCase().includes(query.toLowerCase()) || word.lemma.includes(query.toLowerCase())) && (filter === 'ALL' || (filter === 'UNMARKED' ? word.status === null : word.status === filter))), [uniqueWords, query, filter]);
  const nextWord = useCallback(() => {
    if (!filteredWords.length) return;
    const index = filteredWords.findIndex(word => word.id === selectedId || word.lemma === selected?.lemma);
    setSelectedId(filteredWords[(index + 1) % filteredWords.length].id);
  }, [filteredWords, selectedId, selected?.lemma]);
  const mark = useCallback(async (word: string, status: WordStatus, occurrence?: Occurrence) => {
    if (savingRef.current || busyRef.current) return;
    savingRef.current = true; setSaving(true); setError('');
    const next = occurrence ? filteredWords[(Math.max(0, filteredWords.findIndex(item => item.lemma === occurrence.lemma)) + 1) % filteredWords.length] : undefined;
    try {
      const updated = await request<Vocabulary>(`/vocabulary/${encodeURIComponent(word)}`, { method: 'PUT', body: JSON.stringify({ status, ...(occurrence && document ? { document_id: document.id } : {}) }) });
      ++libraryGeneration.current; // Invalidate a library load started before this write.
      setVocabulary(values => [updated, ...values.filter(item => item.id !== updated.id)]);
      setDocument(value => value ? { ...value, words: value.words.map(item => item.lemma === updated.lemma ? { ...item, status: updated.status } : item) } : null);
      if (occurrence && next && next.lemma !== occurrence.lemma) setSelectedId(current => current === occurrence.id ? next.id : current);
      setNotice(`${word} · ${status === 'KNOWN' ? '已掌握' : status === 'UNKNOWN' ? '不认识' : '不确定'}，已保存`);
    } catch (e) { setError(errorMessage(e)); }
    finally { savingRef.current = false; setSaving(false); }
  }, [document, filteredWords]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (view !== 'reader' || !selected || busy || uploadOpen || event.repeat || event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target as HTMLElement;
      if (target.matches('input, textarea, select') || target.isContentEditable) return;
      const statuses: Record<string, WordStatus> = { '1': 'KNOWN', '2': 'UNKNOWN', '3': 'UNCERTAIN' };
      if (statuses[event.key]) { event.preventDefault(); void mark(selected.word, statuses[event.key], selected); }
      if (event.key === 'ArrowDown') { event.preventDefault(); nextWord(); }
    };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  }, [view, selected, busy, uploadOpen, mark, nextWord]);
  const markedCount = uniqueWords.filter(word => word.status !== null).length;
  const progress = uniqueWords.length ? Math.round(markedCount / uniqueWords.length * 100) : 0;
  const title = titles[view];
  const isLoading = Boolean(busy) || initializing;
  const uploadZone = <div className={`upload-zone ${dragOver ? 'drag-over' : ''}`} onDragOver={event => { event.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)} onDrop={event => { event.preventDefault(); setDragOver(false); void upload(event.dataTransfer.files[0]); }}>
    <div className="upload-icon"><Upload size={25} /></div><h3>把一篇英文，带进你的阅读空间。</h3><p>拖入英文试卷、教材或文章截图<br /><span>JPG / PNG · 最大 10 MB</span></p><button className="button primary" disabled={isLoading} onClick={() => fileInput.current?.click()}><Plus size={17} />选择图片</button><div className="demo-link"><span>先感受一下？</span><button className="text-button" disabled={isLoading} onClick={() => void loadDemo()}>打开示例文章 <ArrowRight size={14} /></button></div>
  </div>;

  return <div className="app-shell">
    <header className="topbar"><a className="brand" href="#" onClick={e => { e.preventDefault(); setView('reader'); }} aria-label="词间 Lexi 首页"><span className="brand-icon"><Leaf size={21} /></span><span>词间<span className="brand-en">lexi</span></span></a>
      <nav aria-label="主要导航">{navigation.map(item => <button key={item.value} className={view === item.value ? 'active' : ''} aria-current={view === item.value ? 'page' : undefined} onClick={() => setView(item.value)}><item.icon size={17} /><span>{item.label}</span>{item.value === 'vocabulary' && vocabulary.filter(word => word.status !== null).length > 0 && <span className="nav-count">{vocabulary.filter(word => word.status !== null).length}</span>}</button>)}</nav>
      <div className="profile"><span className="local-dot" />{health?.auth_mode === 'guest' ? '我的学习空间' : '本地学习空间'}<span className="avatar">L</span></div>
    </header>
    <main>
      <div className="page-heading"><div><p className="overline">{title.eyebrow}</p><h1 ref={pageHeading} tabIndex={-1}>{title.title}</h1><p className="page-subtitle">{title.subtitle}</p></div><button className="button primary new-reading" disabled={isLoading || saving} onClick={() => { setView('reader'); setDragOver(false); setUploadOpen(true); }}><Plus size={17} />添加阅读材料</button></div>
      {health?.auth_mode === 'guest' && <p className="page-subtitle">学习记录保存在服务器，访问凭证保存在当前浏览器。清除浏览器数据会失去访问权限；暂不支持跨设备登录。</p>}
      <input ref={fileInput} type="file" accept="image/png,image/jpeg,.jpg,.jpeg,.png" className="visually-hidden" aria-label="上传英文图片" onChange={event => { void upload(event.target.files?.[0]); event.target.value = ''; }} />
      {error && <div className="error-banner" role="alert"><AlertCircle size={19} /><span>{error}</span>{!document && <button className="text-button" onClick={() => void initialize()}><RefreshCw size={14} />重试连接</button>}<button className="icon-button" aria-label="关闭错误提示" onClick={() => setError('')}><X size={17} /></button></div>}
      {isLoading && <div className="processing-banner" role="status"><LoaderCircle size={19} className="spin" /><span>{busy || '正在加载你的阅读空间…'}{busy.includes('识别') && <small>首次 OCR 运行需要加载模型，请保持页面打开。</small>}</span></div>}
      {view === 'reader' && <>
        {document ? <>
          <section className="reading-bar"><div className="document-selector"><span className="document-file-icon"><FileText size={19} /></span><label><span className="overline">CURRENT READING</span><div className="select-wrap"><select aria-label="切换阅读材料" value={document.id} disabled={isLoading || saving} onChange={event => void openDocument(Number(event.target.value))}>{documents.length ? documents.map(item => <option key={item.id} value={item.id}>{item.title}</option>) : <option value={document.id}>{document.title}</option>}</select><ChevronDown size={14} /></div></label></div><div className="reading-progress"><div><span>词汇扫描进度</span><strong>{markedCount}<span> / {uniqueWords.length}</span></strong></div><div className="progress-track"><div style={{ width: `${progress}%` }} /></div></div><div className="legend"><span><i className="known" />已掌握</span><span><i className="unknown" />不认识</span><span><i className="uncertain" />不确定</span></div></section>
          <div className="reading-workspace"><DocumentPanel key={document.id} document={document} selectedId={selectedId} onSelect={word => setSelectedId(word.id)} /><WordList words={filteredWords} selectedId={uniqueWords.find(word => word.lemma === selected?.lemma)?.id ?? null} query={query} setQuery={setQuery} filter={filter} setFilter={setFilter} total={uniqueWords.length} onSelect={word => setSelectedId(word.id)} onMark={(word, status) => void mark(word.word, status, word)} saving={saving || isLoading} /><ContextPanel key={selectedId} word={selected} onMark={status => selected && void mark(selected.word, status, selected)} onNext={nextWord} saving={saving || isLoading} vocabulary={vocabulary} onLearningChanged={refreshLearning} /></div>
        </> : !initializing && <div className="welcome-grid"><section className="welcome-story"><span className="welcome-label"><Leaf size={15} />学习，发生在真实阅读里</span><h2>少一点机械记忆，<br />多一点<span>真正理解。</span></h2><p>读你感兴趣的内容，圈出还不熟悉的单词。<br />词间会记住你的反馈，陪你建立自己的词汇地图。</p><div className="reading-illustration" aria-hidden="true"><div className="paper-card"><span>A SMALL NOTE ON LEARNING</span><p>Read with <mark>curiosity.</mark><br />Make room for<br /><em>understanding.</em></p><div className="paper-lines"><i /><i /><i /></div></div><div className="word-note"><Leaf size={16} /><span>从语境里，认识一个词。</span></div></div><div className="welcome-steps"><span><b>01</b>带入文章</span><ArrowRight size={14} /><span><b>02</b>标记单词</span><ArrowRight size={14} /><span><b>03</b>连接新知</span></div></section><section className="upload-card panel">{uploadZone}<div className="upload-note"><BookOpen size={16} /><p>示例为已预标注的英文图片，可直接体验标记、词库和关联学习。</p></div></section></div>}
      </>}
      {view === 'vocabulary' && <VocabularyView vocabulary={vocabulary} saving={saving} onMark={(word, status) => void mark(word, status)} />}
      {view === 'history' && <HistoryView events={events} />}
      {view === 'dashboard' && <DashboardView vocabulary={vocabulary} />}
      {view === 'relations' && <RelationsView vocabulary={vocabulary} />}
      <footer className="app-footer"><span><Leaf size={13} />词间 · 在阅读中生长</span><span>{health ? `${health.llm_provider === 'offline' ? '离线学习模式' : 'AI 语境服务已配置'} · ${health.auth_mode === 'guest' ? '学习记录保存在服务器' : '学习记录保存在本机'}` : '正在连接学习空间'}</span></footer>
    </main>
    {notice && <div className="toast" role="status"><Check size={17} />{notice}</div>}
    {uploadOpen && <div className="modal-backdrop" onClick={() => !isLoading && setUploadOpen(false)}><section ref={uploadModal} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="upload-title" className="upload-modal panel" onClick={event => event.stopPropagation()}><div className="panel-heading"><div><FileImage size={17} /><h2 id="upload-title">新的阅读，从这里开始</h2></div><button className="icon-button" disabled={isLoading} onClick={() => setUploadOpen(false)} aria-label="关闭上传窗口"><X size={20} /></button></div>{uploadZone}{error && <p className="modal-error" role="alert">{error}</p>}{isLoading && <div className="loading-copy modal-loading"><LoaderCircle className="spin" size={16} />{busy}</div>}</section></div>}
  </div>;
}
