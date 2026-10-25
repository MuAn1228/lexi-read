import type { MascotScene } from './mascotTypes';

/** Short, authored lines: context is observed, learning results are never invented. */
export const mascotMessages = {
  idle: [
    '今天读到什么有意思的词了吗？',
    '遇到不确定的词，先留个标记也很好。',
    '从语境里记住的词，通常更牢。',
    '要不要继续看看下一处？',
    '慢慢读，重要的词会再次相遇。',
  ],
  reader: [
    '就从这一句开始，我陪你读。',
    '先读完这一句，再回头看生词也可以。',
    '这个词不急着记，先看它和谁一起出现。',
    '遇到长句，试着先找主语和动词。',
    '想听一句，还是想弄懂一句？都按你的节奏来。',
  ],
  vocabulary: [
    '词库不用一次整理完，先挑一小组就好。',
    '拿不准的词，可以先标成“不确定”。',
    '同一个词的几个意思，记得放回例句看看。',
    '找词时也可以试试搜中文释义。',
    '我守着词库，你来决定哪些需要多见几次。',
  ],
  review: [
    '先在心里想一想，再揭晓释义。',
    '想不起来也没关系，如实自评就好。',
    '这次模糊的地方，正好是值得再看的地方。',
    '例句太长的话，先抓住这个词的用法。',
    '我陪你慢慢翻卡，不用抢着选“已掌握”。',
  ],
  practice: [
    '短文里遇到熟词，留意它这次怎么用。',
    '先读一遍大意，再去看高亮的目标词。',
    '想换主题时，选一个你真的好奇的话题。',
    '练习时碰到新用法，可以留个标记。',
    '读完后试着用其中一个词说句话吧。',
  ],
  library: [
    '今天想打开哪一篇？我在书架旁等你。',
    '选一篇愿意读下去的，比一味追难度更重要。',
    '文章多了，可以用标签整理成自己的小书架。',
    '想重读时，试试找一篇以前标过生词的文章。',
  ],
  history: [
    '看看最近反复查过的词，也许能找到复习方向。',
    '记录可以帮你回看过程，不用拿它催自己。',
    '想回到那次阅读时，可以从真实原句接着看。',
    '翻翻以前的学习痕迹，再决定今天从哪里开始。',
  ],
  relations: [
    '相近的词，也要看看它们在哪些场合不一样。',
    '先认识一组联系，再试着回想其中一个词。',
    '派生词的意思相近，用法可不一定相同。',
    '搭配放进一句话里，会比单独排成一行更清楚。',
  ],
  data: [
    '整理学习资料时，也可以顺手留一份备份。',
    '导入词表之后，仍然可以按自己的掌握程度标注。',
    '我在这里等你，先把资料安顿好。',
  ],
  dashboard: [
    '先看看哪些词值得再见一次，再选今天的起点。',
    '数字是路标，具体怎么学还是由你决定。',
    '从一个小任务开始也很好，我把书拿好了。',
  ],
  selected: [
    '这个词放在句子里会更好理解。',
    '可以先看看它在这句话里的用法。',
    '看看前后的搭配，也许就能找到线索。',
    '先猜一下这里的意思，再查词对照。',
  ],
  known: [
    '收到，这个词已标为掌握。',
    '记下了，继续往下读吧。',
    '这次的标注收好了，下次遇见再确认。',
    '收到你的判断，我们接着看。',
  ],
  unknown: [
    '记下来了，再遇到它时可以重点看看。',
    '先留下这个词，理解可以慢慢来。',
    '生词已经标好，先看看它的例句吧。',
    '暂时不认识也没关系，让它多出现几次。',
  ],
  uncertain: [
    '先留个问号也没关系。',
    '记下这份不确定，下一次再确认。',
    '标记收好了，可以对照不同义项看看。',
    '不用勉强选“已掌握”，模糊的地方也值得记录。',
  ],
  analyzing: [
    '让我结合这句话看看……',
    '我在看它和上下文的关系……',
    '正在整理解释，你可以先看看原句。',
    '我先找找句子里的线索……',
  ],
  analysisSuccess: [
    '语境释义准备好了，可以看看它在这里的用法。',
    '找到了，详细解释已经放在语境面板里。',
    '解释准备好了，留意它的词性和搭配。',
    '这次的解释可以查看了，试着和原句对照一下。',
  ],
  analysisOffline: [
    '已有的离线释义准备好了，可以结合原句看看。',
    '离线释义已显示，具体用法还要对照原句。',
    '先用已有的离线释义看看这个词。',
  ],
  recommending: [
    '我看看还有哪些词值得一起认识……',
    '正在寻找可以一起学习的关联词……',
    '我去找找相近的意思和常见联系……',
    '正在整理词与词之间的线索……',
  ],
  recommendationEmpty: [
    '这次没有特别合适的关联词，继续读也很好。',
    '没有特别合适的关联词，我们先留在原句里吧。',
    '这次没有特别合适的新联系，可以换个词再看看。',
  ],
  recommendationOffline: [
    '已整理好 {count} 个离线关联词，可以挑一个看看。',
    '这里有 {count} 个离线关联词，先对照意思看看。',
    '离线资料里整理了 {count} 个关联词，按需要了解就好。',
  ],
  recommendationSuccess: [
    '找到了 {count} 个关联词，可以先从第一个看看。',
    '整理好 {count} 个关联词了，看看哪一个最有帮助。',
    '这里有 {count} 个关联词，可以对照它们的用法。',
  ],
  feedback: [
    '反馈记下了。',
    '已保存，继续按你的节奏学。',
    '收到这次反馈，接着看你感兴趣的内容吧。',
    '你的选择已经保存，我把小本子收好。',
  ],
  error: [
    '这次请求没有完成，可以稍后再试，或者继续读。',
    '这次没能得到结果，我先陪你继续读。',
    '这次请求没有完成；想重试时，我还在这里。',
  ],
  fallback: [
    '模型暂时没回应，先用已有的离线结果帮你。',
    '模型暂时没回应，已有的离线结果仍可查看。',
    '模型暂时没回应；我们先对照离线资料继续看。',
  ],
  gestureNod: ['嗯，我在。', '收到，继续按你的节奏读。', '小小地点个头，陪你接着看。'],
  gestureTilt: ['换个角度，也许就明白了。', '让我换个角度看看。', '这个问号，我先替你举着。'],
  gestureHop: ['好，打起一点精神。', '稍微活动一下，接着读。', '轻轻跳一下，把注意力带回来。'],
  gestureStretch: ['放松一下肩膀和眼睛吧。', '伸个懒腰，再慢慢往下读。', '书先放稳，我也舒展一下。'],
  gesturePet: ['收到这份鼓励了。', '谢谢，我会安静陪你读。', '这份温柔收到了，书也替你拿稳。'],
  gestureWave: ['嗨，Lexi 在这里。', '抬起头，给你一个小小的招呼。', '看到你啦，要从哪一句开始？'],
  gestureClap: ['给这份耐心一个小小的掌声。', '慢慢来，我替你打打气。', '认真对待一个词，也值得鼓励。'],
  gestureRead: ['我也翻翻书，陪你读这一页。', '翻过一页，再看看词语的新用法。', '书页轻轻翻，思路慢慢来。'],
  gestureYawn: ['有点困的话，先歇一会儿也可以。', '打个小哈欠，伸展一下再继续。', '我把书放稳，你也可以稍微放松。'],
  gestureThink: ['让我把这个问号多想一小会儿。', '先找线索，再下结论。', '想清楚一点，比急着猜更有趣。'],
} as const;

