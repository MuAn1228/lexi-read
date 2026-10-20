import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Archive, ArrowRight, BarChart3, Bookmark, BookMarked, BookOpen, Check, ChevronDown, Clock3, FileImage, FileText, FolderOpen, Globe, Leaf, LoaderCircle, Network, Plus, RefreshCw, Sparkles, Upload, X } from 'lucide-react';
import { browserStorageKey, errorMessage, request, reviewStorageKey } from './api';
import ContextPanel from './ContextPanel';
import ArticleQuestionPanel from './ArticleQuestionPanel';
import type { ArticleFocus } from './articleQuestions';
import DocumentPanel from './DocumentPanel';
import DocumentLibraryView from './DocumentLibraryView';
import DataManagementView from './DataManagementView';
import { clearLearningPreferences } from './backupData';
import type { BackupRestore, RestoreOutcome } from './backupData';
import { activeArticleList, initialArticleSelection } from './documentLibrary';
import type { DocumentPatch } from './documentLibrary';
import DashboardView from './DashboardView';
import RelationsView from './RelationsView';
import ReviewView from './ReviewView';
import PracticeGenerationView from './PracticeGenerationView';
import TextImportForm from './TextImportForm';
import PdfImportForm from './PdfImportForm';
import PdfProofreadWorkbench from './PdfProofreadWorkbench';
import UrlImportForm from './UrlImportForm';
import { pdfImportProblem } from './pdfArticle';
import type { PdfRecognitionMode } from './pdfArticle';
import { urlImportProblem } from './urlArticle';
import type { UrlImportPayload } from './urlArticle';
import { normalizeArticle, textImportProblem } from './textArticle';
import type { TextDraft, TextImportPayload } from './textArticle';
import { HistoryView, VocabularyView } from './LibraryViews';
import WordList from './WordList';
import ReadingGarden from './components/ReadingGarden';
import StudioLanding from './components/StudioLanding';
import StudioShelf from './components/StudioShelf';
import StudioEffects from './components/StudioEffects';
import StudioMotion, { MotionTitle } from './components/StudioMotion';
import InspirationCorner from './components/InspirationCorner';
import type { StatusFilter } from './WordList';
import type { DocumentSummary, Health, LearningEvent, Occurrence, ReadingDocument, ReviewItem, View, Vocabulary, WordStatus } from './types';

const navigation = [{ value: 'reader', label: '阅读空间', icon: BookOpen }, { value: 'library', label: '文章库', icon: FolderOpen }, { value: 'vocabulary', label: '我的词库', icon: BookMarked }, { value: 'review', label: '生词复习', icon: RefreshCw }, { value: 'practice', label: '短文练习', icon: Sparkles }, { value: 'dashboard', label: '学习概览', icon: BarChart3 }, { value: 'relations', label: '我的关联', icon: Network }, { value: 'history', label: '学习足迹', icon: Clock3 }, { value: 'data', label: '数据管理', icon: Archive }] as const;
const titles: Record<View, { eyebrow: string; title: string; subtitle: string }> = {
  reader: { eyebrow: 'YOUR PERSONAL READING SPACE', title: '读懂文章，也读懂每一个词。', subtitle: '从真实阅读出发，让遇见的单词慢慢成为你的语言。' },
  library: { eyebrow: 'MAKE ROOM FOR YOUR READING', title: '好文章，值得好好收藏。', subtitle: '整理标题、添上标签，留一个书签，下次从这里继续。' },
  vocabulary: { eyebrow: 'A VOCABULARY THAT GROWS WITH YOU', title: '把遇见的词，变成自己的。', subtitle: '认识、陌生、偶尔忘记。每一种状态，都值得被记住。' },
  review: { eyebrow: 'ANOTHER MOMENT WITH YOUR WORDS', title: '再遇见，让生词慢慢熟悉。', subtitle: '先回想再揭晓释义，也可以填回原句中的词，记录此刻的掌握程度。' },
  practice: { eyebrow: 'A NEW STORY WITH YOUR WORDS', title: '用自己的生词，读一个新故事。', subtitle: '围绕需要巩固的词，生成你感兴趣的英文短文，在新的语境里再遇见。' },
  history: { eyebrow: 'YOUR LEARNING JOURNEY', title: '每一小步，都有迹可循。', subtitle: '回看你的阅读、探索与反馈，慢慢看见积累的力量。' },
  dashboard: { eyebrow: 'A CLEARER VIEW OF YOUR LEARNING', title: '看见积累，也看见下一步。', subtitle: '用真实的学习记录，找到值得再读一次的词。' },
  relations: { eyebrow: 'WORDS CONNECTED BY YOUR CURIOSITY', title: '从一个词，走向更多理解。', subtitle: '你看过的关联、留下的反馈，慢慢连成自己的词汇地图。' },
  data: { eyebrow: 'KEEP YOUR LEARNING CLOSE', title: '每一份积累，都值得留下。', subtitle: '保存一份完整备份，或把熟悉的学习空间带回来。' },
};
function rememberDocument(id: number) { try { localStorage.setItem(browserStorageKey('document'), String(id)); } catch { /* Optional storage: the server already persists this document. */ } }
function rememberedDocument(): number { try { return Number(localStorage.getItem(browserStorageKey('document'))); } catch { return 0; } }
function forgetDocument() { try { localStorage.removeItem(browserStorageKey('document')); } catch { /* Optional reading preference. */ } }

