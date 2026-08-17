import { apiJson, apiPost } from './client';

export interface GalleryVoice {
  id: string;
  name: string;
  character: string;
  category: string;
  source_type: string;
  source_url?: string;
  audio_path: string;
  duration: number;
  description?: string;
  thumbnail?: string;
  tags: string[];
  is_favorite?: boolean;
  created_at: number;
}

export const listGalleryVoices = (params?: {
  category?: string;
  search?: string;
  limit?: number;
}): Promise<GalleryVoice[]> => {
  const query = params
    ? (() => {
        const defined = Object.entries(params).filter(([, value]) => value != null);
        const encoded = new URLSearchParams(
          defined as [string, string][],
        ).toString();
        return encoded ? `?${encoded}` : '';
      })()
    : '';
  return apiJson(`/gallery/voices${query}`);
};

export const deleteGalleryVoice = (voiceId: string): Promise<{ success: boolean }> =>
  apiJson(`/gallery/voices/${encodeURIComponent(voiceId)}`, { method: 'DELETE' });

export interface YoutubeSearchResult {
  title: string;
  video_id: string;
  duration: string | null;
  thumbnail: string | null;
}

export const searchYoutube = async (
  query: string,
  category: string,
  maxResults: number = 5,
): Promise<{ results: YoutubeSearchResult[]; query: string; category: string }> => {
  const url = `/gallery/search/youtube?query=${encodeURIComponent(query)}&category=${encodeURIComponent(category)}&max_results=${maxResults}`;
  return apiJson(url, { method: 'POST' });
};

export interface DownloadParams {
  video_url: string;
  start_time: number;
  duration: number;
  character_name: string;
  category: string;
  description?: string;
}

export const downloadYoutubeClip = async (
  params: DownloadParams,
): Promise<{ success: boolean; voice_id: string }> => {
  const url = `/gallery/download?video_url=${encodeURIComponent(params.video_url)}&start_time=${params.start_time}&duration=${params.duration}&character_name=${encodeURIComponent(params.character_name)}&category=${encodeURIComponent(params.category)}&description=${encodeURIComponent(params.description || '')}`;
  return apiJson(url, { method: 'POST' });
};

export const uploadVoiceClip = async (formData: FormData): Promise<{ id: string; name: string }> =>
  apiPost('/gallery/upload', formData);

export const saveVoiceAsProfile = async (
  voiceId: string,
  profileName: string,
): Promise<{ profile_id: string; name: string }> => {
  const url = `/gallery/voices/${encodeURIComponent(voiceId)}/save-as-profile?profile_name=${encodeURIComponent(profileName)}`;
  return apiJson(url, { method: 'POST' });
};

export const previewVoiceUrl = (voiceId: string): string =>
  `/gallery/voices/${encodeURIComponent(voiceId)}/preview`;
