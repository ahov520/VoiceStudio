import { apiJson, apiPost } from './client';

export interface DramaCastMember {
  name: string;
  aliases: string[];
  description: string;
  voice?: { profile_id?: string; recipe_instruct?: string };
  candidates?: Array<{ id: string; name: string; kind: string }>;
}

export interface DramaLine {
  speaker: string;
  text: string;
  emotion: string;
  intensity: number;
  stage: string;
}

export interface DramaParseResult {
  cast: DramaCastMember[];
  lines: DramaLine[];
  script_text: string;
  voice_map: Record<string, string>;
}

export interface DramaProjectSummary {
  id: string;
  name: string;
  line_count: number;
}

export interface DramaProject extends DramaProjectSummary {
  script: string;
  cast: DramaCastMember[];
  lines: DramaLine[];
}

export function parseDrama(
  script: string,
  profiles: Array<Record<string, unknown>> = [],
  opts: { signal?: AbortSignal } = {},
): Promise<DramaParseResult> {
  return apiJson('/drama/parse', {
    method: 'POST',
    body: JSON.stringify({ script, profiles }),
    signal: opts.signal,
  });
}

export function saveDramaProject(payload: {
  name: string;
  script: string;
  cast: DramaCastMember[];
  lines: DramaLine[];
}): Promise<{ id: string; name: string }> {
  return apiPost('/drama/projects', payload);
}

export function listDramaProjects(): Promise<{ projects: DramaProjectSummary[] }> {
  return apiJson('/drama/projects');
}

export function getDramaProject(id: string): Promise<DramaProject> {
  return apiJson(`/drama/projects/${encodeURIComponent(id)}`);
}

export function deleteDramaProject(id: string): Promise<{ ok: boolean }> {
  return apiJson(`/drama/projects/${encodeURIComponent(id)}`, { method: 'DELETE' });
}
