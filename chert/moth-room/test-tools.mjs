// Calls each Moth tool the way the voice model would, no call needed. Run: node --env-file=.env.local test-tools.mjs
import { askRoom, instructionsFor, todayInTz, tools } from './moth-tools.mjs';

const byName = Object.fromEntries(tools.map(t => [t.name, t]));
const today = todayInTz();
const out = {
  today,
  recent: (await byName.recent_activity.execute({ minutes: 10 }, {})).answer,
  where: (await byName.where_now.execute({}, {})).answer,
  now: (await byName.room_status_now.execute({}, {})).answer,
  day: (await byName.room_history_for_day.execute({ date: today }, {})).answer,
  range: (await byName.room_history_between.execute({ from: `${today}T00:00`, to: `${today}T23:59` }, {})).answer,
  not_connected: (await askRoom('/api/now', {}, {})).answer,
  instructions_ok: instructionsFor(today).includes(today),
};
console.log(JSON.stringify(out, null, 2));
