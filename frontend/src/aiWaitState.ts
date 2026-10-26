import type { AIStreamPhase } from './aiStream';

export function aiWaitPhaseLabel(phase?: AIStreamPhase): string {
  if (!phase) return '正在等待模型响应';
  return {
    connecting: '正在连接 AI 服务',
    generating: '正在生成内容',
    validating: '正在核对内容',
    fallback: '正在等待完整内容',
  }[phase];
}

export function aiWaitElapsedSeconds(startedAt: number, now: number): number {
  return Math.max(0, Math.floor((now - startedAt) / 1_000));
}

export function aiWaitIsSlow(elapsedSeconds: number): boolean {
  return elapsedSeconds >= 15;
}
