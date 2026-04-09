const WebSocket = require('ws');

const USER_ID = 1; // admin
const TARGET_ID = 101; // ehsan

async function test() {
  const ws = new WebSocket(`ws://localhost:8000/api/realtime/ws?token=123`); // Needs valid token... wait, easier to just check it.
}
