// Calls each Moth tool the way the voice model would. Usage: node test-tools.mjs (env: MOTH_* set by the caller)
import { askRoom, instructionsFor, todayInTz, tools } from './moth-tools.mjs';

const byName = Object.fromEntries(tools.map(t => [t.name, t]));
const today = todayInTz();
const out = {
  today,
  now: (await byName.room_status_now.execute({}, {})).answer,
  day: (await byName.room_history_for_day.execute({ date: today }, {})).answer,
  range: await byName.room_history_between.execute({ from: `${today}T00:00`, to: `${today}T23:59` }, {}),
  not_connected: (await askRoom('/api/now', {}, {})).answer,
  instructions_ok: instructionsFor(today).includes(today),
};
console.log(JSON.stringify(out, null, 2));
