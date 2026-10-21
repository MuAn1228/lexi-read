import type { LearningEvent } from './types';

export const eventLabels: Record<string, string> = {
  MARK_KNOWN: '标记为已掌握', MARK_UNKNOWN: '标记为不认识', MARK_UNCERTAIN: '标记为不确定',
  LOOKUP: '查看了语境释义', VIEW_RELATED: '查看了关联词',
  IMPORT_MAIMEMO: '更新了墨墨来源记录', MAIMEMO_SYNC: '完成了墨墨词库导入',
  RECOMMENDATION_GENERATED: '生成了关联推荐', RECOMMENDATION_SHOWN: '看到了关联推荐',
  RECOMMENDATION_USEFUL: '认为关联有用', RECOMMENDATION_NOT_USEFUL: '认为关联没用',
};

export function learningEventLabel(event: Pick<LearningEvent, 'event_type' | 'details'>): string {
  const label = eventLabels[event.event_type] || event.event_type;
  return event.details.origin === 'review' && event.event_type.startsWith('MARK_') ? `复习时${label}` : label;
}
