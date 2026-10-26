import { ArrowRight, CalendarClock, CheckCircle2, Clock3, Gauge, Leaf, Sparkles } from 'lucide-react';
import { STUDY_PLAN_MINUTES } from './studyPlan';
import type { StudyPlan, StudyPlanMinutes } from './types';
import './studyPlan.css';

const statusCopy = (plan: StudyPlan) => {
  if (plan.status === 'COMPLETE') return ['今天的计划已完成', plan.due_now > 0 ? `今天仍有 ${plan.due_now} 个到期词；想继续时可以增加学习预算。` : '可以安心去阅读，让新单词在语境里出现。'];
  if (plan.status === 'WAITING') return ['现在没有到期任务', plan.later_today > 0 ? `今天稍后还有 ${plan.later_today} 个词会到期。` : '继续阅读或标记不熟悉的词，计划会自动更新。'];
  if (plan.status === 'LIGHT') return ['今天是一组轻量计划', '只安排真正需要巩固的词，不会为了凑时长提前复习。'];
  return ['今天的学习计划已准备好', '先处理最需要巩固的词，完成后再继续阅读。'];
};

export default function StudyPlanOverview({ plan, targetMinutes, compact = false, disabled, lockMinutes = false, storageWarning = '', onMinutesChange, onStart }: {
  plan: StudyPlan; targetMinutes: StudyPlanMinutes; compact?: boolean; disabled: boolean; lockMinutes?: boolean;
  storageWarning?: string; onMinutesChange: (value: StudyPlanMinutes) => void; onStart?: () => void;
}) {
  const [title, description] = statusCopy(plan);
  const goalValue = Math.min(plan.target_cards, plan.completed_cards);
  const startLabel = plan.available_now > 0
    ? (plan.completed_cards > 0 ? '继续今日计划' : '开始今日计划')
    : plan.status === 'COMPLETE' ? '查看完成情况' : '查看学习安排';
  return <section className={`panel study-plan-overview${compact ? ' is-compact' : ''}`} aria-labelledby={compact ? 'reader-study-plan-title' : 'study-plan-title'}>
    <div className="study-plan-heading">
      <div className="study-plan-title"><span className="study-plan-mark" aria-hidden="true"><Leaf size={19} /></span><div><span className="overline">PERSONAL STUDY PLAN</span><h2 id={compact ? 'reader-study-plan-title' : 'study-plan-title'}>{compact ? '今日学习计划' : title}</h2><p>{description}</p></div></div>
      {onStart && <button className="button primary" disabled={disabled} onClick={onStart}>{startLabel}<ArrowRight size={16} /></button>}
    </div>
    <div className="study-plan-body">
      <div className="study-plan-progress-copy">
        <div><span>今日进度</span><strong>{plan.completed_cards}<small> / {plan.target_cards} 次复习</small></strong></div>
        <span className={`study-plan-status status-${plan.status.toLowerCase()}`}>{plan.status === 'COMPLETE' ? <CheckCircle2 size={14} /> : <Sparkles size={14} />}{plan.status === 'COMPLETE' ? '已完成' : plan.status === 'WAITING' ? '等待新任务' : plan.status === 'LIGHT' ? '轻量安排' : `还剩 ${plan.remaining_cards} 次`}</span>
      </div>
      {plan.target_cards > 0
        ? <div className="progress-track study-plan-progress" role="progressbar" aria-label="今日个性化学习计划进度" aria-valuemin={0} aria-valuemax={plan.target_cards} aria-valuenow={goalValue} aria-valuetext={`${plan.completed_cards} / ${plan.target_cards} 次复习`}><div style={{ width: `${plan.progress_percent}%` }} /></div>
        : <div className="progress-track study-plan-progress" aria-hidden="true"><div style={{ width: '0%' }} /></div>}
      <dl className="study-plan-facts">
        <div><dt><CalendarClock size={14} />现在可复习</dt><dd>{plan.available_now}<span> 个</span></dd></div>
        <div><dt><Clock3 size={14} />今天稍后</dt><dd>{plan.later_today}<span> 个</span></dd></div>
        <div><dt><Gauge size={14} />预计用时</dt><dd>{Math.ceil(plan.remaining_cards * plan.estimated_seconds_per_card / 60)}<span> 分钟</span></dd></div>
      </dl>
    </div>
    <fieldset className="study-plan-budget" disabled={disabled || lockMinutes}>
      <legend>每日学习预算</legend>
      <div role="group" aria-label="选择每日学习预算">{STUDY_PLAN_MINUTES.map(value => <button type="button" key={value} className={targetMinutes === value ? 'active' : ''} aria-pressed={targetMinutes === value} onClick={() => onMinutesChange(value)}>{value}<span>分钟</span></button>)}</div>
      <p>{lockMinutes ? '当前组完成后可以调整；本组仍使用开始时的预算。' : '预算决定最多安排多少次复习；任务不足时不会用尚未到期的词凑数。'}</p>
    </fieldset>
    {!compact && <details className="study-plan-explainer"><summary>词间为什么这样安排？</summary><p>先处理逾期、最近忘记和反复困难的词，再结合阅读中出现频率排序。明确自评会安排下次复习；近期有效作答用时会作为后续安排的参考。揭晓答案、查看释义和跳过不会改变排期。</p></details>}
    {storageWarning && <p className="study-plan-storage-warning" role="status">{storageWarning}</p>}
  </section>;
}