export default function App() {
  const [view, setView] = useState<View>('reader');
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [document, setDocument] = useState<ReadingDocument | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [citationFocus, setCitationFocus] = useState<ArticleFocus | null>(null);
  const citationToken = useRef(0);
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
  const [importMode, setImportMode] = useState<'image' | 'text' | 'pdf' | 'url'>('image');
  const [pdfDraft, setPdfDraft] = useState<{ file: File; request_id: string } | null>(null);
  const [pdfMode, setPdfMode] = useState<PdfRecognitionMode>('auto');
  const [pdfProcessing, setPdfProcessing] = useState(false);
  const [proofread, setProofread] = useState<{ documentId: number; kind: 'pdf' | 'image'; title: string; initialPage: number; opener: HTMLElement } | null>(null);
  const [proofreadSync, setProofreadSync] = useState<{ documentId: number; error: string } | null>(null);
  const [articleTextRevision, setArticleTextRevision] = useState(0);
  const proofreadLock = useRef(false);
  proofreadLock.current = proofread !== null;
  const proofreadUncertain = useRef<number | null>(null);
  const proofreadSyncGeneration = useRef(0);
  const readerBlocked = proofreadSync !== null && document?.id === proofreadSync.documentId;
  const readerBlockedRef = useRef(false);
  readerBlockedRef.current = readerBlocked;
  const [textDraft, setTextDraft] = useState<TextDraft>({ title: '', text: '' });
  const [urlDraft, setUrlDraft] = useState('');
  const [importSyncError, setImportSyncError] = useState('');
  const [syncingImport, setSyncingImport] = useState(false);
  const [documentRevision, setDocumentRevision] = useState(0);
  const [documentSyncError, setDocumentSyncError] = useState('');
  const [syncingDocuments, setSyncingDocuments] = useState(false);
  const [revealBookmark, setRevealBookmark] = useState(0);
  const [restorationLocked, setRestorationLocked] = useState(false);
  const restorationLock = useRef(false);
  const documentEpoch = useRef(0);
  const documentSyncGeneration = useRef(0);
  const currentDocumentId = useRef<number | null>(null);
  currentDocumentId.current = document?.id ?? null;
  const textImportRequest = useRef<TextImportPayload | null>(null);
  const urlImportRequest = useRef<UrlImportPayload | null>(null);
  const importSyncGeneration = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadModal = useRef<HTMLElement>(null);
  const uploadOpener = useRef<HTMLElement | null>(null);
  const pageHeading = useRef<HTMLHeadingElement>(null);
  const navigationElement = useRef<HTMLElement>(null);
  const requestGeneration = useRef(0);
  const libraryGeneration = useRef(0);
  const savingRef = useRef(false);
  const busyRef = useRef(false);
  const initializingRef = useRef(false);

  const openUpload = (opener: HTMLElement | null, mode?: typeof importMode) => {
    if (proofreadLock.current) return;
    // Capture before a newly mounted form's autoFocus moves focus into the dialog.
    if (!uploadOpen) uploadOpener.current = opener;
    if (mode) setImportMode(mode);
    setUploadOpen(true);
  };

  const acceptDocument = useCallback((value: ReadingDocument, occurrenceId?: number) => {
    if (proofreadUncertain.current === value.id) { proofreadUncertain.current = null; setProofreadSync(null); }
    setDocument(value); setSelectedId(initialArticleSelection(value, occurrenceId)); setRevealBookmark(value => value + 1);
    setQuery(''); setFilter('ALL'); setView('reader'); setUploadOpen(false); rememberDocument(value.id);
  }, []);
  const refreshLearning = useCallback(async () => {
    if (restorationLock.current) return;
    const generation = ++libraryGeneration.current;
    const [words, history] = await Promise.all([request<Vocabulary[]>('/vocabulary'), request<LearningEvent[]>('/events')]);
    if (generation === libraryGeneration.current) {
      setVocabulary(words); setEvents(history);
      const statuses = new Map(words.map(word => [word.lemma, word.status]));
      setDocument(value => value ? { ...value, words: value.words.map(word => statuses.has(word.lemma) ? { ...word, status: statuses.get(word.lemma) ?? null } : word) } : null);
    }
  }, []);
  const openDocument = useCallback(async (id: number, occurrenceId?: number) => {
    if (restorationLock.current || proofreadLock.current || busyRef.current || savingRef.current) return;
    const generation = ++requestGeneration.current;
    busyRef.current = true; setBusy('正在打开阅读材料…'); setError('');
    try { const value = await request<ReadingDocument>(`/documents/${id}`); if (generation === requestGeneration.current) acceptDocument(value, occurrenceId); }
    catch (e) { if (generation === requestGeneration.current) setError(errorMessage(e)); }
    finally { if (generation === requestGeneration.current) { busyRef.current = false; setBusy(''); } }
  }, [acceptDocument]);
  const initialize = useCallback(async () => {
    if (restorationLock.current || proofreadLock.current || initializingRef.current || busyRef.current || savingRef.current) return;
    initializingRef.current = true;
    const epoch = documentEpoch.current;
    setInitializing(true); setError('');
    const results = await Promise.allSettled([request<DocumentSummary[]>('/documents'), request<Health>('/health'), refreshLearning()]);
    if (results[0].status === 'fulfilled' && epoch === documentEpoch.current) {
      const list = results[0].value; setDocuments(list);
      if (list.length) await openDocument(list.find(item => item.id === rememberedDocument())?.id ?? list[0].id);
    } else if (results[0].status === 'rejected') setError(errorMessage(results[0].reason));
    if (results[1].status === 'fulfilled') setHealth(results[1].value);
    if (results[2].status === 'rejected') setError(errorMessage(results[2].reason));
    initializingRef.current = false; setInitializing(false);
  }, [openDocument, refreshLearning]);
  // Avoid duplicate initialization in React development StrictMode.
  const initialized = useRef(false);
  useEffect(() => { if (!initialized.current) { initialized.current = true; void initialize(); } }, [initialize]);
  useEffect(() => { if (view !== 'reader') void refreshLearning().catch(e => setError(errorMessage(e))); }, [view, refreshLearning]);
  useEffect(() => {
    const revealActiveNavigation = () => {
      const nav = navigationElement.current;
      const button = nav?.querySelector<HTMLButtonElement>('[aria-current="page"]');
      if (!nav || !button || nav.scrollWidth <= nav.clientWidth) return;
      const container = nav.getBoundingClientRect(); const item = button.getBoundingClientRect();
      if (item.left < container.left + 8) nav.scrollLeft += item.left - container.left - 8;
      else if (item.right > container.right - 8) nav.scrollLeft += item.right - container.right + 8;
    };
    revealActiveNavigation();
    window.addEventListener('resize', revealActiveNavigation);
    return () => window.removeEventListener('resize', revealActiveNavigation);
  }, [view, health?.auth_mode]);
  useEffect(() => { setCitationFocus(null); }, [view, document?.id, document?.ocr_text, selectedId, revealBookmark, restorationLocked]);
  useEffect(() => { if (!notice) return; const timer = setTimeout(() => setNotice(''), 3600); return () => clearTimeout(timer); }, [notice]);
  useEffect(() => {
    if (!uploadOpen || !uploadModal.current) return;
    const modal = uploadModal.current;
    const dom = window.document;
    const previousFocus = uploadOpener.current;
    const previousOverflow = dom.body.style.overflow;
    dom.body.style.overflow = 'hidden';
    const focusable = () => Array.from(modal.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex="0"]')).filter(element => element.getClientRects().length > 0);
    const focusFirst = () => (focusable()[0] ?? modal).focus();
    (modal.querySelector<HTMLElement>('[data-autofocus]') ?? focusable()[0] ?? modal).focus();
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

  const refreshImportedMaterial = useCallback(async () => {
    const generation = ++importSyncGeneration.current;
    const epoch = documentEpoch.current;
    setSyncingImport(true);
    const results = await Promise.allSettled([request<DocumentSummary[]>('/documents'), refreshLearning()]);
    if (generation !== importSyncGeneration.current) return;
    if (results[0].status === 'fulfilled' && epoch === documentEpoch.current) setDocuments(results[0].value);
    const failed = results.find(result => result.status === 'rejected');
    setImportSyncError(failed?.status === 'rejected' ? `阅读材料已保存；阅读材料列表或词库暂未刷新：${errorMessage(failed.reason)}` : '');
    setSyncingImport(false);
  }, [refreshLearning]);
  const refreshDocumentList = useCallback(async () => {
    const generation = ++documentSyncGeneration.current;
    const epoch = documentEpoch.current;
    setSyncingDocuments(true);
    try {
      const items = await request<DocumentSummary[]>('/documents');
      if (generation !== documentSyncGeneration.current || epoch !== documentEpoch.current) return;
      setDocuments(items); setDocumentSyncError('');
      const id = currentDocumentId.current;
      if (id !== null) {
        const summary = items.find(item => item.id === id);
        if (summary) setDocument(value => value?.id === id ? { ...value, ...summary } : value);
        else { setDocument(value => value?.id === id ? null : value); setSelectedId(null); forgetDocument(); }
      }
    } catch (e) {
      if (generation === documentSyncGeneration.current && epoch === documentEpoch.current) setDocumentSyncError(`阅读材料列表暂未刷新：${errorMessage(e)}`);
    } finally { if (generation === documentSyncGeneration.current) setSyncingDocuments(false); }
  }, []);
  const openProofread = (initialPage: number, opener: HTMLElement) => {
    if (!document || !['pdf', 'upload', 'demo'].includes(document.source) || restorationLock.current || proofreadLock.current || readerBlockedRef.current || busyRef.current || savingRef.current || initializingRef.current || uploadOpen) return;
    // Older list reads must not overwrite summaries returned by this explicit correction.
    ++documentEpoch.current; ++documentSyncGeneration.current; ++importSyncGeneration.current;
    setSyncingDocuments(false); setSyncingImport(false); setDragOver(false);
    proofreadLock.current = true;
    setProofread({ documentId: document.id, kind: document.source === 'pdf' ? 'pdf' : 'image', title: document.title, initialPage, opener });
  };
  const applyProofreadDocument = useCallback((value: ReadingDocument) => {
    ++documentEpoch.current; ++libraryGeneration.current; ++documentSyncGeneration.current; ++importSyncGeneration.current;
    setSyncingDocuments(false); setSyncingImport(false);
    setDocument(current => current?.id === value.id ? value : current);
    setDocuments(items => activeArticleList(items, { ...value, word_count: value.words.length }));
    if (currentDocumentId.current === value.id) {
      // All occurrence IDs and offsets can change; never retain the previous selection or citations.
      setSelectedId(null); setCitationFocus(null); setQuery(''); setFilter('ALL');
      setRevealBookmark(number => number + 1); setArticleTextRevision(number => number + 1);
    }
    setDocumentRevision(number => number + 1);
  }, []);
  const reconcileProofread = async (id: number) => {
    if (restorationLock.current || proofreadLock.current || busyRef.current || savingRef.current) return;
    const generation = ++proofreadSyncGeneration.current;
    proofreadUncertain.current = id; setProofreadSync({ documentId: id, error: '' });
    busyRef.current = true; setBusy('正在核对校对后的阅读正文…');
    try {
      // A lost PUT response may follow a committed save. Reconciliation is strictly read-only.
      const value = await request<ReadingDocument>(`/documents/${id}`);
      if (generation !== proofreadSyncGeneration.current) return;
      applyProofreadDocument(value); proofreadUncertain.current = null; setProofreadSync(null);
      setNotice('已重新核对正文，当前显示服务器保存的最新版本。');
      void refreshImportedMaterial();
    } catch (e) {
      if (generation === proofreadSyncGeneration.current) setProofreadSync({ documentId: id, error: `正文最新版本暂未核对：${errorMessage(e)} 为避免使用旧的单词位置，请先重新核对正文。` });
    } finally { if (generation === proofreadSyncGeneration.current) { busyRef.current = false; setBusy(''); } }
  };
  const closeUncertainProofread = () => {
    const id = proofread?.documentId; if (id === undefined) return;
    proofreadLock.current = false; setProofread(null);
    setSelectedId(null); setCitationFocus(null); setArticleTextRevision(number => number + 1);
    void reconcileProofread(id);
  };
  const proofreadSaved = (value: ReadingDocument) => {
    applyProofreadDocument(value); proofreadLock.current = false; setProofread(null);
    setNotice(`校对已保存。阅读正文、单词位置与原句已更新，${value.source === 'pdf' ? '原 PDF' : '原始图片'}保留。`);
    void refreshImportedMaterial();
  };
  const savePractice = useCallback(async (payload: TextImportPayload): Promise<ReadingDocument> => {
    if (restorationLock.current || proofreadLock.current || readerBlockedRef.current || busyRef.current || savingRef.current || initializingRef.current) throw new Error('正在处理其他操作，请稍后重试保存。');
    busyRef.current = true; setBusy('正在保存练习短文…'); setError('');
    const generation = ++requestGeneration.current;
    try {
      const value = await request<ReadingDocument>('/documents/text', { method: 'POST', body: JSON.stringify(payload) });
      if (generation !== requestGeneration.current || restorationLock.current) throw new Error('阅读空间已变化，请重新确认这次保存。');
      acceptDocument(value); ++documentEpoch.current;
      setDocuments(items => activeArticleList(items, { ...value, word_count: value.words.length }));
      setDocumentRevision(value => value + 1);
      setNotice('练习短文已保存，可以继续阅读、查词与问答。');
      // A confirmed import is complete. Subsequent reads never replay the successful write.
      void refreshImportedMaterial();
      return value;
    } finally { if (generation === requestGeneration.current) { busyRef.current = false; setBusy(''); } }
  }, [acceptDocument, refreshImportedMaterial]);
  const changeArticle = useCallback(async (id: number, action: 'update' | 'delete' | 'restore', patch?: DocumentPatch): Promise<DocumentSummary> => {
    if (restorationLock.current || proofreadLock.current || readerBlockedRef.current || savingRef.current || busyRef.current || initializingRef.current) throw new Error('正在处理其他操作，请稍后重试。');
    savingRef.current = true; setSaving(true);
    ++documentEpoch.current; ++importSyncGeneration.current; setSyncingImport(false);
    ++requestGeneration.current;
    try {
      const updated = await request<DocumentSummary>(`/documents/${id}${action === 'restore' ? '/restore' : ''}`, {
        method: action === 'update' ? 'PUT' : action === 'delete' ? 'DELETE' : 'POST',
        ...(patch ? { body: JSON.stringify(patch) } : {}),
      });
      ++documentEpoch.current;
      setDocuments(items => activeArticleList(items, updated));
      setDocument(value => value?.id === id ? updated.deleted_at ? null : { ...value, ...updated } : value);
      if (updated.deleted_at && document?.id === id) { setSelectedId(null); forgetDocument(); }
      setDocumentRevision(value => value + 1);
      setNotice(action === 'delete' ? '文章已移到回收站，词库和学习记录保留。' : action === 'restore' ? '文章已恢复，可以继续阅读。' : patch && 'bookmark_occurrence_id' in patch ? patch.bookmark_occurrence_id === null ? '阅读书签已清除。' : '当前阅读位置已保存。' : '文章标题与标签已保存。');
      void refreshDocumentList();
      return updated;
    } catch (e) {
      // A lost response may follow a committed write. Reconcile through GET, never replay automatically.
      void refreshDocumentList();
      throw e;
    } finally { savingRef.current = false; setSaving(false); }
  }, [document?.id, refreshDocumentList]);
  const changeTextDraft = (draft: TextDraft) => {
    textImportRequest.current = null;
    setTextDraft(draft); setError('');
  };
  const importText = async () => {
    if (restorationLock.current || proofreadLock.current || busyRef.current || savingRef.current || initializingRef.current) return;
    const problem = textImportProblem(textDraft);
    if (problem) { setError(problem); return; }
    const normalized = { title: textDraft.title.trim(), text: normalizeArticle(textDraft.text) };
    let payload = textImportRequest.current;
    if (!payload || payload.title !== normalized.title || payload.text !== normalized.text) {
      payload = { ...normalized, request_id: crypto.randomUUID() };
      textImportRequest.current = payload;
    }
    busyRef.current = true; setBusy('正在导入英文文章…'); setError('');
    const generation = ++requestGeneration.current;
    try {
      const value = await request<ReadingDocument>('/documents/text', { method: 'POST', body: JSON.stringify(payload) });
      if (generation !== requestGeneration.current) return;
      // A confirmed write completes import even if subsequent list/learning reads fail.
      acceptDocument(value);
      setDocuments(items => [{ ...value, word_count: value.words.length }, ...items.filter(item => item.id !== value.id)]);
      ++documentEpoch.current; setDocumentRevision(value => value + 1);
      textImportRequest.current = null; setTextDraft({ title: '', text: '' });
      setNotice(`文章已导入，共 ${value.words.length} 处英文单词，开始阅读吧。`);
      void refreshImportedMaterial();
    } catch (e) { if (generation === requestGeneration.current) setError(errorMessage(e)); }
    finally { if (generation === requestGeneration.current) { busyRef.current = false; setBusy(''); } }
  };

  const changeUrlDraft = (url: string) => {
    urlImportRequest.current = null;
    setUrlDraft(url); setError('');
  };
  const importUrl = async () => {
    if (restorationLock.current || proofreadLock.current || busyRef.current || savingRef.current || initializingRef.current) return;
    const problem = urlImportProblem(urlDraft);
    if (problem) { setError(problem); return; }
    const url = urlDraft.trim();
    let payload = urlImportRequest.current;
    if (!payload || payload.url !== url) {
      payload = { url, request_id: crypto.randomUUID() };
      urlImportRequest.current = payload;
    }
    busyRef.current = true; setBusy('正在提取网页正文…'); setError('');
    const generation = ++requestGeneration.current;
    try {
      const value = await request<ReadingDocument>('/documents/url', { method: 'POST', body: JSON.stringify(payload) });
      if (generation !== requestGeneration.current) return;
      acceptDocument(value); ++documentEpoch.current;
      setDocuments(items => activeArticleList(items, { ...value, word_count: value.words.length }));
      setDocumentRevision(value => value + 1);
      urlImportRequest.current = null; setUrlDraft('');
      setNotice(`网页已导入，共 ${value.words.length} 处英文单词。`);
      // Only reconcile through reads after the import is confirmed; never repeat a successful POST.
      void refreshImportedMaterial();
    } catch (e) { if (generation === requestGeneration.current) setError(errorMessage(e)); }
    finally { if (generation === requestGeneration.current) { busyRef.current = false; setBusy(''); } }
  };

  const choosePdf = (file: File) => {
    if (restorationLock.current || proofreadLock.current || busyRef.current || savingRef.current || initializingRef.current) return;
    const problem = pdfImportProblem(file);
    if (problem) { setError(problem); return; }
    setPdfDraft({ file, request_id: crypto.randomUUID() }); setError('');
  };
  const changePdfMode = (mode: PdfRecognitionMode) => {
    if (mode === pdfMode || restorationLock.current || proofreadLock.current || busyRef.current || savingRef.current || initializingRef.current) return;
    setPdfMode(mode);
    // Changing recognition changes the intended import. An unchanged retry keeps its receipt.
    setPdfDraft(value => value ? { ...value, request_id: crypto.randomUUID() } : null);
    setError('');
  };
  const importPdf = async () => {
    if (!pdfDraft || restorationLock.current || proofreadLock.current || busyRef.current || savingRef.current || initializingRef.current) return;
    const problem = pdfImportProblem(pdfDraft.file, pdfMode);
    if (problem) { setError(problem); return; }
    const form = new FormData(); form.append('file', pdfDraft.file); form.append('request_id', pdfDraft.request_id); form.append('ocr_mode', pdfMode);
    busyRef.current = true; setPdfProcessing(true); setBusy(pdfMode === 'text' ? '正在提取 PDF 正文与页码…' : '正在逐页读取与识别 PDF…'); setError('');
    const generation = ++requestGeneration.current;
    try {
      const value = await request<ReadingDocument>('/documents/pdf', { method: 'POST', body: form });
      if (generation !== requestGeneration.current) return;
      acceptDocument(value); ++documentEpoch.current;
      setDocuments(items => activeArticleList(items, { ...value, word_count: value.words.length }));
      setDocumentRevision(value => value + 1); setPdfDraft(null);
      setNotice(`PDF 已导入，共 ${value.page_count} 页、${value.words.length} 处英文单词。`);
      void refreshImportedMaterial();
    } catch (e) { if (generation === requestGeneration.current) setError(errorMessage(e)); }
    finally { if (generation === requestGeneration.current) { busyRef.current = false; setPdfProcessing(false); setBusy(''); } }
  };
  const upload = async (file?: File) => {
    if (!file || restorationLock.current || proofreadLock.current || busyRef.current || savingRef.current || initializingRef.current) return;
    if (/\.pdf$/i.test(file.name) || ['application/pdf', 'application/x-pdf'].includes(file.type)) { const focused = window.document.activeElement; openUpload(focused instanceof HTMLElement && focused !== window.document.body ? focused : pageHeading.current, 'pdf'); choosePdf(file); return; }
    if (!/\.(jpe?g|png)$/i.test(file.name) || (file.type && !['image/jpeg', 'image/png'].includes(file.type))) { setError('请选择 JPG 或 PNG 格式的英文图片。'); return; }
    if (file.size > 10 * 1024 * 1024) { setError('图片不能超过 10 MB，请压缩后重新上传。'); return; }
    const form = new FormData(); form.append('file', file);
    busyRef.current = true; setBusy('正在识别英文与单词位置…'); setError('');
    const generation = ++requestGeneration.current;
    try {
      const value = await request<ReadingDocument>('/documents', { method: 'POST', body: form });
      if (generation !== requestGeneration.current) return;
      acceptDocument(value); ++documentEpoch.current; setDocuments(items => activeArticleList(items, { ...value, word_count: value.words.length })); setDocumentRevision(value => value + 1); void refreshImportedMaterial();
      setNotice(`已识别 ${value.words.length} 处英文单词，开始阅读吧。`);
    } catch (e) { if (generation === requestGeneration.current) setError(errorMessage(e)); }
    finally { if (generation === requestGeneration.current) { busyRef.current = false; setBusy(''); } }
  };
  const loadDemo = async () => {
    if (restorationLock.current || proofreadLock.current || busyRef.current || savingRef.current || initializingRef.current) return;
    busyRef.current = true; setBusy('正在准备示例阅读材料…'); setError('');
    const generation = ++requestGeneration.current;
    try {
      const value = await request<ReadingDocument>('/documents/demo', { method: 'POST' });
      if (generation !== requestGeneration.current) return;
      acceptDocument(value); ++documentEpoch.current; setDocuments(items => activeArticleList(items, { ...value, word_count: value.words.length })); setDocumentRevision(value => value + 1); void refreshImportedMaterial();
    }
    catch (e) { if (generation === requestGeneration.current) setError(errorMessage(e)); }
    finally { if (generation === requestGeneration.current) { busyRef.current = false; setBusy(''); } }
  };
  const clearRestoredBrowserState = useCallback(async (): Promise<string> => {
    try {
      return clearLearningPreferences(window.localStorage, await reviewStorageKey(), browserStorageKey('document'));
    } catch {
      return '学习数据已恢复，但当前浏览器的复习进度与阅读位置未能清理。请允许浏览器存储后重新清理，再进入阅读空间。';
    }
  }, []);
  const restoreBackup = useCallback(async (token: string): Promise<RestoreOutcome> => {
    if (health?.auth_mode !== 'local') throw new Error('完整备份恢复仅可在本地学习空间使用。');
    if (proofreadLock.current || savingRef.current || busyRef.current || initializingRef.current) throw new Error('正在处理其他操作，请稍后重试。');
    // Keep navigation locked even after an uncertain response: the database may already be restored.
    restorationLock.current = true; setRestorationLocked(true); setView('data');
    savingRef.current = true; setSaving(true); busyRef.current = true; setBusy('正在恢复学习数据，请保持页面打开…'); setError('');
    ++documentEpoch.current; ++requestGeneration.current; ++libraryGeneration.current;
    ++importSyncGeneration.current; ++documentSyncGeneration.current;
    setSyncingImport(false); setSyncingDocuments(false);
    try {
      const result = await request<BackupRestore>('/backups/restore', { method: 'POST', body: JSON.stringify({ token, confirm: true }) });
      if (!result.restored) throw new Error('恢复结果尚未确认，请用当前预览重试。');
      ++documentEpoch.current; ++requestGeneration.current; ++libraryGeneration.current;
      setDocument(null); setSelectedId(null); setDocuments([]); setVocabulary([]); setEvents([]);
      textImportRequest.current = null; setTextDraft({ title: '', text: '' }); setPdfDraft(null); setPdfMode('auto'); setPdfProcessing(false);
      urlImportRequest.current = null; setUrlDraft('');
      ++proofreadSyncGeneration.current; proofreadUncertain.current = null; setProofreadSync(null);
      setUploadOpen(false); setImportSyncError(''); setDocumentSyncError(''); setNotice(''); setQuery(''); setFilter('ALL');
      const warning = await clearRestoredBrowserState();
      return { ...result, ...(warning ? { cleanup_warning: warning } : {}) };
    } finally { savingRef.current = false; setSaving(false); busyRef.current = false; setBusy(''); }
  }, [health?.auth_mode, clearRestoredBrowserState]);
  const selected = document?.words.find(word => word.id === selectedId) ?? null;
  const uniqueWords = useMemo(() => {
    const seen = new Set<string>();
    return (document?.words ?? []).filter(word => { if (seen.has(word.lemma)) return false; seen.add(word.lemma); return true; });
  }, [document]);
  const filteredWords = useMemo(() => uniqueWords.filter(word => (word.word.toLowerCase().includes(query.toLowerCase()) || word.lemma.includes(query.toLowerCase())) && (filter === 'ALL' || (filter === 'UNMARKED' ? word.status === null : word.status === filter))), [uniqueWords, query, filter]);
  const nextWord = useCallback(() => {
    if (proofreadLock.current || readerBlockedRef.current || !filteredWords.length) return;
    const index = filteredWords.findIndex(word => word.id === selectedId || word.lemma === selected?.lemma);
    setCitationFocus(null); setSelectedId(filteredWords[(index + 1) % filteredWords.length].id);
  }, [filteredWords, selectedId, selected?.lemma]);
  const mark = useCallback(async (word: string, status: WordStatus, occurrence?: Occurrence) => {
    if (restorationLock.current || proofreadLock.current || readerBlockedRef.current || savingRef.current || busyRef.current) return;
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
  const assessReview = useCallback(async (item: ReviewItem, status: WordStatus, requestId: string): Promise<void> => {
    if (restorationLock.current || proofreadLock.current || readerBlockedRef.current || savingRef.current || busyRef.current) throw new Error('正在保存其他学习记录，请稍后重试。');
    savingRef.current = true; setSaving(true); setError('');
    try {
      const updated = await request<Vocabulary>(`/vocabulary/${encodeURIComponent(item.vocabulary.word)}`, {
        method: 'PUT', body: JSON.stringify({ status, origin: 'review', request_id: requestId, ...(item.occurrence ? { document_id: item.occurrence.document_id } : {}) }),
      });
      ++libraryGeneration.current;
      setVocabulary(values => [updated, ...values.filter(word => word.id !== updated.id)]);
      setDocument(value => value ? { ...value, words: value.words.map(word => word.lemma === updated.lemma ? { ...word, status: updated.status } : word) } : null);
      setNotice(`${item.vocabulary.word} · 本次自评已保存`);
      // The confirmed write advances the card even if the following read fails.
      void refreshLearning().catch(e => setError(`自评已保存；暂未刷新词库与学习足迹：${errorMessage(e)}`));
    }
    finally { savingRef.current = false; setSaving(false); }
  }, [refreshLearning]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (view !== 'reader' || !selected || busy || uploadOpen || proofreadLock.current || readerBlockedRef.current || window.document.querySelector('[role="dialog"][aria-modal="true"]') || event.defaultPrevented || event.repeat || event.ctrlKey || event.metaKey || event.altKey) return;
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
    <div className="upload-icon"><Upload size={25} /></div><h3>把一篇英文，带进你的阅读空间。</h3><p>拖入英文 PDF、试卷或文章截图<br /><span>PDF / JPG / PNG · 最大 10 MB</span></p><div className="import-entry-actions"><button className="button primary" disabled={isLoading || saving} onClick={() => fileInput.current?.click()}><Plus size={17} />选择图片</button><button className="button secondary" disabled={isLoading || saving} onClick={event => { openUpload(event.currentTarget, 'text'); setError(''); }}><FileText size={16} />粘贴英文文章</button><button className="button secondary" disabled={isLoading || saving} onClick={event => { openUpload(event.currentTarget, 'pdf'); setError(''); }}><FileText size={16} />导入 PDF 文档</button><button className="button secondary" disabled={isLoading || saving} onClick={event => { openUpload(event.currentTarget, 'url'); setError(''); }}><Globe size={16} />导入网页链接</button></div><div className="demo-link"><span>先感受一下？</span><button className="text-button" disabled={isLoading || saving} onClick={() => void loadDemo()}>打开示例文章 <ArrowRight size={14} /></button></div>
  </div>;

  return <div className="app-shell" data-view={view}>
    <StudioEffects />
    <StudioMotion />
    <header className="topbar"><div className="brand-lockup"><a className="brand" href="#" onClick={e => { e.preventDefault(); if (!restorationLock.current) setView('reader'); }} aria-label="词间 Lexi 首页"><span className="brand-icon"><Leaf size={21} /></span><span className="brand-name">词间<span className="brand-en">lexi</span></span></a><p className="brand-tagline">在阅读里，慢慢积累。</p></div>
      <nav ref={navigationElement} aria-label="主要导航">{navigation.filter(item => item.value !== 'data' || health?.auth_mode === 'local').map((item, index) => <div className="nav-entry" key={item.value}>{(index === 0 || item.value === 'dashboard') && <p className="nav-section-label">{index === 0 ? '阅读与练习' : '积累与管理'}</p>}<button disabled={restorationLocked} className={view === item.value ? 'active' : ''} aria-label={item.label} title={item.label} aria-current={view === item.value ? 'page' : undefined} data-motion-burst="" onClick={() => setView(item.value)}><span data-motion-magnet="" aria-hidden="true"><item.icon size={18} /></span><span className="nav-label">{item.label}</span>{item.value === 'vocabulary' && vocabulary.filter(word => word.status !== null).length > 0 && <span className="nav-count">{vocabulary.filter(word => word.status !== null).length}</span>}</button></div>)}</nav>
      <div className="profile"><span className="avatar">L</span><div className="profile-copy"><strong>我的学习空间</strong><span><span className="local-dot" />{health?.auth_mode === 'guest' ? '学习记录已连接' : '学习记录保存在本机'}</span></div></div>
    </header>
    <main>
      <div className="page-heading studio-heading"><div key={view} className="page-heading-copy studio-reveal"><p className="overline"><span className="heading-spark" aria-hidden="true"><Sparkles size={12} /></span>{title.eyebrow}</p><h1 ref={pageHeading} tabIndex={-1}><MotionTitle text={title.title} /></h1><p className="page-subtitle">{title.subtitle}</p></div><div className="heading-actions"><InspirationCorner disabled={initializing || isLoading || saving || restorationLocked || proofread !== null || uploadOpen || readerBlocked} />{(view !== 'reader' || document) && <ReadingGarden compact />}<button className="button primary new-reading studio-shimmer" disabled={isLoading || saving || restorationLocked} data-motion-burst="" onClick={event => { setView('reader'); setDragOver(false); openUpload(event.currentTarget); }}><span data-motion-magnet="" aria-hidden="true"><Plus size={17} /></span>添加阅读材料</button></div></div>
      {health?.auth_mode === 'guest' && <p className="page-subtitle">学习记录保存在服务器，访问凭证保存在当前浏览器。清除浏览器数据会失去访问权限；暂不支持跨设备登录。</p>}
      <input ref={fileInput} type="file" accept="image/png,image/jpeg,.jpg,.jpeg,.png" className="visually-hidden" aria-label="上传英文图片" onChange={event => { void upload(event.target.files?.[0]); event.target.value = ''; }} />
      {error && <div className="error-banner" role="alert"><AlertCircle size={19} /><span>{error}</span>{!document && <button className="text-button" onClick={() => void initialize()}><RefreshCw size={14} />重试连接</button>}<button className="icon-button" aria-label="关闭错误提示" onClick={() => setError('')}><X size={17} /></button></div>}
      {importSyncError && <div className="error-banner import-sync-warning" role="status"><AlertCircle size={19} /><span>{importSyncError}</span><button className="text-button" disabled={syncingImport} onClick={() => void refreshImportedMaterial()}><RefreshCw size={14} className={syncingImport ? 'spin' : ''} />重新刷新列表与词库</button></div>}
      {documentSyncError && <div className="error-banner" role="status"><AlertCircle size={19} /><span>{documentSyncError}</span><button className="text-button" disabled={syncingDocuments || saving || isLoading} onClick={() => void refreshDocumentList()}><RefreshCw size={14} className={syncingDocuments ? 'spin' : ''} />重新刷新阅读列表</button></div>}
      {proofreadSync && <div className="error-banner proofread-sync-warning" role="alert"><AlertCircle size={19} /><span>{proofreadSync.error || '正在重新核对正文的最新版本…'}</span><button className="text-button" disabled={saving || isLoading || proofread !== null} onClick={() => void reconcileProofread(proofreadSync.documentId)}><RefreshCw size={14} className={isLoading ? 'spin' : ''} />重新核对正文</button></div>}
      {isLoading && <div className="processing-banner" role="status"><LoaderCircle size={19} className="spin" /><span>{busy || '正在加载你的阅读空间…'}{busy.includes('识别') && <small>首次 OCR 运行需要加载模型，请保持页面打开。</small>}</span></div>}
      <ReviewView active={view === 'review'} readerActive={view === 'reader'} vocabulary={vocabulary} disabled={saving || isLoading || restorationLocked || proofread !== null || readerBlocked} onAssess={assessReview} onStart={() => setView('review')} onOpenDocument={(documentId, occurrenceId) => void openDocument(documentId, occurrenceId)} />
      {!restorationLocked && <PracticeGenerationView active={view === 'practice'} disabled={saving || isLoading || proofread !== null || readerBlocked} onSave={savePractice} onOpenDocument={id => void openDocument(id)} onGoReader={() => setView('reader')} />}
      {view === 'reader' && <>
        {document ? <>
          <StudioShelf articleCount={documents.length} vocabulary={vocabulary} disabled={isLoading || saving || restorationLocked || proofread !== null} onNavigate={setView} />
          <section className="reading-bar studio-spotlight"><div className="document-selector"><span className="document-file-icon"><FileText size={19} /></span><label><span className="overline">CURRENT READING</span><div className="select-wrap"><select aria-label="切换阅读材料" value={document.id} disabled={isLoading || saving} onChange={event => void openDocument(Number(event.target.value))}>{documents.length ? documents.map(item => <option key={item.id} value={item.id}>{item.title}</option>) : <option value={document.id}>{document.title}</option>}</select><ChevronDown size={14} /></div></label></div><div className="reading-progress"><div><span>词汇扫描进度</span><strong>{markedCount}<span> / {uniqueWords.length}</span></strong></div><div className="progress-track"><div style={{ width: `${progress}%` }} /></div></div><div className="legend"><span><i className="known" />已掌握</span><span><i className="unknown" />不认识</span><span><i className="uncertain" />不确定</span></div></section>
          <div className="reading-library-tools"><div className="article-tags">{document.tags?.map(tag => <span key={tag} className="article-tag">{tag}</span>)}<button className="text-button" onClick={() => setView('library')}><FolderOpen size={14} />整理文章库</button></div><div className="reading-bookmarks"><span><Bookmark size={14} />{document.bookmark_occurrence_id !== null ? `书签：${document.words.find(word => word.id === document.bookmark_occurrence_id)?.word ?? '已保存位置'}` : '还没有书签'}</span><button className="text-button" aria-label="保存当前阅读位置" disabled={isLoading || saving || readerBlocked || selectedId === null} onClick={() => void changeArticle(document.id, 'update', { bookmark_occurrence_id: selectedId }).catch(e => setError(errorMessage(e)))}>保存当前位置</button>{document.bookmark_occurrence_id !== null && <><button className="text-button" aria-label="回到书签" disabled={isLoading || saving || readerBlocked} onClick={() => { setSelectedId(document.bookmark_occurrence_id); setQuery(''); setFilter('ALL'); setRevealBookmark(value => value + 1); }}>回到书签</button><button className="icon-button" aria-label="清除阅读书签" disabled={isLoading || saving || readerBlocked} onClick={() => void changeArticle(document.id, 'update', { bookmark_occurrence_id: null }).catch(e => setError(errorMessage(e)))}><X size={14} /></button></>}</div></div>
          <div className="reading-workspace"><DocumentPanel key={`document-${document.id}`} document={document} selectedId={selectedId} revealToken={revealBookmark} citationFocus={citationFocus} disabled={saving || isLoading || readerBlocked || proofread !== null} onProofread={openProofread} onSelect={word => { if (proofreadLock.current || readerBlockedRef.current) return; setCitationFocus(null); setSelectedId(word?.id ?? null); }} /><WordList wholeDocument={document.source === 'pdf'} words={filteredWords} selectedId={uniqueWords.find(word => word.lemma === selected?.lemma)?.id ?? null} query={query} setQuery={setQuery} filter={filter} setFilter={setFilter} total={uniqueWords.length} onSelect={word => { if (proofreadLock.current || readerBlockedRef.current) return; setCitationFocus(null); setSelectedId(word.id); }} onMark={(word, status) => void mark(word.word, status, word)} saving={saving || isLoading || readerBlocked || proofread !== null} /><ContextPanel key={`context-${selectedId}`} word={selected} onMark={status => selected && void mark(selected.word, status, selected)} onNext={nextWord} saving={saving || isLoading || readerBlocked || proofread !== null} vocabulary={vocabulary} onLearningChanged={refreshLearning} /></div>
          {!isLoading && !restorationLocked && <ArticleQuestionPanel key={`article-question-${document.id}-${document.ocr_text}-${articleTextRevision}`} document={document} disabled={saving || readerBlocked || proofread !== null} onClear={() => setCitationFocus(null)} onLocate={citation => { if (!proofreadLock.current && !readerBlockedRef.current) setCitationFocus({ text: citation.quote, start: citation.text_start, end: citation.text_end, token: ++citationToken.current }); }} />}
        </> : !initializing && <StudioLanding upload={uploadZone} disabled={isLoading || saving || restorationLocked} onNavigate={setView} />}
      </>}
      {view === 'vocabulary' && <VocabularyView vocabulary={vocabulary} saving={saving} onMark={(word, status) => void mark(word, status)} />}
      {view === 'library' && <DocumentLibraryView documents={documents} revision={documentRevision} disabled={saving || isLoading} onOpen={id => void openDocument(id)} onUpdate={(id, patch) => changeArticle(id, 'update', patch)} onDelete={id => changeArticle(id, 'delete')} onRestore={id => changeArticle(id, 'restore')} />}
      {view === 'data' && health?.auth_mode === 'local' && <DataManagementView disabled={saving || isLoading} onRestore={restoreBackup} onClearBrowserState={clearRestoredBrowserState} />}
      {view === 'history' && <HistoryView events={events} />}
      {view === 'dashboard' && <DashboardView vocabulary={vocabulary} />}
      {view === 'relations' && <RelationsView vocabulary={vocabulary} />}
      <footer className="app-footer"><span><Leaf size={13} />词间 · 在阅读中生长</span><span>{health ? `${health.llm_provider === 'offline' ? '离线学习模式' : 'AI 语境服务已配置'} · ${health.auth_mode === 'guest' ? '学习记录保存在服务器' : '学习记录保存在本机'}` : '正在连接学习空间'}</span></footer>
    </main>
    {notice && <div className="toast" role="status"><Check size={17} />{notice}</div>}
    {uploadOpen && <div className="modal-backdrop" onClick={() => !isLoading && setUploadOpen(false)}><section ref={uploadModal} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="upload-title" className="upload-modal panel" onClick={event => event.stopPropagation()}><div className="panel-heading"><div>{importMode === 'image' ? <FileImage size={17} /> : importMode === 'url' ? <Globe size={17} /> : <FileText size={17} />}<h2 id="upload-title">新的阅读，从这里开始</h2></div><button className="icon-button" disabled={isLoading} onClick={() => setUploadOpen(false)} aria-label="关闭导入窗口"><X size={20} /></button></div><div className="import-mode segmented" role="group" aria-label="导入方式"><button className={importMode === 'image' ? 'active' : ''} aria-pressed={importMode === 'image'} disabled={isLoading} onClick={() => { setImportMode('image'); setError(''); }}><FileImage size={15} />上传图片</button><button className={importMode === 'text' ? 'active' : ''} aria-pressed={importMode === 'text'} disabled={isLoading} onClick={() => { setImportMode('text'); setError(''); }}><FileText size={15} />粘贴文章</button><button className={importMode === 'pdf' ? 'active' : ''} aria-pressed={importMode === 'pdf'} disabled={isLoading} onClick={() => { setImportMode('pdf'); setError(''); }}><FileText size={15} />上传 PDF</button><button className={importMode === 'url' ? 'active' : ''} aria-pressed={importMode === 'url'} disabled={isLoading} onClick={() => { setImportMode('url'); setError(''); }}><Globe size={15} />网页链接</button></div>{importMode === 'image' ? uploadZone : importMode === 'pdf' ? <PdfImportForm file={pdfDraft?.file ?? null} mode={pdfMode} onModeChange={changePdfMode} processing={pdfProcessing} onChoose={choosePdf} onSubmit={() => void importPdf()} disabled={isLoading || saving} /> : importMode === 'url' ? <UrlImportForm url={urlDraft} onChange={changeUrlDraft} onSubmit={() => void importUrl()} disabled={isLoading || saving} /> : <TextImportForm draft={textDraft} onChange={changeTextDraft} onSubmit={() => void importText()} disabled={isLoading || saving} />}{error && <p className="modal-error" role="alert">{error}</p>}{isLoading && <div className="loading-copy modal-loading"><LoaderCircle className="spin" size={16} />{busy}</div>}</section></div>}
    {proofread && <PdfProofreadWorkbench key={proofread.documentId} {...proofread} onClose={() => { proofreadLock.current = false; setProofread(null); }} onSaved={proofreadSaved} onUncertainClose={closeUncertainProofread} />}
  </div>;
}
