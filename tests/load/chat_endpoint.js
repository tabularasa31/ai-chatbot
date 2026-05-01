// k6 load test for the public /chat endpoint.
//
// Goal: validate the async/sync DB boundary in the chat pipeline under
// concurrent load. Watch Railway logs for `MissingGreenlet` and
// `NoActiveSqlalchemyContext` errors during/after the run — zero
// occurrences is the pass criterion.
//
// Usage:
//   BASE_URL=https://ai-chatbot-production-6531.up.railway.app \
//   API_KEY=<tenant-api-key> \
//   k6 run tests/load/chat_endpoint.js
//
// Optional env:
//   RPS         target arrivals/sec (default 15)
//   DURATION    test duration       (default 2m)
//   PREALLOC    preallocated VUs    (default 50)
//   MAX_VUS     max VUs             (default 200)
//
// Each iteration sends one chat turn with a random question from QUESTIONS
// and a unique session_id, simulating fresh concurrent sessions.

import http from 'k6/http';
import { check } from 'k6';
import { uuidv4 } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY;
const RPS = parseInt(__ENV.RPS || '15', 10);
const DURATION = __ENV.DURATION || '2m';
const PREALLOC = parseInt(__ENV.PREALLOC || '50', 10);
const MAX_VUS = parseInt(__ENV.MAX_VUS || '200', 10);

if (!API_KEY) {
  throw new Error('API_KEY env var is required');
}

const QUESTIONS = [
  'What are your business hours?',
  'How do I reset my password?',
  'Do you offer refunds?',
  'How can I contact support?',
  'What payment methods are accepted?',
  'Where are you located?',
  'How long does shipping take?',
  'Can I change my subscription plan?',
];

export const options = {
  scenarios: {
    chat_load: {
      executor: 'constant-arrival-rate',
      rate: RPS,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: PREALLOC,
      maxVUs: MAX_VUS,
    },
  },
  thresholds: {
    // Server-side errors must stay near zero.
    http_req_failed: ['rate<0.02'],
    // p95 latency budget — adjust per environment.
    http_req_duration: ['p(95)<8000'],
  },
};

export default function () {
  const url = `${BASE_URL}/chat`;
  const payload = JSON.stringify({
    question: QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)],
    session_id: uuidv4(),
  });
  const params = {
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': API_KEY,
    },
    timeout: '30s',
  };

  const res = http.post(url, payload, params);
  check(res, {
    'status is 200': (r) => r.status === 200,
    'has text': (r) => {
      try {
        return typeof r.json('text') === 'string';
      } catch (_) {
        return false;
      }
    },
  });
}
