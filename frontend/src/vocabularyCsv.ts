import type { Vocabulary } from './types';

function cell(value: string | number | null): string {
  let text = value === null ? '' : String(value);
  // Quote CSV syntax, and keep imported spreadsheet text from becoming formulas.
  if (/^\s*[=+\-@]/.test(text) || /^[\t\r\n]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

export function vocabularyCsv(words: Vocabulary[]): string {
  const headers = ['单词', '词元', '掌握状态', '规则掌握度（0–1）', '阅读遇见次数', '累计查询次数',
    '首次遇见（UTC）', '最近遇见（UTC）'];
  const statuses = { KNOWN: '已掌握', UNKNOWN: '不认识', UNCERTAIN: '不确定' };
  const rows = words.map(word => [word.word, word.lemma, word.status ? statuses[word.status] : '未标记',
    word.mastery_score, word.seen_count, word.lookup_count, word.first_seen_at, word.last_seen_at]);
  // BOM makes Chinese headers readable in common Windows spreadsheet programs.
  return '\uFEFF' + [headers, ...rows].map(row => row.map(cell).join(',')).join('\r\n') + '\r\n';
}

export function downloadVocabulary(words: Vocabulary[]): void {
  const data = new Blob([vocabularyCsv(words)], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(data);
  const anchor = document.createElement('a');
  const now = new Date();
  const date = [now.getFullYear(), String(now.getMonth() + 1).padStart(2, '0'), String(now.getDate()).padStart(2, '0')].join('-');
  anchor.href = url;
  anchor.download = `lexi-vocabulary-${date}.csv`;
  try {
    document.body.append(anchor);
    anchor.click();
  } finally {
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
