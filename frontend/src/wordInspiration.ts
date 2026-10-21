import type { ReadingDocument, Vocabulary } from './types';

export type InspirationCard = {
  id: string;
  word: string;
  kind: string;
  meaning: string | null;
  sentence: string | null;
  sourceLabel: string;
  accent: string;
  glow: string;
  contextLabel?: string;
};

const tones = [
  { accent: '#d8eaa1', glow: '#b7dc76' },
  { accent: '#efc5a5', glow: '#d9a58e' },
  { accent: '#d5c8f0', glow: '#b5a1e5' },
  { accent: '#f0cfce', glow: '#d6a3b6' },
  { accent: '#eddd9d', glow: '#dbc76b' },
  { accent: '#bce0d0', glow: '#8fc7b3' },
] as const;

// Original editorial examples, available offline. These are never presented as
// explanations of a personal word's meaning in its reading context.
const editorialWords: readonly [string, string, string, string][] = [
  ['curiosity', 'noun', '好奇心 · 求知欲', 'Curiosity turns a quiet walk into a little adventure.'],
  ['explore', 'verb', '探索 · 探究', 'Explore a new path and see where it leads.'],
  ['wonder', 'noun', '惊奇 · 惊叹', 'The night sky fills us with wonder.'],
  ['bloom', 'verb', '开花 · 繁盛', 'With a little care, small ideas begin to bloom.'],
  ['spark', 'verb', '激发 · 引发', 'One good question can spark a new idea.'],
  ['serendipity', 'noun', '意外的幸运发现', 'Finding this little bookshop was pure serendipity.'],
  ['glimpse', 'noun', '一瞥 · 短暂的看见', 'Through the window, I caught a glimpse of the sea.'],
  ['linger', 'verb', '逗留 · 迟迟不离开', 'We lingered in the garden after the rain stopped.'],
  ['resilient', 'adjective', '有韧性的 · 能恢复的', 'The resilient tree grew new leaves after the storm.'],
  ['nurture', 'verb', '培养 · 悉心照料', 'A few minutes of practice each day can nurture a new skill.'],
  ['wander', 'verb', '漫步 · 闲逛', 'We wandered through the old town without a map.'],
  ['vivid', 'adjective', '鲜明的 · 生动的', 'Her vivid description made the distant island feel close.'],
  ['whisper', 'verb', '低声说 · 耳语', 'Please whisper while the baby is asleep.'],
  ['tranquil', 'adjective', '宁静的 · 平和的', 'The lake was tranquil in the early morning.'],
  ['flourish', 'verb', '茁壮成长 · 蓬勃发展', 'These plants flourish in a sunny room.'],
  ['embrace', 'verb', '欣然接受 · 接纳', 'She decided to embrace the chance to learn something new.'],
  ['horizon', 'noun', '地平线', 'A small boat appeared on the horizon.'],
  ['kindle', 'verb', '激起 · 点燃', 'That first science lesson kindled his interest in the stars.'],
  ['delight', 'noun', '喜悦 · 乐趣', 'To her delight, the seeds had begun to grow.'],
  ['thoughtful', 'adjective', '体贴的 · 考虑周到的', 'It was thoughtful of you to save me a seat.'],
  ['rhythm', 'noun', '节奏 · 韵律', 'The gentle rhythm of the rain helped me relax.'],
  ['unfold', 'verb', '逐渐展开 · 逐步呈现', 'The story unfolds over one long summer.'],
  ['cherish', 'verb', '珍爱 · 珍视', 'I cherish the letters my grandmother wrote to me.'],
  ['meadow', 'noun', '草地 · 牧场', 'Wildflowers covered the meadow beside the river.'],
  ['reflect', 'verb', '认真思考 · 反思', 'Take a moment to reflect on what you learned today.'],
  ['thrive', 'verb', '茁壮成长 · 兴旺', 'Children thrive when they feel safe and supported.'],
  ['gentle', 'adjective', '轻柔的 · 温和的', 'A gentle breeze moved the curtains.'],
  ['insight', 'noun', '洞见 · 深刻的理解', 'The conversation gave me a new insight into her work.'],
  ['pause', 'noun', '暂停 · 停顿', 'After a short pause, he began to tell the story.'],
  ['gratitude', 'noun', '感激 · 感恩', 'She wrote a letter to express her gratitude.'],
  ['courage', 'noun', '勇气', 'It took courage to ask the first question.'],
  ['luminous', 'adjective', '发光的 · 明亮的', 'A luminous moon hung above the quiet field.'],
  ['balance', 'noun', '平衡 · 均衡', 'She is finding a better balance between work and rest.'],
  ['observe', 'verb', '观察 · 留意', 'We stopped to observe a bird building its nest.'],
  ['sincere', 'adjective', '真诚的 · 诚恳的', 'He offered a sincere apology for being late.'],
  ['discover', 'verb', '发现 · 发觉', 'You may discover a new favorite author at the library.'],
  ['perspective', 'noun', '视角 · 看待事物的方式', 'Travel can give you a fresh perspective on everyday life.'],
  ['restore', 'verb', '恢复 · 使复原', 'A quiet afternoon helped restore her energy.'],
  ['subtle', 'adjective', '细微的 · 不易察觉的', 'There was a subtle change in the color of the sky.'],
  ['inspire', 'verb', '激励 · 启发', 'Her patient teaching inspired me to keep learning.'],
  ['gather', 'verb', '聚集 · 集合', 'Friends gathered around the table to share a meal.'],
  ['possibility', 'noun', '可能性 · 可能发生的事', 'Every blank page holds the possibility of a new story.'],
];

