export type WordStatus = 'KNOWN' | 'UNKNOWN' | 'UNCERTAIN';
export type View = 'reader' | 'vocabulary' | 'history';
export interface Occurrence {
  id: number; word: string; lemma: string; sentence: string;
  bbox: [number, number, number, number]; line: string | number;
  confidence: number; bbox_source: string; status: WordStatus | null;
}
export interface ReadingDocument {
  id: number; title: string; image_url: string; width: number; height: number;
  ocr_text: string; source: string; words: Occurrence[];
}
export interface DocumentSummary {
  id: number; title: string; image_url: string; width: number; height: number;
  created_at: string; word_count: number; source: string;
}
export interface Vocabulary {
  id: number; user_id: number; word: string; lemma: string; status: WordStatus | null;
  mastery_score: number | null; seen_count: number; lookup_count: number;
  created_at: string; updated_at: string; first_seen_at: string | null; last_seen_at: string | null;
}
export interface LearningEvent {
  id: number; user_id: number; word: string; lemma: string; document_id: number | null;
  event_type: string; details: Record<string, unknown>; created_at: string;
}
export interface WordHistory {
  vocabulary: Vocabulary; last_mark: LearningEvent | null; events: LearningEvent[];
}
export interface Explanation {
  word: string; lemma: string; meaning_in_context: string; simple_explanation: string;
  part_of_speech: string; source: 'llm' | 'offline'; warning?: string;
}
export interface Recommendation {
  items: { word: string; meaning: string; reason: string; score: number }[];
  source: 'llm' | 'offline'; warning?: string;
}
export interface Health { status: string; ocr_provider: string; llm_provider: string }