export type MascotMessageGroup = keyof typeof mascotMessages;

const sceneGroups: Record<MascotScene, MascotMessageGroup> = {
  reader: 'reader', library: 'library', vocabulary: 'vocabulary', review: 'review',
  practice: 'practice', history: 'history', dashboard: 'dashboard', relations: 'relations', data: 'data',
};

/** No scene means a legacy/general greeting; changing pages itself never speaks. */
export function mascotSceneMessageGroup(scene?: MascotScene): MascotMessageGroup {
  return scene ? sceneGroups[scene] ?? 'idle' : 'idle';
}

/** Filter first, so even repeated random values cannot produce consecutive repeats. */
export function chooseMascotMessage(group: MascotMessageGroup, last: string | null, random: number, count = 0, recent: readonly string[] = []): string {
  const safeCount = Number.isFinite(count) ? Math.max(0, Math.min(3, Math.floor(count))) : 0;
  const choices: string[] = mascotMessages[group].map(line => line.replaceAll('{count}', String(safeCount)));
  const alternatives = choices.filter(line => line !== last);
  const unseen = alternatives.filter(line => !recent.includes(line));
  const pool = unseen.length ? unseen : alternatives.length ? alternatives : choices;
  const safeRandom = Number.isFinite(random) ? Math.min(0.999999, Math.max(0, random)) : 0;
  return pool[Math.floor(safeRandom * pool.length)];
}
