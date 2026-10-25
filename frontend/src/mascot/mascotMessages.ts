export const mascotMessages = {
  idle: [
    '今天读到什么有意思的词了吗？',
    '遇到不确定的词，先留个标记也很好。',
    '从语境里记住的词，通常更牢。',
    '要不要继续看看下一处？',
    '慢慢读，重要的词会再次相遇。',
  ],
  selected: ['这个词放在句子里会更好理解。', '可以先看看它在这句话里的用法。'],
  known: ['这个已经记住了。', '记下了，继续往下读吧。'],
  unknown: ['记下来了，再遇到它时可以重点看看。', '先留下这个词，理解可以慢慢来。'],
  uncertain: ['先留个问号也没关系。', '记下这份不确定，下一次再确认。'],
  analyzing: ['让我结合这句话看看……', '我在看它和上下文的关系……'],
  analysisSuccess: ['语境释义准备好了，可以看看它在这里的用法。', '找到了，详细解释已经放在语境面板里。'],
  analysisOffline: ['已有的离线释义准备好了，可以结合原句看看。'],
  recommending: ['我看看还有哪些词值得一起认识……', '正在寻找可以一起学习的关联词……'],
  recommendationEmpty: ['这次没有特别合适的关联词，继续读也很好。'],
  recommendationOffline: ['已整理好 {count} 个离线关联词，可以挑一个看看。'],
  recommendationSuccess: ['找到了 {count} 个关联词，可以先从第一个看看。'],
  feedback: ['反馈记下了。', '已保存，继续按你的节奏学。'],
  error: ['这次请求没有完成，可以稍后再试，或者继续读。', '这次没能得到结果，我先陪你继续读。'],
  fallback: ['模型暂时没回应，先用已有的离线结果帮你。'],
  gestureNod: ['嗯，我在。', '收到，继续按你的节奏读。'],
  gestureTilt: ['换个角度，也许就明白了。', '让我换个角度看看。'],
  gestureHop: ['好，打起一点精神。', '稍微活动一下，接着读。'],
  gestureStretch: ['放松一下肩膀和眼睛吧。', '伸个懒腰，再慢慢往下读。'],
  gesturePet: ['收到这份鼓励了。', '谢谢，我会安静陪你读。'],
} as const;

export type MascotMessageGroup = keyof typeof mascotMessages;

/** Filter first, so even repeated random values cannot produce consecutive repeats. */
export function chooseMascotMessage(group: MascotMessageGroup, last: string | null, random: number, count = 0): string {
  const choices: string[] = mascotMessages[group].map(line => line.replace('{count}', String(count)));
  const alternatives = choices.filter(line => line !== last);
  const pool = alternatives.length ? alternatives : choices;
  const safeRandom = Number.isFinite(random) ? Math.min(0.999999, Math.max(0, random)) : 0;
  return pool[Math.floor(safeRandom * pool.length)];
}