export const curatedCards: readonly InspirationCard[] = Object.freeze(editorialWords.map(([word, kind, meaning, sentence], index) => Object.freeze({
  id: `curated:${word}`,
  word,
  kind,
  meaning,
  sentence,
  sourceLabel: '离线精选',
  contextLabel: '原创示例句',
  ...tones[index % tones.length],
})));

function normalized(value: unknown): string {
  return typeof value === 'string' ? value.trim().toLowerCase() : '';
}

function timestamp(value: unknown): number {
  const time = typeof value === 'string' ? Date.parse(value) : NaN;
  return Number.isFinite(time) ? time : -Infinity;
}

function personalTone(key: string) {
  let hash = 0;
  for (const letter of key) hash = (hash * 31 + letter.codePointAt(0)!) >>> 0;
  return tones[hash % tones.length];
}

/** A read-only view of actual vocabulary, not a generated dictionary entry. */
export function personalInspirationCards(vocabulary: Vocabulary[], document: ReadingDocument | null): InspirationCard[] {
  const byLemma = new Map<string, Vocabulary>();
  for (const entry of Array.isArray(vocabulary) ? vocabulary : []) {
    if (!entry || typeof entry.word !== 'string' || !entry.word.trim()) continue;
    const key = normalized(entry.lemma) || normalized(entry.word);
    const previous = byLemma.get(key);
    // A stale duplicate must not replace a more recently saved status.
    if (!previous || timestamp(entry.updated_at) > timestamp(previous.updated_at)) byLemma.set(key, entry);
  }

  const entries = [...byLemma.entries()];
  const unfamiliar = entries.filter(([, entry]) => entry.status === 'UNKNOWN' || entry.status === 'UNCERTAIN');
  const preferred = unfamiliar.length ? unfamiliar : entries;
  const activeDocument = document && !document.deleted_at ? document : null;
  const occurrences = new Map<string, string>();
  for (const occurrence of Array.isArray(activeDocument?.words) ? activeDocument.words : []) {
    if (!occurrence || typeof occurrence.sentence !== 'string' || !occurrence.sentence.trim()) continue;
    const key = normalized(occurrence.lemma) || normalized(occurrence.word);
    if (key && !occurrences.has(key)) occurrences.set(key, occurrence.sentence);
  }

  return preferred.map(([key, entry]) => {
    const sentence = occurrences.get(key) ?? null;
    const title = typeof activeDocument?.title === 'string' ? activeDocument.title.trim() : '';
    return {
      id: `personal:${key}`,
      word: entry.word.trim(),
      kind: entry.status === 'KNOWN' ? '已掌握' : entry.status === 'UNKNOWN' ? '不认识' : entry.status === 'UNCERTAIN' ? '不确定' : '未标记',
      meaning: null,
      sentence,
      sourceLabel: '我的词库',
      contextLabel: sentence ? (title ? `原句 · ${title}` : '当前文章原句') : '当前文章中暂无原句',
      ...personalTone(key),
    };
  });
}

function validCard(entry: InspirationCard): boolean {
  return Boolean(entry && typeof entry.id === 'string' && entry.id.trim()
    && typeof entry.word === 'string' && entry.word.trim()
    && typeof entry.kind === 'string' && typeof entry.sourceLabel === 'string'
    && (entry.meaning === null || typeof entry.meaning === 'string')
    && (entry.sentence === null || typeof entry.sentence === 'string')
    && typeof entry.accent === 'string' && typeof entry.glow === 'string');
}

function shuffled(cards: InspirationCard[], random: () => number): InspirationCard[] {
  const result = [...cards];
  for (let index = result.length - 1; index > 0; index--) {
    let draw = 0;
    try { draw = typeof random === 'function' ? random() : 0; } catch { /* Invalid random sources use a deterministic safe draw. */ }
    const unit = Number.isFinite(draw) ? Math.max(0, Math.min(draw, 1 - Number.EPSILON)) : 0;
    const other = Math.floor(unit * (index + 1));
    [result[index], result[other]] = [result[other], result[index]];
  }
  return result;
}

/** Prefer unseen cards; when the pool is small, reuse only the real remainder. */
export function pickInspirationBatch(pool: readonly InspirationCard[], previousIds: readonly string[] = [], limit = 6, random: () => number = Math.random): InspirationCard[] {
  if (!Array.isArray(pool) || typeof limit !== 'number' || !Number.isFinite(limit) || limit < 1) return [];
  const unique = new Map<string, InspirationCard>();
  for (const card of pool) if (validCard(card) && !unique.has(card.id)) unique.set(card.id, card);
  const count = Math.min(Math.floor(limit), unique.size);
  if (!count) return [];
  const previous = Array.isArray(previousIds) ? previousIds.filter(id => typeof id === 'string') : [];
  const previousSet = new Set(previous);
  const fresh: InspirationCard[] = [];
  const repeated: InspirationCard[] = [];
  for (const card of unique.values()) (previousSet.has(card.id) ? repeated : fresh).push(card);
  const batch = [...shuffled(fresh, random), ...shuffled(repeated, random)].slice(0, count);
  // Even a constant or broken random source should not make “换一组” inert.
  if (batch.length > 1 && batch.length === previous.length && batch.every((card, index) => card.id === previous[index])) {
    batch.push(batch.shift()!);
  }
  return batch;
}
