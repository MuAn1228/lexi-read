import type { MascotRect, MascotViewport } from './mascotPosition';

export const ASSISTANT_MESSAGE_LIMIT = 500;
export const ASSISTANT_TURN_LIMIT = 12;
export type AssistantMode = 'chat' | 'explain' | 'memory' | 'quiz';
export interface AssistantReadingContext {
  key: string; documentId: number | null; occurrenceId: number | null;
  word: string | null; title: string | null; sentence: string | null;
}
export interface AssistantQuiz {
  question: string; options: string[]; answer_index: number; explanation: string;
}
export interface AssistantResponse {
  source: 'llm' | 'unavailable'; answer: string | null; quiz: AssistantQuiz | null;
  context_limited: boolean; warning: string | null;
}
export interface AssistantHistory { question: string; answer: string }
export interface AssistantTurn {
  id: number; question: string; mode: AssistantMode; response: AssistantResponse;
  quizChoice?: number;
}

function validText(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && Boolean(value.trim()) && Array.from(value).length <= maximum
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);
}

export function assistantMessageProblem(message: string): string {
  const text = message.trim();
  if (!text) return '先告诉我你想了解什么。';
  if (Array.from(text).length > ASSISTANT_MESSAGE_LIMIT) return '每次最多输入 500 字，请精简后发送。';
  if (!validText(text, ASSISTANT_MESSAGE_LIMIT)) return '问题含有无法处理的字符，请修改后发送。';
  return '';
}

/** Reject malformed replies rather than displaying incomplete questions or invented success. */
export function assistantResponse(value: unknown, mode: AssistantMode): AssistantResponse | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const item = value as Record<string, unknown>;
  if ((item.source !== 'llm' && item.source !== 'unavailable') || typeof item.context_limited !== 'boolean'
    || !(item.warning === null || validText(item.warning, 1000))) return null;
  if (item.source === 'unavailable') {
    return item.answer === null && item.quiz === null ? item as unknown as AssistantResponse : null;
  }
  if (!validText(item.answer, 3000)) return null;
  if (mode !== 'quiz') return item.quiz === null ? item as unknown as AssistantResponse : null;
  if (!item.quiz || typeof item.quiz !== 'object' || Array.isArray(item.quiz)) return null;
  const quiz = item.quiz as Record<string, unknown>;
  if (!validText(quiz.question, 500) || !Array.isArray(quiz.options) || quiz.options.length !== 4
    || !quiz.options.every(option => validText(option, 300))
    || new Set(quiz.options.map(option => String(option).trim().toLocaleLowerCase())).size !== 4
    || !Number.isInteger(quiz.answer_index) || Number(quiz.answer_index) < 0 || Number(quiz.answer_index) > 3
    || !validText(quiz.explanation, 800)) return null;
  return item as unknown as AssistantResponse;
}

/** A hidden quiz answer stays out of the conversation until the learner reveals it. */
export function assistantHistory(turns: readonly AssistantTurn[]): AssistantHistory[] {
  return turns.filter(turn => turn.response.source === 'llm' && validText(turn.response.answer, 3000))
    .slice(-3).map(turn => {
      const quiz = turn.response.quiz;
      const parts = [turn.response.answer!];
      if (quiz) {
        parts.push(`练习题：${quiz.question}`, ...quiz.options.map((option, index) => `${'ABCD'[index]}. ${option}`));
        if (Number.isInteger(turn.quizChoice) && turn.quizChoice! >= 0 && turn.quizChoice! <= 3) {
          parts.push(`用户选择：${'ABCD'[turn.quizChoice!]}`, `正确答案：${'ABCD'[quiz.answer_index]}，${quiz.options[quiz.answer_index]}`, `解析：${quiz.explanation}`);
        }
      }
      return { question: turn.question, answer: Array.from(parts.join('\n')).slice(0, 3000).join('') };
    });
}

export function assistantPayload(context: AssistantReadingContext, message: string, mode: AssistantMode, history: readonly AssistantHistory[]) {
  const problem = assistantMessageProblem(message);
  if (problem) throw new Error(problem);
  const documentId = Number.isSafeInteger(context.documentId) && context.documentId! > 0 ? context.documentId : null;
  const occurrenceId = documentId && Number.isSafeInteger(context.occurrenceId) && context.occurrenceId! > 0 ? context.occurrenceId : null;
  const word = context.word?.trim() || null;
  if (word && !validText(word, 150)) throw new Error('当前词条过长或无效，请换一个词再提问。');
  return { message: message.trim(), mode, document_id: documentId, occurrence_id: occurrenceId, word,
    history: history.filter(turn => validText(turn.question, 500) && validText(turn.answer, 3000)).slice(-3).map(turn => ({ ...turn })) };
}

export function assistantQuickQuestion(mode: Exclude<AssistantMode, 'chat'>, context: AssistantReadingContext): string {
  if (mode === 'explain') return context.word ? `请结合原句讲解 ${context.word} 的意思和用法。`
    : context.documentId ? '请讲解这篇文章的主要意思，并指出一个值得学习的表达。' : '请讲解一个常用英语表达，给出中文释义和例句。';
  if (mode === 'memory') return context.word ? `请帮我记住 ${context.word}，给出可靠的联想方法、用法和一个短例句。`
    : context.documentId ? '请从当前文章挑一个值得记忆的词，教我如何记住它。' : '请教我记住一个常用英语单词，并给出短例句。';
  return context.word ? `请围绕 ${context.word} 出一道四选一练习题。`
    : context.documentId ? '请根据当前文章出一道四选一英语练习题。' : '请出一道适合日常学习的四选一英语练习题。';
}

/** The panel can move to fit the viewport; the mascot remains at its saved dock. */
export function assistantPanelPlacement(anchor: MascotRect, viewport: MascotViewport): MascotRect {
  const inset = 12, gap = 12;
  const width = Math.max(1, Math.min(360, viewport.width - inset * 2));
  const availableHeight = Math.max(1, viewport.height - inset * 2);
  let height = Math.min(640, availableHeight);
  const inwardX = anchor.x + anchor.width / 2 >= viewport.x + viewport.width / 2 ? anchor.x - width - gap : anchor.x + anchor.width + gap;
  const minX = viewport.x + inset, maxX = viewport.x + viewport.width - width - inset;
  const sideFits = inwardX >= minX && inwardX <= maxX;
  let y = Math.max(viewport.y + inset, Math.min(anchor.y + anchor.height - height, viewport.y + viewport.height - height - inset));
  if (!sideFits) {
    const above = anchor.y - viewport.y - inset - gap;
    const below = viewport.y + viewport.height - inset - anchor.y - anchor.height - gap;
    if (Math.max(above, below) >= 240) {
      const useAbove = above >= below;
      height = Math.min(height, useAbove ? above : below);
      y = useAbove ? anchor.y - gap - height : anchor.y + anchor.height + gap;
    }
  }
  return { x: Math.max(minX, Math.min(inwardX, maxX)), y, width, height };
}
