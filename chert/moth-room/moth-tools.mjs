// Moth room-history tools for the FaceTime agent. They call the Vultr API (ActiasLuna/cloud/moth_cloud.py).
// Set MOTH_API_URL, MOTH_QUERY_TOKEN, MOTH_ROOM, MOTH_TZ in .env.local. The QUERY token can only READ history,
// and it is only ever sent to MOTH_API_URL.
import { llm } from '@livekit/agents';
import { z } from 'zod';

export const TZ = process.env.MOTH_TZ || 'America/New_York';

export async function askRoom(path, params = {}, env = process.env) {
  const api = (env.MOTH_API_URL || '').replace(/\/+$/, '');
  const token = env.MOTH_QUERY_TOKEN || '';
  if (!api || !token || /YOUR-|paste-/.test(api + token)) {
    return { answer: "Room history isn't connected yet, so I can't check the room." };
  }
  const qs = new URLSearchParams({ room: env.MOTH_ROOM || 'demo-room', tz: env.MOTH_TZ || TZ, ...params });
  try {
    const res = await fetch(`${api}${path}?${qs}`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(6000),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return { answer: `The room history service returned an error (${res.status}).`, error: data.error };
    return data;
  } catch {
    return { answer: "I couldn't reach the room history service just now." };
  }
}

export const tools = [
  llm.tool({
    name: 'room_status_now',
    description: 'Is anyone in the monitored room right now, how many, since when, and which board they are near.',
    execute: async () => askRoom('/api/now'),
  }),
  llm.tool({
    name: 'room_history_for_day',
    description: 'When people were in the room on one calendar day. Use for "today", "yesterday", "on Friday", etc.',
    parameters: z.object({
      date: z.string().describe("The day as YYYY-MM-DD in the room timezone. Work it out from today's date."),
    }),
    execute: async ({ date }) => askRoom('/api/summary', { date }),
  }),
  llm.tool({
    name: 'room_history_between',
    description: 'When people were in the room between two times, e.g. "between 2 and 5 PM" or "last night".',
    parameters: z.object({
      from: z.string().describe('Start, ISO local time like 2026-11-14T14:00'),
      to: z.string().describe('End, ISO local time like 2026-11-14T17:00'),
    }),
    execute: async ({ from, to }) => askRoom('/api/occupancy', { from, to }),
  }),
];

export function todayInTz(tz = TZ) {
  // en-CA formats dates as YYYY-MM-DD
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' })
    .format(new Date());
}

export function instructionsFor(today, tz = TZ) {
  return `You are Actias, an AI that watches over one room using Wi-Fi sensors, speaking over FaceTime.
If asked your name, you are Actias.
Today is ${today} (timezone ${tz}). Work out dates like "yesterday" from that.
When the caller asks whether anyone is or was in the room, ALWAYS call a tool first; never guess.
Speak the tool's "answer" naturally and briefly. Times are already in local time; say them as given.
Be honest about limits:
- You only know how many people were there and which board they were near, never WHO they were. If asked who, say so.
- If a tool says the sensors weren't reporting, say you weren't watching then. Never turn that into "nobody was there".
- If the history service is unreachable, say so plainly.
Keep every reply to one or two short sentences, and be clear that you are an AI.`;
}
